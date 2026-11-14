"""Tests for mem.py. Run with: python3 -m unittest discover -s memory/tests"""
import io
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "store"))
import mem  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"CLAUDYSHA_HOME": str(Path(self.tmp.name) / "home"),
                                             "CLAUDYSHA_CONFIG": str(Path(self.tmp.name) / "none.conf")})
        env.start()
        self.addCleanup(env.stop)
        patcher = mock.patch.multiple(mem, DB_PATH=Path(self.tmp.name) / "memory.db",
                                      LOG_PATH=Path(self.tmp.name) / "extract.log")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = mem.connect()
        self.addCleanup(self.db.close)

    def add(self, text, type_="fact", scope="homelab", **kw):
        return mem.insert(self.db, text, type_, scope, **kw)

    def ids(self, rows):
        return [r["id"] for r in rows]


class TestSearch(StoreTest):
    def test_fts_query_quotes_terms_and_drops_punctuation(self):
        self.assertEqual(mem.fts_query('port 127.0.0.1:8080 - say "hi"'),
                         '"port" OR "127.0.0.1:8080" OR "say" OR """hi"""')
        self.assertEqual(mem.fts_query("  - ! "), "")

    def test_exact_tokens_match(self):
        a = self.add("the landing page listens on 127.0.0.1:8080")
        self.add("the birthday page listens on 127.0.0.1:8081")
        self.assertEqual(self.ids(mem.search(self.db, "127.0.0.1:8080")), [a])

    def test_superseded_hidden_unless_all(self):
        old = self.add("pnpm pinned to 10.15.1")
        self.db.execute("UPDATE memories SET status='superseded' WHERE id=?", (old,))
        self.assertEqual(mem.search(self.db, "pnpm"), [])
        self.assertEqual(self.ids(mem.search(self.db, "pnpm", include_old=True)), [old])

    def test_filters(self):
        a = self.add("the media server uses the iGPU", scope="homelab")
        b = self.add("media client on the iGPU laptop", scope="laptop", type_="todo")
        g = self.add("media goes on the big disks", scope="global")
        self.assertEqual(self.ids(mem.search(self.db, "media", scopes=["homelab"])), [a])
        self.assertEqual(set(self.ids(mem.search(self.db, "media", scopes=["laptop", "global"]))), {b, g})
        self.assertEqual(len(mem.search(self.db, "media")), 3)
        self.assertEqual(len(mem.search(self.db, "media", type_="todo")), 1)

    def test_fts_follows_edits_and_deletes(self):
        a = self.add("old wording about grub")
        self.db.execute("UPDATE memories SET text='new wording about bootloader' WHERE id=?", (a,))
        self.assertEqual(mem.search(self.db, "grub"), [])
        self.assertEqual(self.ids(mem.search(self.db, "bootloader")), [a])
        self.db.execute("DELETE FROM memories WHERE id=?", (a,))
        self.assertEqual(mem.search(self.db, "bootloader"), [])

    def test_mark_used(self):
        a = self.add("fail2ban bans go into DOCKER-USER")
        mem.mark_used(self.db, mem.search(self.db, "fail2ban"))
        r = mem.get(self.db, a)
        self.assertEqual(r["use_count"], 1)
        self.assertIsNotNone(r["last_used_at"])


class TestWriting(StoreTest):
    def test_similar_existing_catches_near_duplicates_only(self):
        self.add("The server boots in legacy BIOS mode with an EF02 partition.")
        self.assertIsNotNone(mem.similar_existing(
            self.db, "The server boots in legacy BIOS mode, with an EF02 partition", "homelab"))
        self.assertIsNone(mem.similar_existing(
            self.db, "The server has two HDDs mounted with nofail", "homelab"))

    def test_unknown_type_rejected(self):
        with self.assertRaises(SystemExit):
            self.add("x", type_="opinion")


class TestCleanup(StoreTest):
    """merge, split and similar: the commands behind the memory-cleanup skill."""

    def run_cmd(self, fn, **kw):
        with redirect_stdout(io.StringIO()) as out:
            fn(self.db, mock.Mock(**kw))
        return out.getvalue()

    def test_merge_replaces_every_entry_with_one(self):
        a = self.add("The media server runs Jellyfin", tags="media")
        b = self.add("Jellyfin is the media server, on port 8096", tags="jellyfin", confidence=0.95)
        out = self.run_cmd(mem.cmd_merge, ids=[a, b], text="The media server runs Jellyfin on port 8096",
                           type=None, scope=None)
        new = self.db.execute("SELECT * FROM memories WHERE status = 'active'").fetchall()
        self.assertEqual(len(new), 1)
        self.assertIn(f"into #{new[0]['id']}", out)
        self.assertEqual((new[0]["tags"], new[0]["confidence"], new[0]["scope"]), ("media jellyfin", 0.95, "homelab"))
        self.assertEqual(json.loads(new[0]["meta"]), {"merged_from": [a, b]})
        for old in (a, b):
            self.assertEqual(mem.get(self.db, old)["superseded_by"], new[0]["id"])

    def test_merge_keeps_proposed_entries_waiting(self):
        a = self.add("Keep answers short", type_="feedback", status="active")
        b = self.add("Answers should be short and plain", type_="feedback", status="proposed")
        self.run_cmd(mem.cmd_merge, ids=[a, b], text="Keep answers short and plain", type=None, scope=None)
        self.assertEqual(self.db.execute("SELECT status FROM memories WHERE superseded_by IS NULL").fetchone()[0],
                         "proposed")

    def test_merge_and_split_refuse_old_entries(self):
        a, b = self.add("one"), self.add("two")
        self.db.execute("UPDATE memories SET status = 'superseded' WHERE id = ?", (b,))
        with self.assertRaises(SystemExit):
            self.run_cmd(mem.cmd_merge, ids=[a, b], text="x", type=None, scope=None)
        with self.assertRaises(SystemExit):
            self.run_cmd(mem.cmd_merge, ids=[a, a], text="x", type=None, scope=None)
        with self.assertRaises(SystemExit):
            self.run_cmd(mem.cmd_split, id=b, texts=["x", "y"], type=None)
        with self.assertRaises(SystemExit):
            self.run_cmd(mem.cmd_split, id=a, texts=["only one"], type=None)

    def test_split_makes_one_entry_per_thing(self):
        a = self.add("The NAS has two disks, and the backups run nightly at 03:00", tags="nas")
        self.run_cmd(mem.cmd_split, id=a, texts=["The NAS has two disks", "The backups run nightly at 03:00"],
                     type=None)
        old = mem.get(self.db, a)
        self.assertEqual(old["status"], "superseded")
        new = json.loads(old["meta"])["split_into"]
        self.assertEqual(old["superseded_by"], new[0])
        self.assertEqual([mem.get(self.db, i)["text"] for i in new],
                         ["The NAS has two disks", "The backups run nightly at 03:00"])
        self.assertEqual({mem.get(self.db, i)["tags"] for i in new}, {"nas"})

    def test_similar_groups_overlaps_but_not_chains(self):
        a = self.add("The media server runs Jellyfin on port 8096")
        b = self.add("Jellyfin on port 8096 is the media server; its library is on the second disk")
        c = self.add("Jellyfin is the media server", scope="global")
        self.add("The second disk holds the backups, rotated weekly")
        self.add("The router hands out addresses in the 10.0.0.0/24 range")
        self.add("The media server runs Jellyfin on port 8096", scope="laptop")  # another scope
        groups = mem.similar_groups(self.db, "homelab")
        self.assertEqual([[r["id"] for r in rows] for _, rows in groups], [[a, b, c]])
        out = self.run_cmd(mem.cmd_similar, scope="homelab", limit=30, min_score=0.3)
        self.assertIn(f"#{c} [fact/global", out)
        self.assertEqual(self.db.execute("SELECT SUM(use_count) FROM memories").fetchone()[0], 0)
        self.assertIn(f"#{c} [fact/global", self.run_cmd(mem.cmd_similar, scope="laptop", limit=30, min_score=0.3))
        self.add("The desk lamp is on a smart plug", scope="office")
        self.assertIn("nothing alike", self.run_cmd(mem.cmd_similar, scope="office", limit=30, min_score=0.3))


class TranscriptTest(StoreTest):
    def write_transcript(self, lines):
        path = Path(self.tmp.name) / "s.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return path


class TestCondense(TranscriptTest):
    def test_keeps_dialogue_and_tool_hints_only(self):
        path = self.write_transcript([
            {"type": "mode"},
            {"type": "user", "timestamp": "2026-11-13T10:00:00Z", "origin": {"kind": "human"},
             "message": {"content": "hello"}},
            {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": "task done"}},
            {"type": "user", "isMeta": True, "message": {"content": "meta stuff"}},
            {"type": "user", "message": {"content": "<command-name>/compact</command-name>"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "hi there"},
                {"type": "tool_use", "name": "Bash", "input": {"description": "List files"}},
                {"type": "thinking", "thinking": "secret thoughts"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "huge output"}]}},
            {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "sub"}]}},
            "not a dict but valid json",
        ])
        text, last, date, humans = mem.condense(path, 0)
        self.assertEqual(text, "the user: hello\n\n[note, not from the user]: task done\n\n"
                               "Claude: hi there\n\n[Claude used Bash: List files]")
        self.assertEqual((last, humans), (9, 1))
        self.assertEqual(date, mem.local_time("2026-11-13T10:00:00Z"))

    def test_local_time(self):
        self.assertIsNone(mem.local_time(None))
        self.assertIsNone(mem.local_time("garbage"))
        self.assertRegex(mem.local_time("2026-11-13T10:00:00.123Z"), r"^2026-11-1[34]T\d\d:00:00$")

    def test_starts_after_given_line(self):
        path = self.write_transcript([
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "first"}},
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "second"}},
        ])
        self.assertEqual(mem.condense(path, 1)[0], "the user: second")

    def test_chunks_split_on_paragraphs(self):
        text = "\n\n".join(["a" * 40] * 10)
        parts = list(mem.chunks(text, size=100))
        self.assertTrue(all(len(p) <= 100 for p in parts))
        self.assertEqual("".join(parts).replace("\n", ""), text.replace("\n", ""))


class TestIngest(TranscriptTest):
    """ingest() with the model calls replaced by canned answers."""

    def fake_claude(self, extract_items, resolve_decisions=()):
        def run(system, prompt, schema):
            if "items" in schema["properties"]:
                self.extract_schemas.append(schema)
                return {"items": extract_items}, 0.001
            self.resolve_prompts.append(prompt)
            return {"decisions": list(resolve_decisions)}, 0.001
        self.resolve_prompts, self.extract_schemas = [], []
        return mock.patch.object(mem, "run_claude", side_effect=run)

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(mem, "session_scope", return_value="homelab")
        patcher.start()
        self.addCleanup(patcher.stop)

    def transcript(self, n=2):
        return self.write_transcript(
            [{"type": "user", "timestamp": "2026-11-13T09:00:00Z", "origin": {"kind": "human"},
              "message": {"content": f"msg {i}"}} for i in range(n)])

    def test_feedback_is_proposed_facts_are_active(self):
        items = [
            {"text": "The media server runs on the homelab box", "type": "fact", "scope": "homelab", "confidence": 0.9},
            {"text": "Keep answers short", "type": "feedback", "scope": "global", "confidence": 0.8},
            {"text": "", "type": "fact", "scope": "x", "confidence": 1},
            {"text": "bad type", "type": "opinion", "scope": "x", "confidence": 1},
        ]
        with self.fake_claude(items):
            added, skipped, sup, cost = mem.ingest(self.db, self.transcript(), "s1")
        self.assertEqual((added, skipped, sup), (2, 0, 0))
        statuses = {r["type"]: r["status"] for r in self.db.execute("SELECT * FROM memories")}
        self.assertEqual(statuses, {"fact": "active", "feedback": "proposed"})
        r = self.db.execute("SELECT * FROM memories WHERE type='fact'").fetchone()
        self.assertEqual((r["source"], r["session_id"], r["scope"]), ("extract", "s1", "homelab"))
        self.assertEqual(r["created_at"], mem.local_time("2026-11-13T09:00:00Z"))
        self.assertEqual(self.db.execute("SELECT scope FROM memories WHERE type='feedback'").fetchone()[0],
                         "global")
        self.assertEqual(self.extract_schemas[0]["properties"]["items"]["items"]["properties"]["scope"]["enum"],
                         ["homelab", "global"])

    def test_scope_is_forced_to_the_session_or_global(self):
        item = [{"text": "A fact filed elsewhere", "type": "fact", "scope": "made-up", "confidence": 0.9}]
        with self.fake_claude(item):
            mem.ingest(self.db, self.transcript(), "s1")
        self.assertEqual(self.db.execute("SELECT scope FROM memories").fetchone()[0], "homelab")

    def test_resolve_only_compares_with_this_scope_and_global(self):
        other = self.add("the backup job runs at night", scope="laptop")
        item = [{"text": "the backup job runs at night on the server", "type": "fact", "scope": "homelab",
                 "confidence": 0.9}]
        with self.fake_claude(item):
            self.assertEqual(mem.ingest(self.db, self.transcript(), "s1")[0], 1)
        self.assertEqual(mem.get(self.db, other)["status"], "active")
        self.assertEqual(self.resolve_prompts, [])

    def test_incremental(self):
        path = self.transcript(2)
        item = [{"text": "Something new", "type": "fact", "scope": "homelab", "confidence": 0.9}]
        with self.fake_claude(item) as run:
            mem.ingest(self.db, path, "s1")
            self.assertEqual(mem.ingest(self.db, path, "s1"), (0, 0, 0, 0.0))
            self.assertEqual(run.call_count, 1)  # nothing new to read, no model call
        lines_done = self.db.execute("SELECT lines_done FROM ingested").fetchone()[0]
        self.assertEqual(lines_done, 2)

    def test_resolve_supersedes_and_dedupes(self):
        old = self.add("old-stacks is the repo for compose stacks")
        same = self.add("The media server reads from /mnt/hdd1 and /mnt/hdd2")
        items = [
            {"text": "the infra monorepo replaced the old-stacks repo",
             "type": "fact", "scope": "homelab", "confidence": 0.9},
            {"text": "Media for the media server is on /mnt/hdd1 and /mnt/hdd2",
             "type": "fact", "scope": "homelab", "confidence": 0.9},
        ]
        decisions = [{"new": 0, "duplicate_of": None, "supersedes": [old, 9999]},
                     {"new": 1, "duplicate_of": same, "supersedes": []}]
        with self.fake_claude(items, decisions):
            added, skipped, sup, _ = mem.ingest(self.db, self.transcript(), "s1")
        self.assertEqual((added, skipped, sup), (1, 1, 1))  # unknown id 9999 ignored
        r = mem.get(self.db, old)
        self.assertEqual(r["status"], "superseded")
        self.assertEqual(mem.get(self.db, r["superseded_by"])["text"], items[0]["text"])
        self.assertEqual(mem.get(self.db, same)["use_count"], 1)
        self.assertIn(f"#{old}", self.resolve_prompts[0])

    def test_resolve_keeps_only_what_a_new_item_adds(self):
        stored = self.add("The media server runs Jellyfin")
        items = [{"text": "The media server runs Jellyfin, and the router uses the 10.0.0.0/24 range",
                  "type": "fact", "scope": "homelab", "confidence": 0.9}]
        decisions = [{"new": 0, "duplicate_of": None, "supersedes": [],
                      "keep_only": "The router uses the 10.0.0.0/24 range"}]
        with self.fake_claude(items, decisions):
            self.assertEqual(mem.ingest(self.db, self.transcript(), "s1")[:3], (1, 0, 0))
        texts = [r["text"] for r in self.db.execute("SELECT text FROM memories ORDER BY id")]
        self.assertEqual(texts, ["The media server runs Jellyfin", "The router uses the 10.0.0.0/24 range"])
        self.assertEqual(mem.get(self.db, stored)["status"], "active")
        self.assertIn("keep_only", mem.RESOLVE_SCHEMA["properties"]["decisions"]["items"]["required"])

    def test_sessions_without_human_messages_are_skipped(self):
        path = self.write_transcript([
            {"type": "user", "entrypoint": "sdk-cli", "message": {"content": "scripted prompt"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "answer"}]}},
        ])
        with self.fake_claude([{"text": "x", "type": "fact", "scope": "p", "confidence": 1}]) as run:
            self.assertEqual(mem.ingest(self.db, path, "s1"), (0, 0, 0, 0.0))
        run.assert_not_called()
        self.assertEqual(self.db.execute("SELECT lines_done FROM ingested").fetchone()[0], 2)

    def test_dry_run_stores_nothing(self):
        item = [{"text": "A fact", "type": "fact", "scope": "homelab", "confidence": 0.9}]
        with self.fake_claude(item), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mem.ingest(self.db, self.transcript(), "s1", dry_run=True)[0], 1)
        self.assertIn("A fact", out.getvalue())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertIsNone(self.db.execute("SELECT * FROM ingested").fetchone())

    def run_ingest_hook(self, path):
        args = mock.Mock(hook=True)
        stdin = io.StringIO(json.dumps({"transcript_path": str(path),
                                        "session_id": "s1", "hook_event_name": "SessionEnd"}))
        with mock.patch.object(sys, "stdin", stdin), mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_ingest(self.db, args)

    def test_hook_mode_logs_errors_instead_of_raising(self):
        with mock.patch.object(mem, "ingest", side_effect=RuntimeError("boom")):
            self.run_ingest_hook(self.transcript())
        self.assertIn("ERROR s1: boom", mem.LOG_PATH.read_text())

    def test_hook_mode_skips_missing_transcript_quietly(self):
        self.run_ingest_hook("/nonexistent.jsonl")
        self.assertFalse(mem.LOG_PATH.exists() and "s1" in mem.LOG_PATH.read_text())

    def session_start(self, cwd):
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": str(cwd)}))), \
                redirect_stdout(io.StringIO()) as out, mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_session_start(self.db, None)
        return out.getvalue()

    def test_session_start_note(self):
        here = Path(self.tmp.name).resolve()
        self.add("a fact")
        out = self.session_start(here)
        self.assertIn("you remember 1 things", out)
        self.assertIn(f"scope is `{here}`", out)
        self.assertNotIn("proposed", out)
        self.assertIn("Memory commands", out)
        self.assertIn("mem supersede <id>", out)
        self.assertIn(str(mem.claudysha.REPO / "mem"), out)
        self.assertNotIn(str(mem.claudysha.data_dir()), out)  # commands only, never where memory is kept
        self.add("be brief", "feedback", "global", status="proposed")
        self.assertIn("1 proposed", self.session_start(here))

    def test_session_start_loads_global_and_own_rules_and_lists_other_scopes(self):
        here = str(Path(self.tmp.name).resolve())
        self.add("Answer in short paragraphs", "feedback", "global")
        self.add("Run the tests before committing here", "preference", here)
        self.add("Deploy with the blue-green script", "preference", "webshop")
        self.add("The webshop runs on port 8080", "fact", "webshop")
        self.add("Not approved yet", "feedback", here, status="proposed")
        self.add("A fact of this scope", "fact", here)
        out = self.session_start(here)
        self.assertIn("- Answer in short paragraphs", out)
        self.assertIn("- Run the tests before committing here", out)
        self.assertNotIn("blue-green", out)          # another scope's rule is never loaded
        self.assertNotIn("Not approved yet", out)
        self.assertNotIn("A fact of this scope", out)  # facts are searched, not loaded
        self.assertIn("`webshop` (2)", out)
        self.assertIn("never as a rule", out)

    def test_session_start_caps_the_rules(self):
        here = str(Path(self.tmp.name).resolve())
        for i in range(mem.RULES_SHOWN + 3):
            self.add(f"rule number {i}", "feedback", here)
        out = self.session_start(here)
        self.assertEqual(out.count("- rule number"), mem.RULES_SHOWN)
        self.assertIn("3 more", out)

    def test_search_defaults_to_this_scope_and_global_and_marks_others(self):
        here = str(Path(self.tmp.name).resolve())
        self.add("nginx config lives in the repo", scope=here)
        self.add("nginx is preferred over apache", "preference", "global")
        self.add("nginx on the webshop box", scope="webshop")

        def run(*argv):
            a = mock.Mock(query=["nginx"], scope=None, all_scopes=False, type=None, limit=10, all=False, full=False)
            for k, v in argv:
                setattr(a, k, v)
            with mock.patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": here}), \
                    redirect_stdout(io.StringIO()) as out:
                mem.cmd_search(self.db, a)
            return out.getvalue()
        out = run()
        self.assertIn("config lives", out)
        self.assertIn("preferred over apache", out)
        self.assertNotIn("webshop", out)
        out = run(("all_scopes", True))
        self.assertIn("nginx/webshop", out.replace("fact/webshop", "nginx/webshop"))
        self.assertIn("(other scope)", out)
        self.assertIn("not a rule", out)
        out = run(("scope", ["webshop"]))
        self.assertEqual(out.count("#"), 1)

    def test_old_store_is_migrated(self):
        old = Path(self.tmp.name) / "old.db"
        db = sqlite3.connect(old)
        db.executescript(mem.SCHEMA.replace("scope", "project").replace("'global'", "'general'"))
        db.execute("INSERT INTO memories (text, type, project) VALUES ('be brief', 'feedback', 'general')")
        db.execute("INSERT INTO memories (text, type, project) VALUES ('grub on sda', 'fact', 'homelab')")
        db.commit()
        db.close()
        with mock.patch.object(mem, "DB_PATH", old):
            db = mem.connect()
        self.addCleanup(db.close)
        self.assertEqual([tuple(r) for r in db.execute("SELECT text, scope FROM memories ORDER BY id")],
                         [("be brief", "global"), ("grub on sda", "homelab")])
        self.assertEqual(len(mem.search(db, "grub")), 1)
        db.execute("UPDATE memories SET text = 'grub on sdb' WHERE id = 2")
        self.assertEqual(len(mem.search(db, "sdb")), 1)  # triggers recreated



class TestScopes(TranscriptTest):
    def test_session_scope_comes_from_the_transcript_cwd(self):
        folder = Path(self.tmp.name).resolve() / "work"
        folder.mkdir()
        path = self.write_transcript([{"type": "mode"}, {"type": "user", "cwd": str(folder),
                                                          "message": {"content": "hi"}}])
        self.assertEqual(mem.session_scope(path), str(folder))
        bare = self.write_transcript([{"type": "user", "message": {"content": "hi"}}])
        self.assertEqual(mem.session_scope(bare), bare.parent.name)

    def test_session_folder_is_the_one_claude_code_files_the_transcript_under(self):
        root = Path(self.tmp.name).resolve()
        project = root / "my.project"
        (root / "elsewhere").mkdir()
        project.mkdir()
        folder = root / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project))
        folder.mkdir(parents=True)
        path = folder / "s.jsonl"
        lines = [{"cwd": str(root / "elsewhere")}] * 5 + [{"cwd": str(project)}]  # resumed from my.project
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        self.assertEqual(mem.session_cwd(path), str(project))
        other = root / "projects" / "unrelated"
        other.mkdir()
        (other / "s.jsonl").write_text(path.read_text())
        self.assertEqual(mem.session_cwd(other / "s.jsonl"), str(root / "elsewhere"))  # most frequent

    def test_rescope_moves_memories_to_their_session_scope(self):
        folder = Path(self.tmp.name).resolve() / "work"
        folder.mkdir()
        path = self.write_transcript([{"type": "user", "cwd": str(folder), "message": {"content": "hi"}}])
        mem.mark_ingested(self.db, "s1", str(path), 1)
        moved = self.add("a fact", scope="homelab", session_id="s1")
        kept = self.add("be brief", "feedback", "global", session_id="s1")
        orphan = self.add("added by hand", scope="homelab")
        with redirect_stdout(io.StringIO()) as out:
            mem.cmd_rescope(self.db, mock.Mock(apply=False))
        self.assertIn(f"homelab -> {folder}: 1", out.getvalue())
        self.assertIn("added by hand", out.getvalue())
        self.assertEqual(mem.get(self.db, moved)["scope"], "homelab")  # dry run
        with redirect_stdout(io.StringIO()):
            mem.cmd_rescope(self.db, mock.Mock(apply=True))
        self.assertEqual([mem.get(self.db, i)["scope"] for i in (moved, kept, orphan)],
                         [str(folder), "global", "homelab"])

    def test_prompts_use_the_configured_user(self):
        conf = Path(self.tmp.name) / "user.conf"
        conf.write_text("user_name = Alex\nuser_pronouns = he/him\n")
        with mock.patch.dict("os.environ", {"CLAUDYSHA_CONFIG": str(conf)}):
            text = mem.prompt_text(mem.PROMPT_PATH)
            self.assertIn("Alex", text)
            self.assertIn("he/him", text)
            self.assertNotIn("{{", text)
            path = self.write_transcript([{"type": "user", "origin": {"kind": "human"},
                                           "message": {"content": "hi"}}])
            self.assertEqual(mem.condense(path, 0)[0], "Alex: hi")

    def stop_payload(self, path, session="s1"):
        return json.dumps({"hook_event_name": "Stop", "transcript_path": str(path), "session_id": session})

    def test_stop_hook_waits_for_enough_new_lines(self):
        path = self.write_transcript([{"type": "user", "message": {"content": f"line {i}"}} for i in range(5)])
        conf = Path(self.tmp.name) / "every.conf"
        conf.write_text("memory_every_lines = 3\n")
        with mock.patch.dict("os.environ", {"CLAUDYSHA_CONFIG": str(conf)}):
            self.assertTrue(mem.hook_due(self.db, self.stop_payload(path)))
            mem.mark_ingested(self.db, "s1", str(path), 3)
            self.assertFalse(mem.hook_due(self.db, self.stop_payload(path)))  # 2 new lines
            conf.write_text("memory_every_lines = 0\n")
            mem.mark_ingested(self.db, "s1", str(path), 0)
            self.assertFalse(mem.hook_due(self.db, self.stop_payload(path)))  # turned off
        self.assertFalse(mem.hook_due(self.db, self.stop_payload("/nonexistent.jsonl")))
        self.assertFalse(mem.hook_due(self.db, "not json"))

    def test_end_and_compact_hooks_always_extract(self):
        for event in ("SessionEnd", "PreCompact"):
            self.assertTrue(mem.hook_due(self.db, json.dumps({"hook_event_name": event})))

    def test_stop_hook_spawns_nothing_when_not_due(self):
        path = self.write_transcript([{"type": "user", "message": {"content": "hi"}}])
        with mock.patch.object(sys, "stdin", io.StringIO(self.stop_payload(path))), \
                mock.patch.object(mem.subprocess, "Popen") as popen, \
                mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_hook(self.db, None)
        popen.assert_not_called()

    def test_hook_guard(self):
        args = mock.Mock(hook=True)
        with mock.patch.dict("os.environ", {"MEM_NO_HOOK": "1"}), \
                mock.patch.object(mem, "ingest") as ingest:
            mem.cmd_ingest(self.db, args)
            mem.cmd_hook(self.db, args)
        ingest.assert_not_called()


if __name__ == "__main__":
    unittest.main()

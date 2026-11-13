"""Tests for mem.py. Run with: python3 -m unittest discover -s memory/tests"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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

    def add(self, text, type_="fact", project="homelab", **kw):
        return mem.insert(self.db, text, type_, project, **kw)

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
        a = self.add("the media server uses the iGPU", project="homelab")
        self.add("media client on the iGPU laptop", project="laptop", type_="todo")
        self.assertEqual(self.ids(mem.search(self.db, "media", project="homelab")), [a])
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
            if schema is mem.EXTRACT_SCHEMA:
                return {"items": extract_items}, 0.001
            self.resolve_prompts.append(prompt)
            return {"decisions": list(resolve_decisions)}, 0.001
        self.resolve_prompts = []
        return mock.patch.object(mem, "run_claude", side_effect=run)

    def transcript(self, n=2):
        return self.write_transcript(
            [{"type": "user", "timestamp": "2026-11-13T09:00:00Z", "origin": {"kind": "human"},
              "message": {"content": f"msg {i}"}} for i in range(n)])

    def test_feedback_is_proposed_facts_are_active(self):
        items = [
            {"text": "The media server runs on the homelab box", "type": "fact", "project": "homelab", "confidence": 0.9},
            {"text": "Keep answers short", "type": "feedback", "project": "general", "confidence": 0.8},
            {"text": "", "type": "fact", "project": "x", "confidence": 1},
            {"text": "bad type", "type": "opinion", "project": "x", "confidence": 1},
        ]
        with self.fake_claude(items):
            added, skipped, sup, cost = mem.ingest(self.db, self.transcript(), "s1")
        self.assertEqual((added, skipped, sup), (2, 0, 0))
        statuses = {r["type"]: r["status"] for r in self.db.execute("SELECT * FROM memories")}
        self.assertEqual(statuses, {"fact": "active", "feedback": "proposed"})
        r = self.db.execute("SELECT * FROM memories WHERE type='fact'").fetchone()
        self.assertEqual((r["source"], r["session_id"]), ("extract", "s1"))
        self.assertEqual(r["created_at"], mem.local_time("2026-11-13T09:00:00Z"))

    def test_incremental(self):
        path = self.transcript(2)
        item = [{"text": "Something new", "type": "fact", "project": "homelab", "confidence": 0.9}]
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
             "type": "fact", "project": "homelab", "confidence": 0.9},
            {"text": "Media for the media server is on /mnt/hdd1 and /mnt/hdd2",
             "type": "fact", "project": "homelab", "confidence": 0.9},
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

    def test_sessions_without_human_messages_are_skipped(self):
        path = self.write_transcript([
            {"type": "user", "entrypoint": "sdk-cli", "message": {"content": "scripted prompt"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "answer"}]}},
        ])
        with self.fake_claude([{"text": "x", "type": "fact", "project": "p", "confidence": 1}]) as run:
            self.assertEqual(mem.ingest(self.db, path, "s1"), (0, 0, 0, 0.0))
        run.assert_not_called()
        self.assertEqual(self.db.execute("SELECT lines_done FROM ingested").fetchone()[0], 2)

    def test_dry_run_stores_nothing(self):
        item = [{"text": "A fact", "type": "fact", "project": "homelab", "confidence": 0.9}]
        with self.fake_claude(item), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mem.ingest(self.db, self.transcript(), "s1", dry_run=True)[0], 1)
        self.assertIn("A fact", out.getvalue())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertIsNone(self.db.execute("SELECT * FROM ingested").fetchone())

    def test_hook_mode_logs_errors_instead_of_raising(self):
        args = mock.Mock(hook=True)
        stdin = io.StringIO(json.dumps({"transcript_path": "/nonexistent.jsonl",
                                        "session_id": "s1", "hook_event_name": "SessionEnd"}))
        with mock.patch.object(sys, "stdin", stdin), mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_ingest(self.db, args)
        self.assertIn("ERROR s1", mem.LOG_PATH.read_text())

    def test_session_start_note(self):
        self.add("a fact")
        with mock.patch.object(sys, "stdin", io.StringIO("{}")), redirect_stdout(io.StringIO()) as out, \
                mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_session_start(self.db, None)
        self.assertIn("1 memories", out.getvalue())
        self.assertNotIn("proposed", out.getvalue())
        self.add("be brief", "feedback", "general", status="proposed")
        with mock.patch.object(sys, "stdin", io.StringIO("{}")), redirect_stdout(io.StringIO()) as out, \
                mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("MEM_NO_HOOK", None)
            mem.cmd_session_start(self.db, None)
        self.assertIn("1 proposed", out.getvalue())

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

    def test_hook_guard(self):
        args = mock.Mock(hook=True)
        with mock.patch.dict("os.environ", {"MEM_NO_HOOK": "1"}), \
                mock.patch.object(mem, "ingest") as ingest:
            mem.cmd_ingest(self.db, args)
            mem.cmd_hook(self.db, args)
        ingest.assert_not_called()


if __name__ == "__main__":
    unittest.main()

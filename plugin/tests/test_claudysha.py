"""Tests for claudysha.py. Run with: python3 -m unittest discover -s plugin/tests"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claudysha  # noqa: E402


class HomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"
        self.conf = self.tmp / "config" / "claudysha.conf"
        env = mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(self.home), "CLAUDYSHA_CONFIG": str(self.conf)})
        env.start()
        self.addCleanup(env.stop)
        self.out = []

    def write(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


class TestConfig(HomeTest):
    def test_read_config(self):
        conf = self.tmp / "c.conf"
        conf.write_text("# comment\nhome = ~/x   # trailing\nbad line\nother=1\n")
        self.assertEqual(claudysha.read_config(conf), {"home": "~/x", "other": "1"})
        self.assertEqual(claudysha.read_config(self.tmp / "missing"), {})

    def test_home_precedence(self):
        self.assertEqual(claudysha.home(), self.home)
        with mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": ""}), \
                mock.patch.object(claudysha, "read_config", return_value={"home": "~/from-conf"}):
            self.assertEqual(claudysha.home(), Path.home() / "from-conf")
        with mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": ""}), \
                mock.patch.object(claudysha, "read_config", return_value={}):
            self.assertEqual(claudysha.home(), Path.home() / ".claudysha")


class TestSettings(HomeTest):
    def test_defaults_without_config(self):
        self.assertEqual((claudysha.user_name(), claudysha.user_pronouns()), ("the user", "they/them"))

    def test_values_from_config(self):
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("user_name = Alex\nuser_pronouns = he/him\n")
        self.assertEqual((claudysha.user_name(), claudysha.user_pronouns()), ("Alex", "he/him"))

    def test_config_path_follows_xdg(self):
        with mock.patch.dict(os.environ, {"CLAUDYSHA_CONFIG": "", "XDG_CONFIG_HOME": "/x"}):
            self.assertEqual(claudysha.config_path(), Path("/x/claudysha/claudysha.conf"))
        with mock.patch.dict(os.environ, {"CLAUDYSHA_CONFIG": "", "XDG_CONFIG_HOME": ""}):
            self.assertEqual(claudysha.config_path(), Path.home() / ".config/claudysha/claudysha.conf")


@unittest.skipUnless(shutil.which("git"), "needs git")
class TestScope(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def git(self, folder, *args):
        subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)

    def test_repo_name_from_remote_urls(self):
        for url in ("https://example.com/someone/tools.git", "git@example.com:someone/tools.git",
                    "https://example.com/someone/tools/", "/srv/git/tools"):
            self.assertEqual(claudysha.repo_name(url), "tools", url)

    def test_repository_uses_origin_name_from_any_subfolder(self):
        repo = self.root / "local-clone"
        (repo / "sub").mkdir(parents=True)
        self.git(repo, "init", "-q")
        self.git(repo, "remote", "add", "origin", "https://example.com/someone/tools.git")
        self.assertEqual(claudysha.scope_of(repo / "sub"), "tools")

    def test_repository_without_remote_uses_its_folder_name(self):
        repo = self.root / "notes"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.assertEqual(claudysha.scope_of(repo), "notes")

    def test_plain_or_missing_folder_is_its_absolute_path(self):
        self.assertEqual(claudysha.scope_of(self.root), str(self.root))
        self.assertEqual(claudysha.scope_of(self.root / "gone"), str(self.root / "gone"))

    def test_current_scope_prefers_claude_project_dir(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}):
            self.assertEqual(claudysha.current_scope(), str(self.root))


class TestSetup(HomeTest):
    def test_interactive_answers_and_defaults(self):
        answers = iter(["~/somewhere", "Alex", ""])
        self.assertTrue(claudysha.ensure_config(ask=lambda q: next(answers), out=self.out.append))
        conf = claudysha.read_config(self.conf)
        self.assertEqual(conf, {"home": "~/somewhere", "user_name": "Alex",
                                "user_pronouns": "they/them"})

    def test_non_interactive_writes_defaults(self):
        self.assertTrue(claudysha.ensure_config(ask=None, out=self.out.append))
        self.assertEqual(claudysha.read_config(self.conf)["user_name"], "the user")
        self.assertIn("edit it", self.out[-1])

    def test_existing_config_is_kept(self):
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("user_name = Mine\n")
        self.assertFalse(claudysha.ensure_config(ask=lambda q: "x", out=self.out.append))
        self.assertEqual(self.conf.read_text(), "user_name = Mine\n")

    def test_rendered_example_matches_keys(self):
        example = claudysha.read_config(claudysha.REPO / "claudysha.conf.example")
        self.assertEqual(set(example), set(claudysha.DEFAULTS))


class TestInit(HomeTest):
    def test_creates_data_folder_and_module_parts(self):
        self.assertEqual(claudysha.init(), self.home)
        self.assertTrue((self.home / "brain/core.md").exists())  # created by the memory module
        self.assertEqual(set(claudysha.MODULES), {"memory"})


class TestArchiveLoad(HomeTest):
    def make_db(self, value):
        path = self.home / "memory/memory.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        db.execute("INSERT INTO t VALUES (?)", (value,))
        db.commit()
        return db  # left open: WAL content not checkpointed yet

    def test_roundtrip_erases_extra_files_and_keeps_safety_copy(self):
        self.write("brain/core.md", "v1")
        db = self.make_db("from-archive")
        arc = claudysha.archive(self.tmp / "a.tar.gz")
        db.close()
        with tarfile.open(arc) as tar:
            names = tar.getnames()
        self.assertIn("claudysha-archive.json", names)
        self.assertNotIn("memory/memory.db-wal", names)

        self.write("brain/core.md", "v2")
        self.write("brain/memories/new.md", "added after the archive")
        safety = claudysha.load(arc, confirm=lambda prompt: "erase", out=self.out.append)

        self.assertEqual((self.home / "brain/core.md").read_text(), "v1")
        self.assertFalse((self.home / "brain/memories/new.md").exists())
        rows = sqlite3.connect(self.home / "memory/memory.db").execute("SELECT v FROM t").fetchall()
        self.assertEqual(rows, [("from-archive",)])  # WAL content made it into the archive
        self.assertTrue(any("brain/memories/new.md" in line for line in self.out))
        with tarfile.open(safety) as tar:
            self.assertIn("brain/memories/new.md", tar.getnames())

    def test_load_cancelled_without_the_exact_word(self):
        self.write("brain/core.md", "v1")
        arc = claudysha.archive(self.tmp / "a.tar.gz")
        self.write("brain/core.md", "v2")
        for answer in ("", "yes", "ERASE", "erase please"):
            self.assertIsNone(claudysha.load(arc, confirm=lambda prompt: answer, out=self.out.append))
        self.assertEqual((self.home / "brain/core.md").read_text(), "v2")
        self.assertIn("Cancelled, nothing changed.", self.out)

    def test_load_into_missing_home(self):
        self.write("brain/core.md", "v1")
        arc = claudysha.archive(self.tmp / "a.tar.gz")
        __import__("shutil").rmtree(self.home)
        self.assertIsNone(claudysha.load(arc, confirm=lambda p: "erase", out=self.out.append))
        self.assertEqual((self.home / "brain/core.md").read_text(), "v1")

    def test_rejects_unsafe_or_foreign_archives(self):
        bad = self.tmp / "bad.tar.gz"
        evil = self.tmp / "evil.txt"
        evil.write_text("x")
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo(claudysha.MANIFEST)
            data = json.dumps({"format": 1}).encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            tar.add(evil, arcname="../evil.txt")
        with self.assertRaises(SystemExit):
            claudysha.read_archive(bad)
        plain = self.tmp / "plain.tar.gz"
        with tarfile.open(plain, "w:gz") as tar:
            tar.add(evil, arcname="evil.txt")
        with self.assertRaises(SystemExit):
            claudysha.read_archive(plain)

    def test_tty_confirm_refuses_without_terminal(self):
        with mock.patch.object(sys, "stdin", io.StringIO("erase\n")):
            with self.assertRaises(SystemExit):
                claudysha.tty_confirm("? ")


class TestGuard(HomeTest):
    def setUp(self):
        super().setUp()
        self.home.mkdir(parents=True)
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("user_name = Alex\n")

    def denies(self, tool, cwd=None, **args):
        event = {"tool_name": tool, "tool_input": args, "cwd": str(cwd or self.tmp)}
        out = claudysha.guard_hook(json.dumps(event))
        if out:
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        return bool(out)

    def test_path_tools(self):
        self.assertTrue(self.denies("Read", file_path=str(self.home / "brain/core.md")))
        self.assertTrue(self.denies("Edit", file_path=str(self.home)))
        self.assertTrue(self.denies("Write", file_path=str(self.conf)))
        self.assertTrue(self.denies("Read", file_path="home/x", cwd=self.tmp))  # relative
        self.assertTrue(self.denies("Read", file_path=str(self.tmp / "elsewhere/../home/x")))
        self.assertTrue(self.denies("Grep", pattern="x", path=str(self.home)))
        self.assertTrue(self.denies("Glob", pattern=str(self.home) + "/**/*.md"))
        self.assertTrue(self.denies("Grep", pattern="x", cwd=self.home / "brain"))
        self.assertFalse(self.denies("Read", file_path=str(self.tmp / "home2/x")))
        self.assertFalse(self.denies("Grep", pattern="x", cwd=self.tmp))
        self.assertFalse(self.denies("WebFetch", url=str(self.home)))

    def test_bash(self):
        self.assertTrue(self.denies("Bash", command=f"cat {self.home}/brain/core.md"))
        self.assertTrue(self.denies("Bash", command=f"ls '{self.home}'"))
        self.assertTrue(self.denies("Bash", command=f"cat {self.conf}"))
        self.assertFalse(self.denies("Bash", command=f"ls {self.home}-backup.tar.gz"))
        self.assertFalse(self.denies("Bash", command="mem search cache"))

    def test_home_relative_spellings(self):
        h = Path.home() / ".claudysha-test-guard"
        with mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(h)}):
            for cmd in ("cat ~/.claudysha-test-guard/x", "ls $HOME/.claudysha-test-guard",
                        "ls ${HOME}/.claudysha-test-guard/"):
                self.assertTrue(self.denies("Bash", command=cmd), cmd)
            self.assertTrue(self.denies("Read", file_path="~/.claudysha-test-guard/x"))

    def test_settings_file_free_until_it_exists(self):
        self.conf.unlink()
        self.assertFalse(self.denies("Write", file_path=str(self.conf)))

    def test_pause_needs_dev_mode_and_expires(self):
        target = str(self.home / "x")
        with self.assertRaises(SystemExit):
            claudysha.pause_guard(10)
        claudysha.guard_pause_path().write_text("2999-01-01T00:00:00")  # written by hand: no effect
        self.assertTrue(self.denies("Read", file_path=target))
        claudysha.set_setting("dev_mode", "on")
        self.assertFalse(self.denies("Read", file_path=target))  # the hand-written pause counts now
        claudysha.resume_guard()
        self.assertTrue(self.denies("Read", file_path=target))
        claudysha.pause_guard(10)
        self.assertFalse(self.denies("Read", file_path=target))
        claudysha.guard_pause_path().write_text("2000-01-01T00:00:00")  # expired
        self.assertTrue(self.denies("Read", file_path=target))

    def test_denial_mentions_dev_commands_only_in_dev_mode(self):
        event = json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(self.home)}})
        self.assertNotIn("guard off", claudysha.guard_hook(event))
        claudysha.set_setting("dev_mode", "on")
        self.assertIn("guard off", claudysha.guard_hook(event))


class TestDevMode(HomeTest):
    def setUp(self):
        super().setUp()
        self.home.mkdir(parents=True)
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("# mine\nuser_name = Alex   # me\n")

    def test_on_needs_the_typed_word(self):
        with self.assertRaises(SystemExit):
            claudysha.set_dev_mode(True, confirm=lambda p: "yes", out=self.out.append)
        self.assertFalse(claudysha.dev_mode())
        claudysha.set_dev_mode(True, confirm=lambda p: "dev", out=self.out.append)
        self.assertTrue(claudysha.dev_mode())
        self.assertEqual(claudysha.read_config()["user_name"], "Alex")
        self.assertIn("# mine", self.conf.read_text())

    def test_on_refuses_without_a_terminal(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaises(SystemExit):
                claudysha.set_dev_mode(True, confirm=claudysha.dev_confirm, out=self.out.append)
        self.assertFalse(claudysha.dev_mode())

    def test_off_also_ends_a_pause(self):
        claudysha.set_setting("dev_mode", "on")
        claudysha.pause_guard(10)
        claudysha.set_dev_mode(False, out=self.out.append)
        self.assertFalse(claudysha.dev_mode())
        self.assertFalse(claudysha.guard_pause_path().exists())

    def test_session_text(self):
        claudysha.set_setting("dev_mode", "on")
        text = claudysha.dev_session_text()
        self.assertIn("guard off", text)
        self.assertIn("Never turn dev mode on or off yourself", text)

    def test_bad_input_lets_the_call_through(self):
        self.assertEqual(claudysha.guard_hook("not json"), "")


if __name__ == "__main__":
    unittest.main()

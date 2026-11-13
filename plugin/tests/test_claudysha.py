"""Tests for claudysha.py. Run with: python3 -m unittest discover -s plugin/tests"""
import io
import json
import os
import sqlite3
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
        self.assertEqual((claudysha.user_name(), claudysha.user_pronouns(), claudysha.projects()),
                         ("the user", "they/them", []))

    def test_values_from_config(self):
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("user_name = Alex\nuser_pronouns = he/him\nprojects = a, b ,, c\n")
        self.assertEqual((claudysha.user_name(), claudysha.user_pronouns(), claudysha.projects()),
                         ("Alex", "he/him", ["a", "b", "c"]))

    def test_config_path_follows_xdg(self):
        with mock.patch.dict(os.environ, {"CLAUDYSHA_CONFIG": "", "XDG_CONFIG_HOME": "/x"}):
            self.assertEqual(claudysha.config_path(), Path("/x/claudysha/claudysha.conf"))
        with mock.patch.dict(os.environ, {"CLAUDYSHA_CONFIG": "", "XDG_CONFIG_HOME": ""}):
            self.assertEqual(claudysha.config_path(), Path.home() / ".config/claudysha/claudysha.conf")


class TestSetup(HomeTest):
    def test_interactive_answers_and_defaults(self):
        answers = iter(["~/somewhere", "Alex", "", "a, b"])
        self.assertTrue(claudysha.ensure_config(ask=lambda q: next(answers), out=self.out.append))
        conf = claudysha.read_config(self.conf)
        self.assertEqual(conf, {"home": "~/somewhere", "user_name": "Alex",
                                "user_pronouns": "they/them", "projects": "a, b"})

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
        self.assertEqual(set(example) | {"projects"}, set(claudysha.DEFAULTS))


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


if __name__ == "__main__":
    unittest.main()

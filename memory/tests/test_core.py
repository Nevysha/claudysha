"""Tests for memory/core.py. Run with: python3 -m unittest discover -s memory/tests"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.conf = self.tmp / "claudysha.conf"
        env = mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(self.home), "CLAUDYSHA_CONFIG": str(self.conf)})
        env.start()
        self.addCleanup(env.stop)

    def write(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_paths(self):
        self.assertEqual((core.brain_dir(), core.store_dir()), (self.home / "brain", self.home / "memory"))

    def test_init_creates_layout_and_keeps_existing_files(self):
        self.write("brain/core.md", "mine")
        core.init()
        self.assertEqual((self.home / "brain/core.md").read_text(), "mine")
        for rel in ("brain/maybe-later/README.md", "memory"):
            self.assertTrue((self.home / rel).exists(), rel)
        self.assertFalse((self.home / "brain/memories").exists())  # retired topic notes

    def test_session_start_prints_core_without_paths_or_topic_notes(self):
        self.conf.write_text("user_name = Alex\nuser_pronouns = he/him\n")
        self.write("brain/core.md", "# Core\nbe nice")
        self.write("brain/memories/MEMORY.md", "- [A](a.md)")  # an older home: ignored
        text = core.session_start_text()
        self.assertIn("be nice", text)
        self.assertIn("You work with Alex (he/him)", text)
        self.assertNotIn("a.md", text)
        self.assertNotIn(str(self.home), text)
        self.assertNotIn("core.md", text)

    def test_session_start_empty_without_data(self):
        self.assertEqual(core.session_start_text(), "")
        self.write("brain/core.md", "  \n")
        self.assertEqual(core.session_start_text(), "")


    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as out:
            core.cli(list(argv))
        return out.getvalue()

    def test_core_commands(self):
        import subprocess
        self.write("brain/core.md", "# Core\n\n## Who\n\n- Alex\n\n## Working together\n\n- be nice\n")
        b = self.home / "brain"
        subprocess.run(["git", "init", "-q", str(b)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
            subprocess.run(["git", "-C", str(b), "config", k, v], check=True)
        self.assertIn("be nice", self.run_cli("show"))
        out = self.run_cli("add", "- be brief", "--section", "working together", "-m", "brevity")
        self.assertIn("+- be brief", out)
        self.assertNotIn(str(self.home), out)
        self.run_cli("add", "- in Who", "--section", "Who")
        self.run_cli("edit", "--old", "be nice", "--new", "be kind")
        self.run_cli("remove", "in Who")
        self.assertEqual((b / "core.md").read_text(),
                         "# Core\n\n## Who\n\n- Alex\n\n## Working together\n\n- be kind\n- be brief\n")
        log = subprocess.run(["git", "-C", str(b), "log", "--format=%s"], capture_output=True, text=True).stdout
        self.assertEqual(log.splitlines()[-1], "docs(memory): brevity")
        self.assertEqual(len(log.splitlines()), 4)

    def test_core_commands_refuse_ambiguous_changes(self):
        self.write("brain/core.md", "- a x\n- b x\n")
        for argv in (("edit", "--old", "x", "--new", "y"), ("remove", "x"), ("add", "z", "--section", "Nope")):
            with self.assertRaises(SystemExit):
                self.run_cli(*argv)
        self.assertEqual((self.home / "brain/core.md").read_text(), "- a x\n- b x\n")

if __name__ == "__main__":
    unittest.main()

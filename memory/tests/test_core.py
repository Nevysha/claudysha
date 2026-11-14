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


if __name__ == "__main__":
    unittest.main()

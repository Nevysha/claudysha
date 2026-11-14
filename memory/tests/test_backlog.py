"""Tests for backlog.py. Run with: python3 -m unittest discover -s memory/tests"""
import datetime as dt
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backlog  # noqa: E402

TODAY = dt.date(2026, 11, 27)


class BacklogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "maybe-later"
        self.dir.mkdir()

    def note(self, name, body):
        (self.dir / name).write_text(body)

    def test_frontmatter(self):
        self.note("a.md", "---\nstatus: open   # comment\nreview-after: 2026-11-27\nsummary: x: y\n---\n# A\n")
        self.assertEqual(backlog.frontmatter(self.dir / "a.md"),
                         {"status": "open", "review-after": "2026-11-27", "summary": "x: y"})
        self.note("b.md", "# no frontmatter\nstatus: open\n")
        self.assertEqual(backlog.frontmatter(self.dir / "b.md"), {})

    def test_due(self):
        self.note("due.md", "---\nstatus: open\nreview-after: 2026-11-27\n---\n")
        self.note("waiting.md", "---\nstatus: waiting\nreview-after: 2026-11-20\n---\n")
        self.note("later.md", "---\nstatus: open\nreview-after: 2026-11-28\n---\n")
        self.note("done.md", "---\nstatus: done\nreview-after: 2026-11-01\n---\n")
        self.note("someday.md", "---\nstatus: someday\nreview-after:\n---\n")
        self.note("nodate.md", "---\nstatus: open\nreview-after:\n---\n")
        self.note("plain.md", "# old note without frontmatter\n")
        self.note("README.md", "---\nstatus: open\nreview-after: 2000-01-01\n---\n")
        self.assertEqual([n["file"] for n in backlog.due(self.dir, TODAY)], ["due.md", "waiting.md"])

    def test_session_start_prints_due_items_only(self):
        self.note("due.md", "---\nstatus: open\nreview-after: 2026-11-01\nsummary: do it\n---\n")
        with mock.patch.object(backlog, "BRAIN", Path(self.tmp.name)), \
                mock.patch.dict("os.environ", {}, clear=False) as env, \
                redirect_stdout(io.StringIO()) as out:
            env.pop("MEM_NO_HOOK", None)
            backlog.cmd_session_start(None)
        self.assertIn("1 postponed idea(s)", out.getvalue())
        self.assertIn("- do it (open, due 2026-11-01)", out.getvalue())
        self.assertNotIn("due.md", out.getvalue())
        self.assertNotIn(self.tmp.name, out.getvalue())

    def test_session_start_silent_when_nothing_due_or_no_folder(self):
        for brain in (Path(self.tmp.name), Path(self.tmp.name) / "missing"):
            with mock.patch.object(backlog, "BRAIN", brain), redirect_stdout(io.StringIO()) as out:
                backlog.cmd_session_start(None)
            self.assertEqual(out.getvalue(), "")


class BacklogCommandsTest(unittest.TestCase):
    """`mem backlog ...`, on a temporary home (a git repo, like the real brain folder)."""
    def setUp(self):
        import os, subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        env = mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(self.home),
                                           "CLAUDYSHA_CONFIG": str(Path(self.tmp.name) / "c.conf")})
        env.start()
        self.addCleanup(env.stop)
        self.brain = self.home / "data/brain"
        (self.brain / "maybe-later").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.brain)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
            subprocess.run(["git", "-C", str(self.brain), "config", k, v], check=True)
        patch = mock.patch.object(backlog, "BRAIN", self.brain)
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            backlog.cli(list(argv))
        return out.getvalue()

    def log(self):
        import subprocess
        return subprocess.run(["git", "-C", str(self.brain), "log", "--format=%s"],
                              capture_output=True, text=True).stdout

    def test_add_show_update_done(self):
        out = self.run_cli("add", "Try the new cache #12", "--review-after", "2026-12-01", "--details", "why: speed")
        self.assertIn("try-the-new-cache-12", out)
        shown = self.run_cli("show", "try-the-new-cache-12")
        self.assertIn("summary: Try the new cache #12", shown)
        self.assertIn("review-after: 2026-12-01", shown)
        self.assertIn("why: speed", shown)
        self.run_cli("update", "try-the-new-cache-12", "--review-after", "2026-12-15", "--append", "still slow")
        shown = self.run_cli("show", "try-the-new-cache-12")
        self.assertIn("2026-12-15", shown)
        self.assertIn("why: speed", shown)
        self.assertIn("still slow", shown)
        self.run_cli("done", "try-the-new-cache-12")
        self.assertNotIn("try-the-new-cache", self.run_cli("list"))
        self.assertIn("done", self.run_cli("list", "-a"))
        self.assertEqual(self.log().split(), ["docs(backlog):", "close", "try-the-new-cache-12",
                                              "docs(backlog):", "update", "try-the-new-cache-12",
                                              "docs(backlog):", "add", "try-the-new-cache-12"])

    def test_update_keeps_comments_and_unknown_keys(self):
        (self.brain / "maybe-later" / "old.md").write_text(
            "---\nstatus: open   # open | done\nowner: me\nsummary: s\n---\n# Title\n\n---\nmore\n")
        self.run_cli("update", "old", "--status", "waiting")
        text = (self.brain / "maybe-later" / "old.md").read_text()
        self.assertIn("status: waiting   # open | done", text)
        self.assertIn("owner: me", text)
        self.assertIn("# Title\n\n---\nmore", text)

    def test_edit_replaces_one_passage(self):
        self.run_cli("add", "Clean up", "--details", "old path here; old path there")
        with self.assertRaises(SystemExit):
            self.run_cli("edit", "clean-up", "--old", "old path", "--new", "new path")
        self.run_cli("edit", "clean-up", "--old", "old path here", "--new", "new path here")
        self.assertIn("new path here; old path there", self.run_cli("show", "clean-up"))

    def test_unknown_or_unsafe_ids_and_bad_dates(self):
        for bad in ("nope", "../core", "README"):
            with self.assertRaises(SystemExit):
                self.run_cli("show", bad)
        with self.assertRaises(SystemExit):
            self.run_cli("add", "x", "--review-after", "tomorrow")

    def test_list_shows_ids_not_paths(self):
        self.run_cli("add", "One thing")
        out = self.run_cli("list")
        self.assertIn("one-thing: One thing", out)
        self.assertNotIn(str(self.home), out)


if __name__ == "__main__":
    unittest.main()

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
        self.assertIn("1 postponed item(s)", out.getvalue())
        self.assertIn("due.md (open, due 2026-11-01): do it", out.getvalue())

    def test_session_start_silent_when_nothing_due_or_no_folder(self):
        for brain in (Path(self.tmp.name), Path(self.tmp.name) / "missing"):
            with mock.patch.object(backlog, "BRAIN", brain), redirect_stdout(io.StringIO()) as out:
                backlog.cmd_session_start(None)
            self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

"""Tests for the interactive review loop."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "store"))
import mem  # noqa: E402
import review  # noqa: E402
from test_mem import StoreTest  # noqa: E402


class TestReviewLoop(StoreTest):
    def run_review(self, keys, edits=()):
        rows = self.db.execute("SELECT * FROM memories WHERE status='proposed' ORDER BY id").fetchall()
        keys, edits, self.out = iter(keys), iter(edits), []
        return review.review_loop(
            self.db, rows, mem.search, mem.NEEDS_APPROVAL,
            key=lambda: next(keys), edit=lambda prompt, cur: next(edits),
            out=self.out.append, st=review.Style(False), width=80)

    def status(self, id_):
        return mem.get(self.db, id_)["status"]

    def setUp(self):
        super().setUp()
        self.a = self.add("Keep answers short", "feedback", "general", status="proposed")
        self.b = self.add("Commits use Conventional Commits", "preference", "general", status="proposed")
        self.c = self.add("sudo on the laptop is sudo-rs", "feedback", "laptop", status="proposed")

    def test_approve_reject_skip(self):
        counts = self.run_review(["a", "r", "s"])
        self.assertEqual((self.status(self.a), self.status(self.b), self.status(self.c)),
                         ("active", "rejected", "proposed"))
        self.assertEqual((counts["approved"], counts["rejected"], counts["skipped"]), (1, 1, 1))

    def test_quit_stops(self):
        self.run_review(["q"])
        self.assertEqual(self.status(self.a), "proposed")

    def test_edit_then_approve(self):
        counts = self.run_review(["e", "a", "q"], edits=["Keep answers short and concrete"])
        r = mem.get(self.db, self.a)
        self.assertEqual((r["text"], r["status"]), ("Keep answers short and concrete", "active"))
        self.assertEqual(counts["edited"], 1)
        self.assertEqual(mem.search(self.db, "concrete")[0]["id"], self.a)  # FTS updated

    def test_retype_to_fact_stores_it(self):
        self.run_review(["s", "s", "t", "f"])
        r = mem.get(self.db, self.c)
        self.assertEqual((r["type"], r["status"]), ("fact", "active"))

    def test_retype_within_behaviour_types_keeps_asking(self):
        self.run_review(["t", "p", "a", "q"])
        r = mem.get(self.db, self.a)
        self.assertEqual((r["type"], r["status"]), ("preference", "active"))

    def test_unknown_key_and_bad_type_key_repeat_entry(self):
        self.run_review(["z", "t", "?", "r", "q"])
        self.assertEqual(self.status(self.a), "rejected")

    def test_undo_restores_previous_entry(self):
        counts = self.run_review(["a", "r", "u", "u", "s", "a", "q"])
        # a then r, undo both, then skip a, approve b
        self.assertEqual((self.status(self.a), self.status(self.b), self.status(self.c)),
                         ("proposed", "active", "proposed"))
        self.assertEqual((counts["approved"], counts["rejected"], counts["skipped"]), (1, 0, 1))
        self.assertIn("nothing to undo", "".join(self.run_review(["u", "q"]) and self.out))

    def test_undo_retype(self):
        self.run_review(["t", "f", "u", "q"])
        r = mem.get(self.db, self.a)
        self.assertEqual((r["type"], r["status"]), ("feedback", "proposed"))

    def test_render_shows_context(self):
        self.add("Answers should stay short in chat", "fact", "general")
        self.run_review(["q"])
        screen = self.out[0]
        self.assertIn("1/3", screen)
        self.assertIn("Keep answers short", screen)
        self.assertIn("looks related to:", screen)
        self.assertIn("[a] approve", screen)

"""Tests for the memory module's dashboard routes (panel.py). Run with: python3 -m unittest discover -s memory/tests"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "store"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mem  # noqa: E402
import panel  # noqa: E402


class PanelTest(unittest.TestCase):
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

    def status(self, id_):
        return mem.get(self.db, id_)["status"]


class TestReading(PanelTest):
    def test_summary_counts_and_scopes(self):
        self.add("the router runs on port 8080")
        self.add("reply in short sentences", "preference", "global", status="proposed")
        s = panel.summary({}, {})
        self.assertEqual(s["counts"]["active"], 1)
        self.assertEqual(s["counts"]["proposed"], 1)
        self.assertEqual(s["scopes"][0]["scope"], "global")  # global first
        self.assertIn("preference", s["needs_approval"])

    def test_proposed_has_source_and_related(self):
        self.add("Robin prefers short commit messages")
        p = self.add("Robin wants short commit messages", "preference", status="proposed", session_id="abcdef1234")
        items = panel.proposed({}, {})["items"]
        self.assertEqual([i["id"] for i in items], [p])
        self.assertEqual(items[0]["source_label"], "session abcdef12")
        self.assertEqual(len(items[0]["related"]), 1)

    def test_listing_filters(self):
        a = self.add("backups run nightly", scope="homelab")
        b = self.add("deploys go through CI", "decision", scope="webapp")
        c = self.add("old note", scope="homelab", status="rejected")
        ids = lambda **q: [i["id"] for i in panel.listing(q, {})["items"]]  # noqa: E731
        self.assertEqual(ids(), [b, a])
        self.assertEqual(ids(scope="homelab"), [a])
        self.assertEqual(ids(type="decision"), [b])
        self.assertEqual(ids(status="rejected"), [c])
        self.assertEqual(ids(status="all"), [c, b, a])
        self.assertEqual(ids(q="nightly backups"), [a])
        self.assertEqual(ids(q="old note"), [])
        self.assertEqual(ids(q="old note", status="all"), [c])
        self.assertEqual(panel.listing({"limit": "1"}, {})["total"], 2)

    def test_listing_unused(self):
        old = self.add("an old unused fact", created_at="2020-01-01T00:00:00")
        used = self.add("an old used fact", created_at="2020-01-01T00:00:00")
        self.add("a new fact")
        self.db.execute("UPDATE memories SET use_count = 3 WHERE id = ?", (used,))
        self.db.commit()
        self.assertEqual([i["id"] for i in panel.listing({"unused_days": "30"}, {})["items"]], [old])

    def test_listing_does_not_count_as_use(self):
        a = self.add("backups run nightly")
        panel.listing({"q": "backups"}, {})
        panel.item({"id": str(a)}, {})
        self.assertEqual(mem.get(self.db, a)["use_count"], 0)

    def test_item_shows_the_supersede_chain(self):
        old = self.add("backups run weekly")
        panel.supersede({}, {"id": old, "text": "backups run nightly"})
        new = mem.get(self.db, old)["superseded_by"]
        d = panel.item({"id": str(old)}, {})
        self.assertEqual(d["superseded_by"]["id"], new)
        self.assertEqual([x["id"] for x in panel.item({"id": str(new)}, {})["supersedes"]], [old])

    def test_bad_filters(self):
        with self.assertRaises(ValueError):
            panel.listing({"status": "deleted"}, {})
        with self.assertRaises(ValueError):
            panel.listing({"type": "opinion"}, {})


class TestWriting(PanelTest):
    def test_approve_reject_restore(self):
        a = self.add("reply in short sentences", "preference", status="proposed")
        b = self.add("use tabs", "preference", status="proposed")
        panel.set_status({}, {"ids": [a, b], "status": "active"})
        self.assertEqual((self.status(a), self.status(b)), ("active", "active"))
        panel.set_status({}, {"ids": [b], "status": "rejected"})
        self.assertEqual(self.status(b), "rejected")
        panel.set_status({}, {"ids": [b], "status": "active"})
        self.assertEqual(self.status(b), "active")

    def test_status_refuses_bad_input(self):
        a = self.add("x")
        for body in ({"ids": [a], "status": "superseded"}, {"ids": [], "status": "active"},
                     {"ids": ["1"], "status": "active"}):
            with self.assertRaises(ValueError):
                panel.set_status({}, body)
        with self.assertRaises(SystemExit):
            panel.set_status({}, {"ids": [999], "status": "active"})

    def test_superseded_memories_keep_their_status(self):
        old = self.add("backups run weekly")
        panel.supersede({}, {"id": old, "text": "backups run nightly"})
        with self.assertRaises(ValueError):
            panel.set_status({}, {"ids": [old], "status": "active"})
        with self.assertRaises(ValueError):
            panel.supersede({}, {"id": old, "text": "again"})

    def test_edit(self):
        a = self.add("backups run nightly", tags="ops")
        item = panel.edit({}, {"id": a, "text": " backups run at 2am ", "scope": "global", "tags": "Ops  Backup",
                               "confidence": 0.5, "type": "decision"})["item"]
        self.assertEqual((item["text"], item["scope"], item["tags"], item["confidence"], item["type"]),
                         ("backups run at 2am", "global", "ops backup", 0.5, "decision"))
        self.assertEqual([r["id"] for r in mem.search(self.db, "2am")], [a])  # the search index follows

    def test_edit_refuses_bad_input(self):
        a = self.add("x")
        for body in ({"id": a}, {"id": a, "text": "  "}, {"id": a, "type": "opinion"},
                     {"id": a, "scope": ""}, {"id": a, "confidence": 2}):
            with self.assertRaises(ValueError, msg=body):
                panel.edit({}, body)

    def test_supersede_keeps_type_scope_and_tags(self):
        old = self.add("reply briefly", "preference", "global", tags="style")
        r = panel.supersede({}, {"id": old, "text": "reply briefly, in plain words"})
        new = mem.get(self.db, r["new"])
        self.assertEqual((new["type"], new["scope"], new["tags"], new["status"]), ("preference", "global", "style", "active"))
        self.assertEqual(self.status(old), "superseded")

    def test_overview_cards(self):
        self.add("x")
        self.add("y", "preference", status="proposed")
        cards = {c["label"]: c for c in panel.overview_cards()}
        self.assertEqual(cards["Memories"]["value"], 1)
        self.assertEqual(cards["Waiting for review"]["value"], 1)
        self.assertTrue(cards["Waiting for review"]["accent"])


if __name__ == "__main__":
    unittest.main()

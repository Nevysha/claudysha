"""Tests for dashboard.py. Run with: python3 -m unittest discover -s plugin/tests"""
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claudysha  # noqa: E402
import dashboard  # noqa: E402


class DashboardTest(unittest.TestCase):
    """One server on a temporary home for the whole class (the store module reads its path once)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.home = cls.tmp / "home"
        cls.env = mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(cls.home),
                                               "CLAUDYSHA_CONFIG": str(cls.tmp / "claudysha.conf"),
                                               "MEM_DB": str(cls.tmp / "memory.db")})
        cls.env.start()
        claudysha.init()
        cls.srv = dashboard.make_server(port=0, token="secret-token")
        # The store module reads its database path once, at its first import: point it here.
        store = sys.modules["mem"]
        store.DB_PATH, store.LOG_PATH = cls.tmp / "memory.db", cls.tmp / "extract.log"
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, body=None, token="secret-token", host=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers[dashboard.TOKEN_HEADER] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = ctype
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw.decode()
        return res.status, payload, res

    def test_page_loads_its_built_script_with_strict_headers(self):
        status, page, res = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
        self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer")
        scripts = re.findall(r'<script[^>]*src="([^"]+)"', page)
        self.assertTrue(scripts and all(s.startswith("/static/plugin/assets/") for s in scripts), scripts)
        self.assertNotIn("<script>", page)  # no inline code: the CSP would block it
        status, js, res = self.request("GET", scripts[0], token=None)
        self.assertEqual(status, 200)
        self.assertIn("javascript", res.getheader("Content-Type"))

    def test_api_needs_the_token(self):
        self.assertEqual(self.request("GET", "/api/plugin/overview", token=None)[0], 401)
        self.assertEqual(self.request("GET", "/api/plugin/overview", token="wrong")[0], 401)
        status, data, _ = self.request("GET", "/api/plugin/overview")
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["name"], "the user")
        self.assertIn("Memories", [c["label"] for c in data["cards"]])

    def test_other_hosts_are_refused(self):
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends its own Host.
        self.assertEqual(self.request("GET", "/api/plugin/overview", host="evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/", token=None, host=f"evil.example:{self.port}")[0], 403)
        self.assertEqual(self.request("GET", "/", token=None, host=f"localhost:{self.port}")[0], 200)

    def test_static_files_stay_in_their_folder(self):
        for path in ("/static/plugin/../dashboard.py", "/static/plugin/%2e%2e/dashboard.py",
                     "/static/nope/app.js", "/static/plugin/missing.js", "/etc/passwd"):
            self.assertEqual(self.request("GET", path, token=None)[0], 404, path)

    def test_post_needs_json(self):
        status, data, _ = self.request("POST", "/api/memory/status", {"ids": [1], "status": "active"},
                                       ctype="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)

    def test_bad_requests_report_the_reason(self):
        status, data, _ = self.request("POST", "/api/memory/status", {"ids": [1], "status": "deleted"})
        self.assertEqual(status, 400)
        self.assertIn("status must be", data["error"])
        status, data, _ = self.request("GET", "/api/memory/item?id=999")
        self.assertEqual(status, 400)
        self.assertIn("no memory #999", data["error"])

    def test_archive_and_load_preview_but_no_load(self):
        status, data, _ = self.request("POST", "/api/plugin/archive", {})
        self.assertEqual(status, 200)
        path = Path(data["path"])
        self.assertEqual(path.parent, self.home / "archives")
        self.assertTrue(path.is_file())
        status, prev, _ = self.request("GET", f"/api/plugin/load-preview?name={path.name}")
        self.assertEqual(status, 200)
        self.assertTrue(prev["command"].startswith("claudysha load "))
        self.assertEqual(self.request("GET", "/api/plugin/load-preview?name=../../etc/passwd")[0], 400)
        self.assertFalse(any("load" in p and m == "POST" for m, p in self.srv.routes))


class TestSessionPreview(DashboardTest):
    """Runs the real SessionStart hooks against the temporary home."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(claudysha.REPO / "memory" / "store"))
        import mem
        db = mem.connect()
        mem.insert(db, "Robin wants commit messages in English", "preference", "webapp")
        mem.insert(db, "Robin likes short answers", "preference", "global")
        db.close()
        (claudysha.data_dir() / "brain" / "core.md").write_text("# Core\n\n- Robin builds a webapp.\n")

    def preview(self, scope=""):
        status, data, _ = self.request("GET", f"/api/plugin/session-preview?scope={scope}")
        self.assertEqual(status, 200, data)
        return data, {s["source"]: s for s in data["sections"]}

    def test_scope_rules_only_in_that_scope(self):
        data, parts = self.preview("webapp")
        self.assertEqual(data["scope"], "webapp")
        memory = parts["mem"]["text"]
        self.assertIn("commit messages in English", memory)
        self.assertIn("short answers", memory)
        self.assertIn("Robin builds a webapp", parts["memory/core.py"]["text"])
        self.assertEqual(parts["memory/core.py"]["title"], "Core memory")
        self.assertIn("webapp", [s["scope"] for s in data["scopes"]])

    def test_fresh_session(self):
        data, parts = self.preview()
        self.assertIsNone(data["scope"])
        self.assertNotIn("commit messages in English", parts["mem"]["text"])
        self.assertIn("short answers", parts["mem"]["text"])
        self.assertIn(dashboard.fresh_scope(), parts["mem"]["text"])

    def test_tokens_and_skills(self):
        data, parts = self.preview()
        self.assertEqual(data["total"], sum(s["tokens"] for s in data["sections"]))
        self.assertGreater(parts["mem"]["tokens"], 0)
        skills = parts["skills/*/SKILL.md"]["text"]
        self.assertIn("claudysha:recall:", skills)
        self.assertNotIn("memory-review", skills)  # for the user only: not listed to the model
        self.assertFalse(any(s["error"] for s in data["sections"]), data["sections"])

    def test_on_demand_skills_are_shown_but_not_counted(self):
        data, _ = self.preview()
        bodies = {s["title"]: s for s in data["on_demand"]}
        recall = bodies["claudysha:recall"]
        self.assertIn("mem search", recall["text"])
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", recall["text"])
        self.assertNotIn("name: recall", recall["text"])  # the body, without its frontmatter
        self.assertEqual(recall["when"], "when Claude uses the skill")
        self.assertEqual(bodies["claudysha:memory-review"]["when"], "when you run /claudysha:memory-review")
        self.assertEqual(data["total"], sum(s["tokens"] for s in data["sections"]))


class TestTokens(unittest.TestCase):
    def test_estimate(self):
        self.assertEqual(dashboard.estimate_tokens(""), 0)
        self.assertEqual(dashboard.estimate_tokens("the cat sat"), 3)
        self.assertEqual(dashboard.estimate_tokens("configuration"), 3)
        self.assertEqual(dashboard.estimate_tokens("port 8080!\n"), 5)
        prose = "Search your memory before answering about past work or redoing something. " * 20
        self.assertTrue(len(prose) / 6 < dashboard.estimate_tokens(prose) < len(prose) / 3)


class TestDetach(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"CLAUDYSHA_HOME": str(self.tmp / "home"),
                                           "CLAUDYSHA_CONFIG": str(self.tmp / "claudysha.conf"),
                                           "MEM_DB": str(self.tmp / "memory.db")})
        env.start()
        self.addCleanup(env.stop)
        claudysha.init()
        self.out = []
        self.addCleanup(lambda: dashboard.running() and dashboard.stop(out=self.out.append))

    def test_detach_status_stop(self):
        st = dashboard.detach(port=0, open_browser=False, out=self.out.append)
        self.assertIn(st["url"], self.out[0])
        self.assertEqual(os.stat(dashboard.state_path()).st_mode & 0o777, 0o600)
        conn = http.client.HTTPConnection("127.0.0.1", st["port"], timeout=10)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{st['port']}"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        # A second start reuses it.
        self.assertEqual(dashboard.detach(port=0, open_browser=False, out=self.out.append)["pid"], st["pid"])
        self.assertIn("already running", self.out[-1])
        self.assertEqual(dashboard.status(out=self.out.append)["pid"], st["pid"])
        self.assertTrue(dashboard.stop(out=self.out.append))
        self.assertFalse(dashboard._alive(st["pid"]))
        self.assertIsNone(dashboard.running())
        self.assertFalse(dashboard.stop(out=self.out.append))
        self.assertIn("not running", self.out[-1])

    def test_older_running_dashboard_is_reported(self):
        st = dashboard.detach(port=0, open_browser=False, out=self.out.append)
        with mock.patch.object(dashboard, "plugin_version", return_value="99.0.0"):
            dashboard.detach(port=0, open_browser=False, out=self.out.append)
        self.assertIn("older claudysha", self.out[-1])
        self.assertEqual(dashboard.running()["pid"], st["pid"])  # reported, not restarted

    def test_stale_state_is_forgotten(self):
        dashboard.state_path().parent.mkdir(parents=True, exist_ok=True)
        dashboard.state_path().write_text(json.dumps({"pid": 2 ** 22 + 12345, "port": 1, "url": "x"}))
        self.assertIsNone(dashboard.running())
        self.assertFalse(dashboard.state_path().exists())


class TestBuiltPage(unittest.TestCase):
    def test_built_page_is_committed_and_self_contained(self):
        # Users need only Python: the page built from dashboard/ is in plugin/web/, and it loads
        # nothing from outside (the CSP allows only the page's own files).
        web = claudysha.REPO / "plugin" / "web"
        page = (web / "index.html").read_text()
        for ref in re.findall(r'(?:src|href)="([^"]+)"', page):
            self.assertTrue(ref.startswith("/static/plugin/"), ref)
            self.assertTrue((web / ref.removeprefix("/static/plugin/")).is_file(), ref)


class TestArchivesFolder(unittest.TestCase):
    def test_archives_folder_is_not_data(self):
        # An old layout's migration must not move the dashboard's archives into data/.
        self.assertIn("archives", claudysha.PLUGIN_ENTRIES)


if __name__ == "__main__":
    unittest.main()

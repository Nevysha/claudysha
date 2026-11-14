"""Tests for dashboard.py. Run with: python3 -m unittest discover -s plugin/tests"""
import http.client
import json
import os
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

    def test_page_loads_module_scripts_with_strict_headers(self):
        status, page, res = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn('src="/static/memory/memory.js"', page)
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
        self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer")
        status, js, res = self.request("GET", "/static/memory/memory.js", token=None)
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


class TestArchivesFolder(unittest.TestCase):
    def test_archives_folder_is_not_data(self):
        # An old layout's migration must not move the dashboard's archives into data/.
        self.assertIn("archives", claudysha.PLUGIN_ENTRIES)


if __name__ == "__main__":
    unittest.main()

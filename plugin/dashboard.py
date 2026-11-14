"""claudysha dashboard: a local web page where the user browses and manages claudysha.

`claudysha dashboard` serves it on 127.0.0.1 and opens the browser. Layers stay apart like
everywhere else: this file is the plugin's part (the server, the page shell, the overview with
settings and archives), and each module in claudysha.MODULES may add panels by exposing
`dashboard()` in its init file, returning a spec:

    {"static": Path,                 # folder served at /static/<module>/
     "scripts": ["panel.js", ...],   # loaded by the page, in order; they call claudysha.panel(...)
     "routes": {("GET", "/api/<module>/..."): fn(query, body) -> JSON-able, ...},
     "overview": fn() -> [{"label", "value", "hint"?}, ...]}   # cards on the overview (optional)

A handler raises ValueError (or SystemExit, as the CLI helpers do) for a bad request: the page
shows the message.

Security: the server listens on 127.0.0.1 only and every API call must carry the random token
made at start (the URL printed, in its #fragment so it never reaches a log or a Referer). The
Host header must be the server's own, which stops DNS rebinding, and the custom token header
can't be sent cross-origin without a CORS preflight, which is never granted. `load` is not
offered here on purpose: it needs the user's typed confirmation in a real terminal, and a web
request can always be replayed by any program that knows the URL. The page shows the command
to run instead.
"""
import datetime as dt
import hmac
import http.server
import json
import mimetypes
import secrets
import shlex
import sys
import traceback
import urllib.parse
import webbrowser
from pathlib import Path

import claudysha

WEB = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8765
TOKEN_HEADER = "X-Claudysha-Token"
MAX_BODY = 1_000_000
SCRIPTS_MARK = "<!--claudysha:scripts-->"
HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


# ---------------------------------------------------------------- the plugin's own panel

def archives_dir():
    """Where the dashboard writes archives (in the plugin's folder, outside data/)."""
    return claudysha.home() / "archives"


def list_archives():
    """Archives made from the dashboard, and the backups `load` saves before replacing the data."""
    h = claudysha.home()
    found = []
    for folder, pattern, kind in ((archives_dir(), "*.tar.gz", "archive"),
                                  (h, "*-before-load-*.tar.gz", "before load")):
        if folder.is_dir():
            found += [(p, kind) for p in folder.glob(pattern) if p.is_file()]
    found.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return [{"name": p.name, "kind": kind, "path": str(p), "size": p.stat().st_size,
             "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p, kind in found]


def folder_size(path):
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file() and not p.is_symlink())


def overview(specs):
    def run(query, body):
        paused = claudysha.guard_paused_until()
        cards = []
        for spec in specs:
            if spec.get("overview"):
                cards += [{**c, "module": spec["name"]} for c in spec["overview"]()]
        data = claudysha.data_dir()
        return {
            "user": {"name": claudysha.user_name(), "pronouns": claudysha.user_pronouns()},
            "cards": cards,
            "settings": {k: str(claudysha.home()) if k == "home" else claudysha.setting(k)
                         for k in claudysha.DEFAULTS},
            "config": {"path": str(claudysha.config_path()), "exists": claudysha.config_path().exists()},
            "data": {"path": str(data), "size": folder_size(data) if data.is_dir() else 0},
            "dev_mode": claudysha.dev_mode(),
            "guard": {"paused_until": paused.isoformat(timespec="minutes") if paused else None},
            "archives": list_archives(),
            "archives_dir": str(archives_dir()),
        }
    return run


def make_archive(query, body):
    dest = archives_dir()
    dest.mkdir(parents=True, exist_ok=True)
    return {"path": str(claudysha.archive(dest)), "archives": list_archives()}


def preview_load(query, body):
    """What `claudysha load` of a listed archive would erase, and the command to run it."""
    name = query.get("name", "")
    match = [a for a in list_archives() if a["name"] == name]
    if not match:
        raise ValueError(f"no archive named {name!r}")
    path = match[0]["path"]
    manifest, names, erased = claudysha.plan_load(path)
    return {"archive": match[0], "created": manifest.get("created"), "files": len(names),
            "erased": erased, "command": f"claudysha load {shlex.quote(path)}"}


def plugin_spec(specs):
    return {"name": "plugin", "static": WEB, "scripts": [],
            "routes": {("GET", "/api/plugin/overview"): overview(specs),
                       ("POST", "/api/plugin/archive"): make_archive,
                       ("GET", "/api/plugin/load-preview"): preview_load}}


def module_specs():
    specs = []
    for name in claudysha.MODULES:
        module = claudysha.load_module(name)
        if hasattr(module, "dashboard"):
            specs.append({**module.dashboard(), "name": name})
    return specs


# ---------------------------------------------------------------- server

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "claudysha"
    sys_version = ""

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def send(self, status, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method):
        srv = self.server
        if self.headers.get("Host", "") not in srv.allowed_hosts:
            return self.send(403, {"error": "wrong host"})
        path, _, qs = self.path.partition("?")
        if path.startswith("/api/"):
            return self.api(method, path, qs)
        if method != "GET":
            return self.send(405, {"error": "method not allowed"})
        if path in ("/", "/index.html"):
            return self.send(200, srv.index, "text/html; charset=utf-8")
        return self.static(path)

    def api(self, method, path, qs):
        srv = self.server
        if not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), srv.token):
            return self.send(401, {"error": "missing or wrong token: open the URL `claudysha dashboard` printed"})
        route = srv.routes.get((method, path))
        if not route:
            return self.send(404, {"error": f"no such API: {method} {path}"})
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(qs).items()}
        body = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.send(413, {"error": "request too large"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.send(415, {"error": "send JSON"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad JSON"})
            if not isinstance(body, dict):
                return self.send(400, {"error": "send a JSON object"})
        try:
            return self.send(200, route(query, body))
        except (ValueError, KeyError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        except SystemExit as e:
            return self.send(400, {"error": str(e.code)})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def static(self, path):
        parts = path.split("/")  # /static/<module>/<file...>
        if len(parts) < 4 or parts[1] != "static" or parts[2] not in self.server.static:
            return self.send(404, {"error": "not found"})
        root = self.server.static[parts[2]].resolve()
        file = (root / urllib.parse.unquote("/".join(parts[3:]))).resolve()
        if root not in file.parents or not file.is_file():
            return self.send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
            ctype += "; charset=utf-8"
        return self.send(200, file.read_bytes(), ctype)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def make_server(port=DEFAULT_PORT, token=None, verbose=False):
    """Build the server (not started). Falls back to a free port when `port` is taken."""
    try:
        srv = Server(("127.0.0.1", port), Handler)
    except OSError:
        if not port:
            raise
        srv = Server(("127.0.0.1", 0), Handler)
    specs = module_specs()
    specs.insert(0, plugin_spec(specs))
    srv.verbose = verbose
    srv.token = token or secrets.token_urlsafe(24)
    srv.routes, srv.static, scripts = {}, {}, []
    for spec in specs:
        srv.routes.update(spec.get("routes", {}))
        if spec.get("static"):
            srv.static[spec["name"]] = Path(spec["static"])
        scripts += [f"/static/{spec['name']}/{s}" for s in spec.get("scripts", [])]
    tags = "\n".join(f'<script defer src="{urllib.parse.quote(s)}"></script>' for s in scripts)
    srv.index = (WEB / "index.html").read_text().replace(SCRIPTS_MARK, tags).encode()
    p = srv.server_address[1]
    srv.allowed_hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
    srv.url = f"http://127.0.0.1:{p}/#token={srv.token}"
    return srv


def serve(port=DEFAULT_PORT, open_browser=True, verbose=False, out=print):
    srv = make_server(port, verbose=verbose)
    out(f"claudysha dashboard: {srv.url}")
    out("Only this machine can reach it, and only with that link. Ctrl+C stops it.")
    sys.stdout.flush()
    if open_browser:
        try:
            webbrowser.open(srv.url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        out("\ndashboard stopped")
    finally:
        srv.server_close()

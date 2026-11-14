"""claudysha dashboard: a local web page where the user browses and manages claudysha.

`claudysha dashboard` serves it on 127.0.0.1 and opens the browser. Layers stay apart like
everywhere else: this file is the plugin's part (the server, the page shell, the overview with
settings and archives), and each module in claudysha.MODULES may add panels by exposing
`dashboard()` in its init file, returning a spec:

    {"static": Path,                 # folder served at /static/<module>/
     "scripts": ["panel.js", ...],   # loaded by the page, in order; they call claudysha.panel(...)
     "routes": {("GET", "/api/<module>/..."): fn(query, body) -> JSON-able, ...},
     "overview": fn() -> [{"label", "value", "hint"?}, ...],   # cards on the overview (optional)
     "scopes": fn() -> [{"scope", "count"}, ...],               # scopes the session preview offers
     "session_labels": {"<hook script, relative to the repo>": "<title>", ...}}  # names its hooks

The session preview runs the plugin's real SessionStart hooks (hooks/hooks.json) for a scope,
so it shows exactly what a session receives, and estimates the tokens.

One dashboard runs at a time. Its pid, port and link are kept in <claudysha home>/state/
dashboard.json while it runs, so `claudysha dashboard -d` can start it in the background, and
`claudysha dashboard status` / `stop` can find it again.

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
import contextlib
import datetime as dt
import hmac
import http.server
import json
import math
import mimetypes
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
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


# ---------------------------------------------------------------- session preview

PREVIEW_TIMEOUT = 15
SKILL_FIELD = re.compile(r"^(name|description|disable-model-invocation):\s*(.*)$")


def estimate_tokens(text):
    """A rough count of Claude tokens (no tokenizer in the standard library): short words are one
    token, long ones a token per ~6 letters, each digit run a token per 3 digits, each symbol one,
    each line break one. Close to real counts for English prose and Markdown, within ~15%."""
    n = 0
    for m in re.finditer(r"[^\W\d_]+|\d+|\n+|[^\w\s]|_", text):
        s = m.group()
        if s[0] == "\n":
            n += 1
        elif s[0].isdigit():
            n += math.ceil(len(s) / 3)
        elif s[0].isalpha():
            n += math.ceil(len(s) / 6)
        else:
            n += 1
    return n


def session_hooks():
    """The SessionStart commands of hooks/hooks.json, in order: [(command, timeout)]."""
    hooks = json.loads((claudysha.REPO / "hooks" / "hooks.json").read_text())["hooks"]
    return [(h["command"], h.get("timeout", 10)) for group in hooks.get("SessionStart", [])
            for h in group.get("hooks", []) if h.get("type") == "command"]


def hook_script(command):
    """'"${CLAUDE_PLUGIN_ROOT}/memory/core.py" session-start' -> 'memory/core.py'."""
    first = shlex.split(command)[0] if command.strip() else ""
    return first.replace("${CLAUDE_PLUGIN_ROOT}/", "")


def run_hook(command, timeout, scope, cwd):
    """Run one SessionStart hook as Claude Code would, for a session in `scope`."""
    env = {k: v for k, v in os.environ.items() if k not in ("MEM_NO_HOOK", "CLAUDE_PROJECT_DIR")}
    env["CLAUDE_PLUGIN_ROOT"] = str(claudysha.REPO)
    payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(cwd),
               "session_id": "dashboard-preview", "claudysha_preview_scope": scope}
    try:
        proc = subprocess.run(command, shell=True, input=json.dumps(payload), capture_output=True, text=True,
                              env=env, cwd=str(cwd), timeout=min(timeout, PREVIEW_TIMEOUT))
    except subprocess.TimeoutExpired:
        return "", f"timed out after {timeout}s"
    return proc.stdout.strip(), (proc.stderr.strip()[-500:] if proc.returncode else "")


def skills():
    """The plugin's skills: [(name, frontmatter fields, body)]."""
    out = []
    for path in sorted((claudysha.REPO / "skills").glob("*/SKILL.md")):
        meta, lines, body_start = {}, path.read_text().splitlines(), 0
        if lines and lines[0].strip() == "---":
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "---":
                    body_start = i + 1
                    break
                m = SKILL_FIELD.match(line)
                if m:
                    meta[m.group(1)] = m.group(2).strip()
        out.append((meta.get("name") or path.parent.name, meta, "\n".join(lines[body_start:]).strip()))
    return out


def user_only(meta):
    return meta.get("disable-model-invocation", "").lower() == "true"


def skill_listing():
    """What Claude Code lists about the plugin's skills in every session: name and description
    (skills with disable-model-invocation are only for the user, and not listed)."""
    return "\n".join(f"- claudysha:{name}: {meta.get('description', '')}"
                     for name, meta, _ in skills() if not user_only(meta))


def skill_bodies():
    """What loads later, on demand: each skill's full instructions, when Claude uses it (or, for
    user-only skills, when the user runs it), with the plugin's folder filled in."""
    return [{"title": f"claudysha:{name}", "source": f"skills/{name}/SKILL.md",
             "when": f"when you run /claudysha:{name}" if user_only(meta) else "when Claude uses the skill",
             "text": (body := body_text.replace("${CLAUDE_PLUGIN_ROOT}", str(claudysha.REPO))),
             "tokens": estimate_tokens(body)}
            for name, meta, body_text in skills()]


def fresh_scope():
    """The scope of a folder claudysha has never seen (a folder outside git is scoped by its path)."""
    return str(Path.home() / "new-project")


def session_preview(specs):
    labels = {"claudysha": "Plugin notes"}
    for spec in specs:
        labels.update(spec.get("session_labels", {}))

    def run(query, body):
        scope = (query.get("scope") or "").strip() or None
        target = scope or fresh_scope()
        sections = []
        for command, timeout in session_hooks():
            script = hook_script(command)
            text, error = run_hook(command, timeout, target, Path.home())
            sections.append({"title": labels.get(script, script), "kind": "hook", "source": script,
                             "text": text, "error": error, "tokens": estimate_tokens(text)})
        skills = skill_listing()
        sections.append({"title": "Skill list", "kind": "skills", "source": "skills/*/SKILL.md",
                         "text": skills, "error": "", "tokens": estimate_tokens(skills)})
        scopes = []
        for spec in specs:
            if spec.get("scopes"):
                scopes += spec["scopes"]()
        return {"scope": scope, "shown_scope": target, "sections": sections, "on_demand": skill_bodies(),
                "total": sum(s["tokens"] for s in sections), "scopes": scopes,
                "dev_mode": claudysha.dev_mode()}
    return run


def plugin_spec(specs):
    return {"name": "plugin", "static": WEB, "scripts": [],
            "routes": {("GET", "/api/plugin/overview"): overview(specs),
                       ("POST", "/api/plugin/archive"): make_archive,
                       ("GET", "/api/plugin/load-preview"): preview_load,
                       ("GET", "/api/plugin/session-preview"): session_preview(specs)}}


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
    specs.insert(0, plugin_spec(specs))  # it sees the module specs (cards, scopes, hook labels)
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


# ---------------------------------------------------------------- running it

def state_path():
    return claudysha.state_dir() / "dashboard.json"


def log_path():
    return claudysha.state_dir() / "dashboard.log"


def _alive(pid):
    """Is `pid` a running claudysha dashboard? (Not just any process that reused the pid.)"""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True  # no /proc (macOS): trust the pid
    return b"dashboard" in cmdline


def running():
    """The running dashboard's state ({pid, port, url, started}), or None. Forgets a stale one."""
    try:
        st = json.loads(state_path().read_text())
        pid = int(st["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if _alive(pid):
        return st
    state_path().unlink(missing_ok=True)
    return None


def plugin_version():
    return claudysha.plugin_version()


def already_running(st, out, open_browser):
    """Say a dashboard runs already (and if it's older than this code), and give its link."""
    out(f"claudysha dashboard is already running: {st['url']}")
    if st.get("version") != plugin_version():
        out(f"It runs an older claudysha ({st.get('version') or 'unknown version'}, now {plugin_version()}): "
            "restart it to get the changes (`claudysha dashboard stop`, then start it again).")
    if open_browser:
        open_link(st["url"])


def write_state(srv):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the link is the key
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "port": srv.server_address[1], "url": srv.url, "version": plugin_version(),
                   "started": dt.datetime.now().isoformat(timespec="seconds")}, f)
    tmp.replace(path)


def clear_state():
    with contextlib.suppress(OSError, ValueError, AttributeError):
        if json.loads(state_path().read_text()).get("pid") == os.getpid():
            state_path().unlink()


def open_link(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _stop_signal(signum, frame):
    raise KeyboardInterrupt


def serve(port=DEFAULT_PORT, open_browser=True, verbose=False, out=print):
    """Run the dashboard in this process until Ctrl+C or `claudysha dashboard stop`."""
    st = running()
    if st:
        already_running(st, out, open_browser)
        return
    srv = make_server(port, verbose=verbose)
    write_state(srv)
    signal.signal(signal.SIGTERM, _stop_signal)
    out(f"claudysha dashboard: {srv.url}")
    out("Only this machine can reach it, and only with that link. Ctrl+C (or `claudysha dashboard stop`) stops it.")
    sys.stdout.flush()
    if open_browser:
        open_link(srv.url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        out("\ndashboard stopped")
    finally:
        srv.server_close()
        clear_state()


def detach(port=DEFAULT_PORT, open_browser=True, out=print, wait=10):
    """Start the dashboard in the background (it outlives this terminal) and print its link."""
    st = running()
    if st:
        already_running(st, out, open_browser)
        return st
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(log_path(), "a") as log:
        proc = subprocess.Popen([sys.executable, str(claudysha.REPO / "plugin" / "claudysha.py"), "dashboard",
                                 "--no-open", "--port", str(port)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        st = running()
        if st and st["pid"] == proc.pid:
            out(f"claudysha dashboard: {st['url']}")
            out("Running in the background. `claudysha dashboard status` shows the link again, "
                "`claudysha dashboard stop` stops it.")
            if open_browser:
                open_link(st["url"])
            return st
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    tail = log_path().read_text(errors="replace").splitlines()[-5:] if log_path().exists() else []
    raise SystemExit("the dashboard didn't start" + (":\n" + "\n".join(tail) if tail else ""))


def stop(out=print, wait=5):
    st = running()
    if not st:
        out("claudysha dashboard is not running")
        return False
    os.kill(st["pid"], signal.SIGTERM)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and _alive(st["pid"]):
        time.sleep(0.1)
    if _alive(st["pid"]):
        raise SystemExit(f"the dashboard (pid {st['pid']}) didn't stop")
    state_path().unlink(missing_ok=True)
    out("claudysha dashboard stopped")
    return True


def status(out=print):
    st = running()
    out(f"claudysha dashboard is running since {st['started'].replace('T', ' ')}: {st['url']}" if st
        else "claudysha dashboard is not running")
    return st

#!/usr/bin/env python3
"""claudysha: the plugin itself, shared by every module.

- settings: ~/.config/claudysha/claudysha.conf (see claudysha.conf.example): the data
  folder path and who the user is;
- the plugin's folder (~/.claudysha by default; CLAUDYSHA_HOME overrides it):
    bin/    the claudysha and mem commands (links to the repo), for the user's PATH
    state/  runtime state (the guard pause, which version each session runs)
    archives/  archives made from the dashboard
    data/   the user's data: every module keeps its part here; archived, restored by load,
            and guarded. Older versions kept it directly in the plugin's folder: init()
            moves it into data/ (see migrate());
  and a lock modules take while writing;
- the scope of a session: which repository (or folder) it works in;
- setup and init (each module in MODULES creates its own part of the data folder);
- archive and load of the whole data folder;
- the guard: a PreToolUse hook that keeps sessions out of the data folder and the settings
  file, so they only reach their memory through the modules' commands;
- the dashboard (dashboard.py): a local web page to browse and manage it all;
- dev mode, for people working on claudysha itself: only the user turns it on (in a real
  terminal), and it gives sessions extra commands, like pausing the guard.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_HOME = "~/.claudysha"
DEFAULTS = {"home": DEFAULT_HOME, "user_name": "the user", "user_pronouns": "they/them", "dev_mode": "off"}
DEV_CONFIRM_WORD = "dev"
GUARD_PAUSE_MINUTES = 60
GLOBAL_SCOPE = "global"
ARCHIVE_FORMAT = 1
MANIFEST = "claudysha-archive.json"
CONFIRM_WORD = "erase"


# ---------------------------------------------------------------- config and paths

def config_path():
    """CLAUDYSHA_CONFIG, else $XDG_CONFIG_HOME/claudysha/claudysha.conf (~/.config by default).

    Outside the plugin folder on purpose: an installed plugin may be a cached copy
    that is replaced on every update.
    """
    if os.environ.get("CLAUDYSHA_CONFIG"):
        return Path(os.environ["CLAUDYSHA_CONFIG"]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "claudysha" / "claudysha.conf"


def read_config(path=None):
    """`key = value` lines; `#` starts a comment."""
    conf = {}
    path = Path(path) if path else config_path()
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            key, sep, value = line.partition("=")
            if sep:
                conf[key.strip()] = value.strip()
    return conf


def setting(key):
    return read_config().get(key) or DEFAULTS[key]


def set_setting(key, value):
    """Set `key = value` in the config file, keeping its other lines and comments."""
    path = config_path()
    lines = path.read_text().splitlines() if path.is_file() else []
    for i, line in enumerate(lines):
        k, sep, _ = line.split("#", 1)[0].partition("=")
        if sep and k.strip() == key:
            lines[i] = f"{key} = {value}"
            break
    else:
        lines.append(f"{key} = {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def user_name():
    return setting("user_name")


def user_pronouns():
    return setting("user_pronouns")


def home():
    """The plugin's folder (bin/, state/, data/)."""
    raw = os.environ.get("CLAUDYSHA_HOME") or setting("home")
    return Path(os.path.expandvars(raw)).expanduser()


PLUGIN_ENTRIES = {"bin", "state", "data", "archives"}   # what the plugin's folder holds besides old-layout leftovers
LEGACY_LINKS = ("brain", "memory")          # links left after migrate() for sessions running older code


def legacy_layout():
    """True while the data still sits directly in the plugin's folder (before migrate())."""
    h = home()
    return not (h / "data").exists() and any(
        (h / name).is_dir() and not (h / name).is_symlink() for name in LEGACY_LINKS)


def data_dir():
    """The user's data: what modules write, archive saves and load replaces."""
    return home() if legacy_layout() else home() / "data"


def bin_dir():
    return home() / "bin"


def state_dir():
    return home() / "state"


SESSION_NOTE_DAYS = 14   # how long a session's version note is kept


def plugin_version():
    """The version of the code running now (this copy of the plugin)."""
    try:
        return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text()).get("version", "")
    except (OSError, ValueError):
        return ""


def note_session(stdin_text, prune=False):
    """Write down which version a session runs (state/sessions/<id>), for the user's status line.

    Called by hooks with their stdin: SessionStart, and the guard on every tool call, so a session
    that reloaded its plugins shows the new version after its next tool call. Never fails a hook.
    """
    with contextlib.suppress(Exception):
        sid = str(json.loads(stdin_text or "{}").get("session_id") or "")
        if not re.fullmatch(r"[\w-]{1,128}", sid):
            return
        folder = state_dir() / "sessions"
        note, version = folder / sid, plugin_version() or "?"
        if not note.exists() or note.read_text() != version:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f".{sid}.tmp"
            tmp.write_text(version)
            tmp.replace(note)
        if prune:
            cutoff = time.time() - SESSION_NOTE_DAYS * 86400
            for f in folder.iterdir():
                if f.stat().st_mtime < cutoff:
                    f.unlink()


@contextlib.contextmanager
def home_lock(exclusive=False, wait=True):
    """Shared for normal writers (extraction), exclusive for `load` and migrate().

    With wait=False, raises BlockingIOError instead of waiting when the lock is taken.
    (Next to the folder, where older versions take it too.)
    """
    h = home()
    h.parent.mkdir(parents=True, exist_ok=True)
    lock_path = h.parent / f".{h.name.lstrip('.')}.lock"
    with open(lock_path, "w") as f:
        fcntl.flock(f, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if wait else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- scope

def _git(folder, *args):
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def repo_name(url):
    """'https://host/owner/name.git' or 'git@host:owner/name.git' -> 'name'."""
    name = url.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def scope_of(folder):
    """The scope of a session working in `folder`.

    Inside a git repository: the repository's name, from its `origin` remote (so a clone
    elsewhere keeps the same scope), else the name of its top folder. Outside one: the
    folder's absolute path. Never GLOBAL_SCOPE, which is for what holds in every session.
    """
    folder = Path(folder).expanduser().resolve()
    top = _git(folder, "rev-parse", "--show-toplevel") if folder.is_dir() else None
    if not top:
        return str(folder)
    url = _git(top, "remote", "get-url", "origin")
    return (repo_name(url) if url else "") or Path(top).name


def current_scope():
    """Scope of the running session: CLAUDE_PROJECT_DIR (set by Claude Code), else the current folder."""
    return scope_of(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


# ---------------------------------------------------------------- modules

# Each module has a Python file exposing init(): create the module's part of the data
# folder, never overwriting existing files.
MODULES = {"memory": REPO / "memory" / "core.py"}


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"claudysha_{name}", MODULES[name])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- setup and init

SETUP_QUESTIONS = [
    ("home", "Where should claudysha keep its folder (your data, its commands)?", DEFAULT_HOME),
    ("user_name", "What name should Claude use for you?", None),
    ("user_pronouns", "Your pronouns (used in the memory prompts)?", "they/them"),
]


def render_config(values):
    """Config file text for these settings (missing keys get the defaults)."""
    v = {**DEFAULTS, **{k: x for k, x in values.items() if x is not None}}
    return (
        "# claudysha settings. Lines are `key = value`; `#` starts a comment.\n"
        "# Written by install.sh; edit freely. Reference: claudysha.conf.example in the plugin.\n\n"
        "# The plugin's folder: its commands (bin/) and your data (data/), see the README.\n"
        f"home = {v['home']}\n\n"
        "# Who Claude works with. Used by the modules (prompts, session notes).\n"
        f"user_name = {v['user_name']}\n"
        f"user_pronouns = {v['user_pronouns']}\n"
    )


def interactive_setup(ask, out=print):
    """Ask the setup questions; `ask(prompt)` returns the answer ('' keeps the default)."""
    out("No claudysha config yet: a few questions (press Enter to keep the default in brackets).")
    values = {}
    for key, question, default in SETUP_QUESTIONS:
        default = default if default is not None else os.environ.get("USER", "the user")
        hint = f" [{default}]" if default else ""
        answer = ask(f"{question}{hint} ").strip()
        values[key] = answer or default
    return values


def ensure_config(ask=None, out=print):
    """Write the config if it's missing: from answers when `ask` is given, else from the defaults.

    Returns True when a config was written.
    """
    conf = config_path()
    if conf.exists():
        return False
    values = interactive_setup(ask, out) if ask else {}
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(render_config(values))
    out(f"Wrote {conf}" + ("" if ask else " with default settings: edit it to set your name and pronouns."))
    return True


def migrate(wait=True):
    """Move an old layout's data (directly in the plugin's folder) into data/.

    Under the exclusive lock, so no extraction writes meanwhile; with wait=False (session start)
    it gives up when the lock is taken, and the data stays usable where it is (data_dir() follows
    it) until a later try. Leaves links brain -> data/brain and memory -> data/memory for
    sessions still running older code. Returns True if it moved something.
    """
    if not legacy_layout():
        return False
    h = home()
    try:
        with home_lock(exclusive=True, wait=wait):
            if not legacy_layout():
                return False
            staging = h / ".data-migrating"
            staging.mkdir(exist_ok=True)
            for entry in sorted(h.iterdir()):
                if entry.name not in PLUGIN_ENTRIES and entry != staging and not entry.name.startswith(".data-"):
                    entry.rename(staging / entry.name)
            staging.rename(h / "data")
            for name in LEGACY_LINKS:
                if (h / "data" / name).exists() and not (h / name).exists():
                    (h / name).symlink_to(Path("data") / name)
    except BlockingIOError:
        return False
    old_pause = h.parent / f".{h.name.lstrip('.')}.guard-paused-until"
    old_pause.unlink(missing_ok=True)
    return True


def init(wait=True):
    """Create the plugin's folder and the data folder, and let every module create its part.

    Never overwrites files. Migrates an old layout first (see migrate()).
    """
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    migrate(wait=wait)
    data_dir().mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        load_module(name).init()
    return h


# ---------------------------------------------------------------- guard

# Like a brain: a session can remember (through the modules' commands) but never opens its
# own memory. Path tools are checked by resolving their path; shell commands by looking for
# the folder's path in the text, which catches mistakes, not someone determined to get in.
GUARDED_TOOLS = {"Read": ("file_path",), "Edit": ("file_path",), "MultiEdit": ("file_path",),
                 "Write": ("file_path",), "NotebookEdit": ("notebook_path",), "Grep": ("path",),
                 "Glob": ("path", "pattern"), "LS": ("path",)}
GUARD_REASON = ("That is where claudysha keeps its memory, which sessions never open directly. "
                "Use the memory commands instead (see the claudysha:recall skill).")


def dev_mode():
    return setting("dev_mode").lower() in ("on", "yes", "true", "1")


def guard_pause_path():
    """In state/ (not archived). Writing it by hand does nothing without dev mode."""
    return state_dir() / "guard-paused-until"


def guard_paused_until():
    """When the guard comes back on, if dev mode is on and the guard is paused; else None."""
    if not dev_mode():
        return None
    try:
        until = dt.datetime.fromisoformat(guard_pause_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return until if until > dt.datetime.now() else None


def pause_guard(minutes):
    if not dev_mode():
        raise SystemExit("The guard can only be paused in dev mode, which only the user can turn on "
                         "(`claudysha dev on`, in their own terminal).")
    until = dt.datetime.now() + dt.timedelta(minutes=minutes)
    guard_pause_path().parent.mkdir(parents=True, exist_ok=True)
    guard_pause_path().write_text(until.isoformat(timespec="seconds"))
    return until


def resume_guard():
    with contextlib.suppress(FileNotFoundError):
        guard_pause_path().unlink()


def guarded_paths():
    """(guarded, open): the plugin's folder but its bin/ and state/, and the settings file
    once it exists (install may still create it)."""
    guarded = [home()]
    if config_path().exists():
        guarded.append(config_path())
    return guarded, [bin_dir(), state_dir()]


def _inside(path, roots):
    for root in roots:
        for r in {root, root.resolve()}:
            if path == r or r in path.parents:
                return True
    return False


def _spellings(path):
    """How a shell command may spell `path`: absolute (as given and resolved), ~/..., $HOME/..."""
    out = {str(path), str(path.resolve())}
    for p in list(out):
        with contextlib.suppress(ValueError):
            rel = Path(p).relative_to(Path.home())
            out |= {f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"}
    return out


BOUNDARY = r"(?=$|[/\s\"'`;|&)<>])"


def _bash_touches(cmd, guarded, open_):
    """Does the command text name a guarded path (not one of the open ones inside it)?"""
    for root in guarded:
        for spelling in _spellings(root):
            for m in re.finditer(re.escape(spelling) + BOUNDARY, cmd):
                rest = cmd[m.end():]
                if not any(re.match(re.escape("/" + o.name) + BOUNDARY, rest)
                           for o in open_ if o.parent == root):
                    return True
    return False


def guard_denies(event):
    """True if this PreToolUse event (the hook JSON) touches a guarded path."""
    if guard_paused_until():
        return False
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    guarded, open_ = guarded_paths()
    if tool == "Bash":
        return _bash_touches(str(args.get("command", "")), guarded, open_)
    keys = GUARDED_TOOLS.get(tool)
    if not keys:
        return False
    for key in keys:
        raw = args.get(key)
        if not raw:
            continue
        raw = str(raw)
        if key == "pattern":  # a glob: check the part before the first wildcard
            raw = re.split(r"[*?\[{]", raw, maxsplit=1)[0] or "."
        path = Path(os.path.expandvars(raw)).expanduser()
        path = Path(os.path.normpath(path if path.is_absolute() else cwd / path))
        if _inside(path, guarded) and not _inside(path, open_):
            return True
    # Grep or Glob without a path search the session's folder.
    return (tool in ("Grep", "Glob") and not args.get("path")
            and _inside(cwd, guarded) and not _inside(cwd, open_))


def guard_hook(stdin_text):
    """PreToolUse hook: the JSON that denies the call, or "" to let it through. Never fails closed."""
    try:
        if not guard_denies(json.loads(stdin_text)):
            return ""
    except Exception:
        return ""
    reason = GUARD_REASON
    if dev_mode():
        reason += (f" (Dev mode is on: if this work really needs it, `{REPO / 'claudysha'} guard off` "
                   "pauses the guard; turn it back on with `guard on` when done.)")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


# ---------------------------------------------------------------- dev mode

def set_dev_mode(on, confirm=None, out=print):
    """Turn dev mode on (only with the user's typed confirmation, see dev_confirm) or off."""
    if not on:
        set_setting("dev_mode", "off")
        resume_guard()
        out("dev mode off: the guard is on for every session")
        return
    if dev_mode():
        out("dev mode is already on")
        return
    out("Dev mode is for working on claudysha itself. It lets Claude sessions pause the guard and\n"
        "open claudysha's memory files directly. Turn it off with `claudysha dev off`.")
    if confirm(f"Type '{DEV_CONFIRM_WORD}' to turn it on: ").strip() != DEV_CONFIRM_WORD:
        raise SystemExit("not confirmed: dev mode stays off")
    set_setting("dev_mode", "on")
    out("dev mode on: new sessions get the dev commands")


def dev_confirm(prompt):
    if not sys.stdin.isatty():
        raise SystemExit("Only the user can turn dev mode on: they run `claudysha dev on` in their own terminal.")
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def dev_session_text():
    """What sessions are told while dev mode is on."""
    c = REPO / "claudysha"
    paused = guard_paused_until()
    state = f"paused until {paused:%H:%M}" if paused else "on"
    return (f"[claudysha] Dev mode is on: {user_name()} works on claudysha itself. The guard is {state}. "
            f"Dev commands: `{c} guard off [--minutes N]` pauses the guard (default {GUARD_PAUSE_MINUTES} min) "
            f"so you can open claudysha's data folder (`{c} config` shows where) and settings file; `{c} guard on` turns it "
            "back on. Pause it only when the work needs it, say so, and turn it back on when done. "
            "Never turn dev mode on or off yourself.")


# ---------------------------------------------------------------- archive

def archive(dest=None):
    """Write a .tar.gz of the data folder. Returns its path."""
    h = data_dir()
    if not h.is_dir():
        raise SystemExit(f"no claudysha data at {h}")
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = Path(dest).expanduser() if dest else Path.cwd() / f"claudysha-{stamp}.tar.gz"
    if dest.is_dir():
        dest = dest / f"claudysha-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        manifest = {"format": ARCHIVE_FORMAT, "created": dt.datetime.now().isoformat(timespec="seconds"),
                    "home": str(h)}
        Path(tmp, MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(Path(tmp, MANIFEST), arcname=MANIFEST)
            for path in sorted(h.rglob("*")):
                rel = path.relative_to(h)
                if h == home() and rel.parts[0] in PLUGIN_ENTRIES:
                    continue  # old layout: the data is home itself, minus the plugin's folders
                if path.is_symlink() or path.is_dir() or path.name.endswith((".db-wal", ".db-shm")) or path.name.startswith(".ingest-"):
                    continue
                if path.suffix == ".db":
                    # A consistent copy even while extraction writes to it.
                    copy = Path(tmp, path.name)
                    src = sqlite3.connect(path)
                    dst = sqlite3.connect(copy)
                    with dst:
                        src.backup(dst)
                    src.close()
                    dst.close()
                    tar.add(copy, arcname=str(rel))
                else:
                    tar.add(path, arcname=str(rel), recursive=False)
    return dest


# ---------------------------------------------------------------- load

def read_archive(path):
    """Validate an archive and return (manifest, member names)."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise SystemExit(f"no such archive: {path}")
    with tarfile.open(path, "r:gz") as tar:
        try:
            manifest = json.load(tar.extractfile(MANIFEST))
        except (KeyError, json.JSONDecodeError, TypeError):
            raise SystemExit(f"{path} is not a claudysha archive (no {MANIFEST})")
        names = []
        for m in tar.getmembers():
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                raise SystemExit(f"refusing archive: unsafe entry {m.name!r}")
            if m.name != MANIFEST and m.isfile():
                names.append(m.name)
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise SystemExit(f"unsupported archive format {manifest.get('format')!r}")
    return manifest, names


def plan_load(path):
    """What loading would do: (manifest, files in archive, current files that would be erased)."""
    manifest, names = read_archive(path)
    h = data_dir()
    current = set()
    if h.is_dir():
        current = {str(p.relative_to(h)) for p in h.rglob("*")
                   if p.is_file() and not p.name.endswith((".db-wal", ".db-shm"))}
    return manifest, names, sorted(current - set(names))


def load(path, confirm, out=print):
    """Replace the home with the archive's content, after explicit confirmation.

    `confirm(prompt)` must return the confirmation word; anything else aborts.
    The current home is archived next to it first.
    """
    path = Path(path).expanduser()
    migrate()  # an old layout first, so the load replaces data/ only
    manifest, names, erased = plan_load(path)
    h = data_dir()
    out(f"Archive: {path} (created {manifest.get('created')}, {len(names)} files)")
    out(f"Data:    {h}")
    out(f"Loading REPLACES all of claudysha's data with the archive. {len(erased)} current file(s) are not in it"
        " and will be erased" + (":" if erased else "."))
    for name in erased[:30]:
        out(f"  - {name}")
    if len(erased) > 30:
        out(f"  ... and {len(erased) - 30} more")
    if confirm(f"Type '{CONFIRM_WORD}' to replace everything, anything else to cancel: ").strip() != CONFIRM_WORD:
        out("Cancelled, nothing changed.")
        return None
    with home_lock(exclusive=True):
        safety = None
        if h.is_dir() and any(h.iterdir()):
            stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            safety = archive(h.parent / f"{h.name}-before-load-{stamp}.tar.gz")
        staging = Path(tempfile.mkdtemp(prefix=f".{h.name.lstrip('.')}-load-", dir=h.parent))
        try:
            with tarfile.open(path, "r:gz") as tar:
                tar.extractall(staging, filter="data")
            (staging / MANIFEST).unlink(missing_ok=True)
            old = None
            if h.exists():
                old = h.parent / f".{h.name.lstrip('.')}-old-{os.getpid()}"
                h.rename(old)
            staging.rename(h)
            if old:
                shutil.rmtree(old)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    out(f"Loaded. The previous home was saved to {safety}." if safety else "Loaded.")
    return safety


# ---------------------------------------------------------------- commands

def tty_confirm(prompt):
    if not sys.stdin.isatty():
        raise SystemExit("`claudysha load` needs the user's own confirmation in a real terminal: "
                         "the user runs it in their own terminal.")
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def tty_input(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit("setup cancelled, nothing written")


def main():
    p = argparse.ArgumentParser(prog="claudysha", description="claudysha: settings and folders, archive and "
                                "load of your data, dev mode, and the guard. Memory commands are in `mem`.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("home", help="print the plugin's folder (bin/, state/, data/)")
    sub.add_parser("config", help="print the config file path and the settings in effect")
    sub.add_parser("init", help="create the folders (keeps existing files)")
    sub.add_parser("setup", help="write the config if missing (asks questions in a terminal), then init")
    s = sub.add_parser("archive", help="write a .tar.gz of your data (data/)")
    s.add_argument("dest", nargs="?", help="file or folder (default: ./claudysha-<date>.tar.gz)")
    s = sub.add_parser("load", help="replace your data with an archive (you type erase to confirm)")
    s.add_argument("archive")
    sub.add_parser("session-start", help="for the SessionStart hook: make sure the data folder exists")
    s = sub.add_parser("guard", help="the guard: check (PreToolUse hook), off/on (dev mode only), status")
    s.add_argument("action", choices=["check", "off", "on", "status"])
    s.add_argument("--minutes", type=int, default=GUARD_PAUSE_MINUTES, help="how long `off` lasts")
    s = sub.add_parser("dashboard", help="open the dashboard: a local web page to review and browse "
                       "memories, preview what sessions receive, see settings and archives")
    s.add_argument("action", nargs="?", choices=["stop", "status"],
                   help="stop: stop the running dashboard; status: show its link")
    s.add_argument("-d", "--detach", action="store_true", help="run it in the background")
    s.add_argument("-p", "--port", type=int, default=8765, help="port on 127.0.0.1 (another one if taken)")
    s.add_argument("--no-open", action="store_true", help="print the link without opening the browser")
    s.add_argument("-v", "--verbose", action="store_true", help="log each request")
    s = sub.add_parser("dev", help="dev mode, for working on claudysha itself (`on` asks you to confirm)")
    s.add_argument("action", choices=["on", "off", "status"])
    if len(sys.argv) == 1:
        p.print_help()
        return
    a = p.parse_args()

    if a.cmd == "home":
        print(home())
    elif a.cmd == "config":
        print(f"config: {config_path()}" + ("" if config_path().exists() else " (missing: defaults)"))
        print(f"data:   {data_dir()}")
        print(f"bin:    {bin_dir()}")
        for key in DEFAULTS:
            print(f"  {key} = {home() if key == 'home' else setting(key)}")
    elif a.cmd == "init":
        print(f"claudysha home ready: {init()}")
    elif a.cmd == "setup":
        ensure_config(ask=tty_input if sys.stdin.isatty() else None)
        print(f"claudysha home ready: {init()}")
    elif a.cmd == "archive":
        print(archive(a.dest))
    elif a.cmd == "load":
        load(a.archive, tty_confirm)
    elif a.cmd == "guard":
        if a.action == "check":
            text = sys.stdin.read()
            note_session(text)
            out = guard_hook(text)
            if out:
                print(out)
        elif a.action == "off":
            print(f"guard paused until {pause_guard(a.minutes):%H:%M} (`claudysha guard on` to end it sooner)")
        elif a.action == "on":
            resume_guard()
            print("guard on")
        else:
            paused = guard_paused_until()
            print(f"guard paused until {paused:%H:%M}" if paused else "guard on")
    elif a.cmd == "dashboard":
        import dashboard
        if a.action == "stop":
            dashboard.stop()
        elif a.action == "status":
            dashboard.status()
        elif a.detach:
            init(wait=False)
            dashboard.detach(a.port, open_browser=not a.no_open)
        else:
            init(wait=False)
            dashboard.serve(a.port, open_browser=not a.no_open, verbose=a.verbose)
    elif a.cmd == "dev":
        if a.action == "status":
            print(f"dev mode {'on' if dev_mode() else 'off'}")
        else:
            set_dev_mode(a.action == "on", dev_confirm)
    elif a.cmd == "session-start":
        if os.environ.get("MEM_NO_HOOK"):
            return
        text = ""
        with contextlib.suppress(Exception):
            if not sys.stdin.isatty():
                text = sys.stdin.read()
        init(wait=False)  # a plugin installed from GitHub has no install.sh run: create the folders here
        note_session(text, prune=True)
        if dev_mode():
            print(dev_session_text())
        if not config_path().exists():
            print(f"[claudysha] No settings file yet ({config_path()}). Suggest once that the user runs "
                  f"`{REPO / 'claudysha'} setup` in a terminal to set their name, pronouns and data folder.")


if __name__ == "__main__":
    main()

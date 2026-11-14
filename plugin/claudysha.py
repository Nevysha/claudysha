#!/usr/bin/env python3
"""claudysha: the plugin itself, shared by every module.

- settings: ~/.config/claudysha/claudysha.conf (see claudysha.conf.example): the data
  folder path and who the user is;
- the data folder (~/.claudysha by default; CLAUDYSHA_HOME overrides it), where every
  module keeps its data, and a lock modules take while writing;
- the scope of a session: which repository (or folder) it works in;
- setup and init (each module in MODULES creates its own part of the data folder);
- archive and load of the whole data folder;
- the guard: a PreToolUse hook that keeps sessions out of the data folder and the settings
  file, so they only reach their memory through the modules' commands;
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
    raw = os.environ.get("CLAUDYSHA_HOME") or setting("home")
    return Path(os.path.expandvars(raw)).expanduser()


@contextlib.contextmanager
def home_lock(exclusive=False):
    """Shared for normal writers (extraction), exclusive for `load`."""
    h = home()
    h.parent.mkdir(parents=True, exist_ok=True)
    lock_path = h.parent / f".{h.name.lstrip('.')}.lock"
    with open(lock_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
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
    ("home", "Where should claudysha keep its data (notes, memory database, backups)?", DEFAULT_HOME),
    ("user_name", "What name should Claude use for you?", None),
    ("user_pronouns", "Your pronouns (used in the memory prompts)?", "they/them"),
]


def render_config(values):
    """Config file text for these settings (missing keys get the defaults)."""
    v = {**DEFAULTS, **{k: x for k, x in values.items() if x is not None}}
    return (
        "# claudysha settings. Lines are `key = value`; `#` starts a comment.\n"
        "# Written by install.sh; edit freely. Reference: claudysha.conf.example in the plugin.\n\n"
        "# The data folder, where every module keeps its data (see the README).\n"
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


def init():
    """Create the data folder and let every module create its part. Never overwrites files."""
    h = home()
    h.mkdir(parents=True, exist_ok=True)
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
    """Outside the data folder (not archived). Writing it by hand does nothing without dev mode."""
    h = home()
    return h.parent / f".{h.name.lstrip('.')}.guard-paused-until"


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
    guard_pause_path().write_text(until.isoformat(timespec="seconds"))
    return until


def resume_guard():
    with contextlib.suppress(FileNotFoundError):
        guard_pause_path().unlink()


def guarded_paths():
    """The data folder, and the settings file once it exists (install may still create it)."""
    paths = [home()]
    if config_path().exists():
        paths.append(config_path())
    return paths


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


def guard_denies(event):
    """True if this PreToolUse event (the hook JSON) touches a guarded path."""
    if guard_paused_until():
        return False
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    roots = guarded_paths()
    if tool == "Bash":
        cmd = str(args.get("command", ""))
        return any(re.search(re.escape(s) + r"(?=$|[/\s\"'`;|&)<>])", cmd)
                   for root in roots for s in _spellings(root))
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
        if _inside(path, roots):
            return True
    # Grep or Glob without a path search the session's folder.
    return tool in ("Grep", "Glob") and not args.get("path") and _inside(cwd, roots)


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
            f"so you can open claudysha's data folder (`{c} home`) and settings file; `{c} guard on` turns it "
            "back on. Pause it only when the work needs it, say so, and turn it back on when done. "
            "Never turn dev mode on or off yourself.")


# ---------------------------------------------------------------- archive

def archive(dest=None):
    """Write a .tar.gz of the whole home. Returns its path."""
    h = home()
    if not h.is_dir():
        raise SystemExit(f"no claudysha home at {h}")
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
                if path.is_dir() or path.name.endswith((".db-wal", ".db-shm")) or path.name.startswith(".ingest-"):
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
    h = home()
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
    manifest, names, erased = plan_load(path)
    h = home()
    out(f"Archive: {path} (created {manifest.get('created')}, {len(names)} files)")
    out(f"Home:    {h}")
    out(f"Loading REPLACES the whole home with the archive. {len(erased)} current file(s) are not in it"
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
    p = argparse.ArgumentParser(prog="claudysha", description="claudysha plugin home: paths, archive, load.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("home", help="print the home folder path")
    sub.add_parser("config", help="print the config file path and the settings in effect")
    sub.add_parser("init", help="create the home folder layout (keeps existing files)")
    sub.add_parser("setup", help="write the config if missing (asks questions in a terminal), then init")
    s = sub.add_parser("archive", help="write a .tar.gz of the whole home")
    s.add_argument("dest", nargs="?", help="file or folder (default: ./claudysha-<date>.tar.gz)")
    s = sub.add_parser("load", help="replace the whole home with an archive (asks for confirmation)")
    s.add_argument("archive")
    sub.add_parser("session-start", help="for the SessionStart hook: make sure the data folder exists")
    s = sub.add_parser("guard", help="the guard: check (PreToolUse hook), off/on (dev mode only), status")
    s.add_argument("action", choices=["check", "off", "on", "status"])
    s.add_argument("--minutes", type=int, default=GUARD_PAUSE_MINUTES, help="how long `off` lasts")
    s = sub.add_parser("dev", help="dev mode, for working on claudysha itself (`on` asks you to confirm)")
    s.add_argument("action", choices=["on", "off", "status"])
    a = p.parse_args()

    if a.cmd == "home":
        print(home())
    elif a.cmd == "config":
        print(f"config: {config_path()}" + ("" if config_path().exists() else " (missing: defaults)"))
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
            out = guard_hook(sys.stdin.read())
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
    elif a.cmd == "dev":
        if a.action == "status":
            print(f"dev mode {'on' if dev_mode() else 'off'}")
        else:
            set_dev_mode(a.action == "on", dev_confirm)
    elif a.cmd == "session-start":
        if os.environ.get("MEM_NO_HOOK"):
            return
        with contextlib.suppress(Exception):
            if not sys.stdin.isatty():
                sys.stdin.read()
        init()  # a plugin installed from GitHub has no install.sh run: create the data folder here
        if dev_mode():
            print(dev_session_text())
        if not config_path().exists():
            print(f"[claudysha] No settings file yet ({config_path()}). Suggest once that the user runs "
                  f"`{REPO / 'claudysha'} setup` in a terminal to set their name, pronouns and data folder.")


if __name__ == "__main__":
    main()

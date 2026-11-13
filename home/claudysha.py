#!/usr/bin/env python3
"""claudysha: the plugin's home folder (config, init, archive, load) and the core-memory hook.

The home folder holds everything the plugin keeps about its user:
    <home>/brain/    core.md, memories/ (topic notes), maybe-later/ (backlog): its own git repo
    <home>/memory/   memory.db (tier-1 store) and extract.log
Settings come from ~/.config/claudysha/claudysha.conf (see claudysha.conf.example):
the home path (default ~/.claudysha, CLAUDYSHA_HOME overrides it) and who the user is.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_HOME = "~/.claudysha"
DEFAULTS = {"home": DEFAULT_HOME, "user_name": "the user", "user_pronouns": "they/them", "projects": ""}
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


def user_name():
    return setting("user_name")


def user_pronouns():
    return setting("user_pronouns")


def projects():
    """Projects the extractor should prefer when filing memories."""
    return [p.strip() for p in setting("projects").split(",") if p.strip()]


def home():
    raw = os.environ.get("CLAUDYSHA_HOME") or setting("home")
    return Path(os.path.expandvars(raw)).expanduser()


def brain_dir():
    return home() / "brain"


def memory_dir():
    return home() / "memory"


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


# ---------------------------------------------------------------- init

CORE_TEMPLATE = """# Who I work with, and how

Loaded into every Claude Code session by the claudysha plugin. Keep it short: only what should
shape every session. Changes need the user's OK.
"""

INDEX_TEMPLATE = "Topic notes, in the brain folder's `memories/` (read the ones that matter for the task):\n"

BACKLOG_README = """# maybe-later

Postponed ideas and recommendations, one note per topic. Each note starts with:

```yaml
---
status: open          # open | waiting (on the user or something external) | someday | done
created: 2026-11-13
review-after: 2026-11-27   # empty = no date
summary: one line
---
```

The claudysha plugin's session start lists the notes whose `review-after` date has passed
(status open or waiting). When a note is done, set `status: done` (or delete it; git keeps it).
"""


SETUP_QUESTIONS = [
    ("home", "Where should claudysha keep its data (notes, memory database, backups)?", DEFAULT_HOME),
    ("user_name", "What name should Claude use for you?", None),
    ("user_pronouns", "Your pronouns (used in the memory prompts)?", "they/them"),
    ("projects", "Projects you work on, comma-separated, to file memories under (optional)?", ""),
]


def render_config(values):
    """Config file text for these settings (missing keys get the defaults)."""
    v = {**DEFAULTS, **{k: x for k, x in values.items() if x is not None}}
    return (
        "# claudysha settings. Lines are `key = value`; `#` starts a comment.\n"
        "# Written by install.sh; edit freely. Reference: claudysha.conf.example in the plugin.\n\n"
        "# Where the plugin keeps its data: brain/ (core.md, notes, backlog), memory/ (database), backups/.\n"
        f"home = {v['home']}\n\n"
        "# Who Claude works with. Used by the memory extraction prompts and the session notes.\n"
        f"user_name = {v['user_name']}\n"
        f"user_pronouns = {v['user_pronouns']}\n\n"
        "# Projects the extractor should prefer when filing memories, comma-separated (optional).\n"
        f"projects = {v['projects']}\n"
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
    """Create the home layout if missing. Never overwrites files. (The config: ensure_config.)"""
    b, m = brain_dir(), memory_dir()
    for d in (b / "memories", b / "maybe-later", m):
        d.mkdir(parents=True, exist_ok=True)
    for path, text in ((b / "core.md", CORE_TEMPLATE), (b / "memories" / "MEMORY.md", INDEX_TEMPLATE),
                       (b / "maybe-later" / "README.md", BACKLOG_README)):
        if not path.exists():
            path.write_text(text)
    if not (b / ".git").exists() and shutil.which("git"):
        subprocess.run(["git", "init", "-q", "-b", "main", str(b)], check=True)
    return home()


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


# ---------------------------------------------------------------- session start

def session_start_text():
    """core.md and the topic index, as context for every session."""
    b = brain_dir()
    parts = []
    core = b / "core.md"
    if core.is_file():
        parts.append(core.read_text().strip())
    index = b / "memories" / "MEMORY.md"
    if index.is_file():
        parts.append(f"## Topic notes (in {b / 'memories'})\n\n" + index.read_text().strip())
    if not parts:
        return ""
    return (f"[claudysha] You work with {user_name()} ({user_pronouns()}). Core memory from {b} "
            f"(tier 2; edit those files only with {user_name()}'s OK):\n\n" + "\n\n".join(parts))


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
    sub.add_parser("session-start", help="for the SessionStart hook: print core.md and the topic index")
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
    elif a.cmd == "session-start":
        if os.environ.get("MEM_NO_HOOK"):
            return
        with contextlib.suppress(Exception):
            if not sys.stdin.isatty():
                sys.stdin.read()
        init()  # a plugin installed from GitHub has no install.sh run: create the data folder here
        if not config_path().exists():
            print(f"[claudysha] No settings file yet ({config_path()}). Suggest once that the user runs "
                  f"`{REPO / 'claudysha'} setup` in a terminal to set their name, pronouns and data folder.")
        text = session_start_text()
        if text:
            print(text)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Memory module: its folders in the data folder, and core memory.

Data, inside the plugin's data folder:
    brain/     hand-written tier 2, its own git repo:
                 core.md       who the user is, how to work with them
                 maybe-later/  backlog of postponed ideas (see backlog.py)
    memory/    tier-1 store: memory.db and extract.log (see store/mem.py)

Core memory: at session start, core.md is printed into the session. The session text talks about
"core memory" and points to the claudysha:recall skill, never to files: a user who doesn't know the
plugin's internals must still understand it. (Older homes may have brain/memories/, the retired topic
notes: it is left alone, and kept in archives, but no longer created or loaded.)
"""
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin"))
import claudysha  # noqa: E402  (the plugin: settings, data folder)


def brain_dir():
    return claudysha.home() / "brain"


def store_dir():
    return claudysha.home() / "memory"


CORE_TEMPLATE = """# Who I work with, and how

Loaded into every Claude Code session by the claudysha plugin. Keep it short: only what should
shape every session. Changes need the user's OK. Facts learned in sessions go in the long-term
memory store, not here.
"""

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


def init():
    """Create the memory module's folders and starter files. Never overwrites files."""
    b, m = brain_dir(), store_dir()
    for d in (b / "maybe-later", m):
        d.mkdir(parents=True, exist_ok=True)
    for path, text in ((b / "core.md", CORE_TEMPLATE), (b / "maybe-later" / "README.md", BACKLOG_README)):
        if not path.exists():
            path.write_text(text)
    if not (b / ".git").exists() and shutil.which("git"):
        subprocess.run(["git", "init", "-q", "-b", "main", str(b)], check=True)


def session_start_text():
    """Core memory, as context for every session (no file paths: see the module docstring)."""
    core = brain_dir() / "core.md"
    text = core.read_text().strip() if core.is_file() else ""
    if not text:
        return ""
    name, pronouns = claudysha.user_name(), claudysha.user_pronouns()
    return (f"[claudysha] You work with {name} ({pronouns}). Here is {name}'s core memory: who they are and "
            f"how they want you to work with them. It applies to every session. Change it only with "
            f"{name}'s OK (the claudysha:recall skill explains how).\n\n" + text)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        init()
    elif cmd == "session-start":
        if os.environ.get("MEM_NO_HOOK"):
            return
        with contextlib.suppress(Exception):
            if not sys.stdin.isatty():
                sys.stdin.read()
        text = session_start_text()
        if text:
            print(text)
    else:
        raise SystemExit("usage: core.py init | session-start")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Memory module: its folders in the data folder, and core memory.

Data, inside the plugin's data folder (claudysha.data_dir()):
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
    return claudysha.data_dir() / "brain"


def store_dir():
    return claudysha.data_dir() / "memory"


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


def dashboard():
    """The memory module's dashboard panels (plugin/dashboard.py calls this; see memory/panel.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("claudysha_memory_panel", Path(__file__).resolve().parent / "panel.py")
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)
    return panel.spec()


def core_text():
    """Core memory's text, or "" when there is none."""
    core = brain_dir() / "core.md"
    return core.read_text().strip() if core.is_file() else ""


def session_start_text():
    """Core memory, as context for every session (no file paths: see the module docstring)."""
    text = core_text()
    if not text:
        return ""
    name, pronouns = claudysha.user_name(), claudysha.user_pronouns()
    return (f"[claudysha] You work with {name} ({pronouns}). Here is {name}'s core memory: who {name} is and "
            f"how {name} wants you to work together. It applies to every session. Change it only with "
            f"{name}'s OK (the claudysha:recall skill explains how).\n\n" + text)


# ---------------------------------------------------------------- commands
# Sessions change core memory only through these (the plugin's guard keeps them out of the
# files). Each change is committed in the brain folder's git repo, so it has a history.

def commit(paths, message):
    """Commit `paths` (in the brain folder) if it is a git repo. Returns True if committed."""
    b = brain_dir()
    if not (b / ".git").exists() or not shutil.which("git"):
        return False
    try:
        rel = [str(Path(p).resolve().relative_to(b.resolve())) for p in paths]
    except ValueError:  # not in the brain folder: nothing to commit there
        return False
    git = ["git", "-C", str(b)]
    subprocess.run([*git, "add", "-A", "--", *rel], capture_output=True)
    done = subprocess.run([*git, "commit", "-q", "-m", message, "--", *rel], capture_output=True)
    return done.returncode == 0


def _diff(before, after):
    import difflib
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=1)
    return "\n".join(list(lines)[2:])


def _sections(text):
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def add_line(text, line, section=None):
    """Append `line` at the end of `section` (a `## ` heading), or of the whole text."""
    lines = text.rstrip("\n").splitlines()
    if section is None:
        return "\n".join(lines + [line]) + "\n"
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("## ") and l[3:].strip().lower() == section.strip().lower()), None)
    if start is None:
        raise ValueError(f"no section {section!r}; sections: " + ", ".join(_sections(text)))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + [line] + lines[end:]) + "\n"


def replace_once(text, old, new):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"the text to replace appears {n} times; it must appear exactly once "
                         "(give more of it to make it unique)")
    return text.replace(old, new)


def remove_line(text, part):
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if part in l]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} lines contain that text; it must match exactly one")
    return "\n".join(lines[:hits[0]] + lines[hits[0] + 1:]) + "\n"


def change_core(fn, message):
    """Apply fn(text) -> new text to core memory, commit, and return the diff."""
    path = brain_dir() / "core.md"
    with claudysha.home_lock():
        before = path.read_text() if path.is_file() else ""
        after = fn(before)
        if after == before:
            return ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after)
        commit([path], message)
    return _diff(before, after)


def cli(argv=None, prog="mem core"):
    """`mem core ...`: read and change core memory."""
    import argparse
    p = argparse.ArgumentParser(prog=prog, description="Core memory: who the user is and how to work "
                                "together, shown at the start of every session. Change it only with the user's OK.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print core memory")
    s = sub.add_parser("add", help="add a line, at the end of a section or of the whole")
    s.add_argument("line")
    s.add_argument("--section", help="a section title, e.g. 'Working together'")
    s = sub.add_parser("edit", help="replace a passage (it must appear exactly once)")
    s.add_argument("--old", required=True)
    s.add_argument("--new", required=True)
    s = sub.add_parser("remove", help="remove the one line that contains this text")
    s.add_argument("text")
    for s in sub.choices.values():
        if s.prog.split()[-1] != "show":
            s.add_argument("-m", "--message", help="what changed, for the history")
    a = p.parse_args(argv)
    if a.cmd == "show":
        path = brain_dir() / "core.md"
        print(path.read_text().rstrip() if path.is_file() else "(core memory is empty)")
        return
    ops = {"add": lambda t: add_line(t, a.line, a.section),
           "edit": lambda t: replace_once(t, a.old, a.new),
           "remove": lambda t: remove_line(t, a.text)}
    try:
        diff = change_core(ops[a.cmd], f"docs(memory): {a.message or a.cmd + ' in core memory'}")
    except ValueError as e:
        raise SystemExit(f"nothing changed: {e}")
    print(f"core memory updated:\n{diff}" if diff else "nothing changed")


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
        cli(sys.argv[1:], prog="core.py")


if __name__ == "__main__":
    main()

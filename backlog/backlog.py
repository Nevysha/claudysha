#!/usr/bin/env python3
"""backlog: postponed ideas in <home>/brain/maybe-later, and which ones are due.

Each note starts with a small frontmatter (status, created, review-after, summary).
See maybe-later/README.md there. Part of the claudysha plugin.
"""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "home"))
import claudysha  # noqa: E402  (plugin home: config, paths)

BRAIN = claudysha.brain_dir()
ACTIVE = {"open", "waiting"}


def frontmatter(path):
    """Parse the simple `key: value` frontmatter between the first two `---` lines."""
    meta = {}
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.split("#", 1)[0].strip()
    return meta


def notes(folder):
    out = []
    for p in sorted(Path(folder).glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        meta = frontmatter(p)
        meta["file"] = p.name
        out.append(meta)
    return out


def due(folder, today):
    """Active notes (open or waiting) whose review-after date has passed."""
    result = []
    for n in notes(folder):
        if n.get("status", "open") not in ACTIVE:
            continue
        try:
            when = dt.date.fromisoformat(n.get("review-after", ""))
        except ValueError:
            continue
        if when <= today:
            result.append(n)
    return result


def cmd_session_start(a):
    if os.environ.get("MEM_NO_HOOK"):
        return
    folder = BRAIN / "maybe-later"
    if not folder.is_dir():
        return
    items = due(folder, dt.date.today())
    if not items:
        return
    lines = [f"Backlog: {len(items)} postponed item(s) in {folder} are due for review. "
             f"Mention them to {claudysha.user_name()} once, briefly:"]
    for n in items:
        lines.append(f"- {n['file']} ({n.get('status')}, due {n.get('review-after')}): {n.get('summary', '')}")
    print("\n".join(lines))


def cmd_list(a):
    today = dt.date.today()
    for n in notes(BRAIN / "maybe-later"):
        if not a.all and n.get("status", "open") == "done":
            continue
        rev = n.get("review-after") or "-"
        mark = "DUE " if n in due(BRAIN / "maybe-later", today) else "    "
        print(f"{mark}{n.get('status', '?'):<8} {rev:<10} {n['file']}: {n.get('summary', '')}")


def main():
    p = argparse.ArgumentParser(prog="backlog")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("session-start", help="for the SessionStart hook").set_defaults(fn=cmd_session_start)
    s = sub.add_parser("list", help="all postponed items")
    s.add_argument("-a", "--all", action="store_true", help="include done items")
    s.set_defaults(fn=cmd_list)
    a = p.parse_args()
    with __import__("contextlib").suppress(Exception):
        if a.cmd == "session-start" and not sys.stdin.isatty():
            sys.stdin.read()
    a.fn(a)


if __name__ == "__main__":
    main()

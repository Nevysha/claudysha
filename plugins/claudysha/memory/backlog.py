#!/usr/bin/env python3
"""backlog (memory module): postponed ideas in <data folder>/brain/maybe-later, and which are due.

Each note starts with a small frontmatter (status, created, review-after, summary).
See maybe-later/README.md there. Part of the claudysha plugin.

Sessions read and change the backlog only through `mem backlog ...` (cli() below): an item's
id is its note's name without `.md`, and what they print never shows paths.
"""
import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claudysha  # noqa: E402  (the plugin: settings)
import core  # noqa: E402  (memory module paths)

BRAIN = core.brain_dir()
ACTIVE = {"open", "waiting"}
STATUSES = ["open", "waiting", "someday", "done"]
COMMENT = re.compile(r"\s+#\s")  # `key: value   # comment` (a `#12` in a summary is kept)


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
            meta[key.strip()] = COMMENT.split(value, 1)[0].strip()
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
    # No paths or file names here: the claudysha:recall skill explains where the backlog lives.
    lines = [f"Backlog: {len(items)} postponed idea(s) are due for review. "
             f"Mention them to {claudysha.user_name()} once, briefly (the claudysha:recall skill explains "
             "how to update or close them):"]
    for n in items:
        lines.append(f"- {n.get('summary') or n['file']} ({n.get('status')}, due {n.get('review-after')})")
    print("\n".join(lines))


def item_path(item_id):
    path = BRAIN / "maybe-later" / f"{item_id}.md"
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*", item_id) or not path.is_file() or item_id.lower() == "readme":
        raise SystemExit(f"no backlog item {item_id!r} (`mem backlog list -a` shows them all)")
    return path


def set_fields(text, fields):
    """Set frontmatter `key: value` lines (adding missing ones, keeping comments and the rest)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        lines = ["---", "---"] + lines
    end = next(i for i in range(1, len(lines) + 1) if i == len(lines) or lines[i].strip() == "---")
    if end == len(lines):
        lines.append("---")
    for key, value in fields.items():
        value = str(value).replace("\n", " ").strip()
        for i in range(1, end):
            k, sep, rest = lines[i].partition(":")
            if sep and k.strip() == key:
                comment = COMMENT.search(rest)
                lines[i] = f"{key}: {value}" + (f"   {rest[comment.start():].strip()}" if comment else "")
                break
        else:
            lines.insert(end, f"{key}: {value}")
            end += 1
    return "\n".join(lines) + "\n"


def body(text):
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines) - 1)
        lines = lines[end + 1:]
    return "\n".join(lines).strip()


def slug(summary):
    words = re.findall(r"[a-z0-9]+", summary.lower())
    base = "-".join(words)[:50].strip("-") or "item"
    name, n = base, 2
    while (BRAIN / "maybe-later" / f"{name}.md").exists():
        name, n = f"{base}-{n}", n + 1
    return name


def date_arg(value):
    if value in ("", "none"):
        return ""
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise SystemExit(f"not a date (YYYY-MM-DD): {value!r}")


def save(path, text, message):
    with claudysha.home_lock():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        core.commit([path], message)


def cmd_list(a):
    today = dt.date.today()
    rows = [n for n in notes(BRAIN / "maybe-later") if a.all or n.get("status", "open") != "done"]
    if not rows:
        print("(the backlog is empty)")
    due_ids = {n["file"] for n in due(BRAIN / "maybe-later", today)}
    for n in rows:
        rev = n.get("review-after") or "-"
        mark = "DUE " if n["file"] in due_ids else "    "
        print(f"{mark}{n.get('status', '?'):<8} {rev:<10} {n['file'][:-3]}: {n.get('summary', '')}")


def cmd_show(a):
    path = item_path(a.id)
    meta = frontmatter(path)
    for key in ("status", "created", "review-after", "summary"):
        print(f"{key}: {meta.get(key, '')}")
    text = body(path.read_text())
    if text:
        print(f"\n{text}")


def cmd_add(a):
    item_id = slug(a.summary)
    fields = {"status": a.status, "created": dt.date.today().isoformat(),
              "review-after": date_arg(a.review_after or ""), "summary": a.summary}
    text = set_fields("", fields) + (f"\n{a.details.strip()}\n" if a.details else "")
    save(BRAIN / "maybe-later" / f"{item_id}.md", text, f"docs(backlog): add {item_id}")
    print(f"added backlog item {item_id}")


def cmd_update(a):
    path = item_path(a.id)
    fields = {k: v for k, v in (("status", a.status), ("summary", a.summary)) if v}
    if a.review_after is not None:
        fields["review-after"] = date_arg(a.review_after)
    text = set_fields(path.read_text(), fields)
    if a.details is not None or a.append:
        head = text[:len(text) - len(text.split("\n---", 1)[-1])] if text.startswith("---") else ""
        rest = a.details.strip() if a.details is not None else body(text)
        if a.append:
            rest = (rest + "\n\n" + a.append.strip()).strip()
        text = head.rstrip("\n") + "\n" + (f"\n{rest}\n" if rest else "")
    if text == path.read_text():
        print("nothing changed")
        return
    save(path, text, f"docs(backlog): {'close' if a.status == 'done' else 'update'} {a.id}")
    print(f"updated backlog item {a.id}")


def cmd_edit(a):
    path = item_path(a.id)
    try:
        text = core.replace_once(path.read_text(), a.old, a.new)
    except ValueError as e:
        raise SystemExit(f"nothing changed: {e}")
    save(path, text, f"docs(backlog): edit {a.id}")
    print(f"updated backlog item {a.id}")


def cli(argv=None, prog="mem backlog"):
    """`mem backlog ...`: list, read, add and change postponed ideas."""
    p = argparse.ArgumentParser(prog=prog, description="The backlog: postponed ideas, each with a review date. "
                                "Session start lists the open or waiting ones that are due.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", help="the backlog (DUE marks items to review now)")
    s.add_argument("-a", "--all", action="store_true", help="include done items")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("show", help="one item in full")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("add", help="postpone an idea")
    s.add_argument("summary", help="one line")
    s.add_argument("--review-after", help="YYYY-MM-DD: when to bring it up again")
    s.add_argument("--details", help="the context a future session needs")
    s.add_argument("--status", choices=STATUSES, default="open")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("update", help="change an item")
    s.add_argument("id")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--review-after", help="YYYY-MM-DD, or none")
    s.add_argument("--summary")
    s.add_argument("--details", help="replace the details")
    s.add_argument("--append", help="add to the details")
    s.set_defaults(fn=cmd_update)
    s = sub.add_parser("edit", help="replace a passage of an item (it must appear exactly once)")
    s.add_argument("id")
    s.add_argument("--old", required=True)
    s.add_argument("--new", required=True)
    s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("done", help="close an item")
    s.add_argument("id")
    s.set_defaults(fn=lambda a: cmd_update(argparse.Namespace(id=a.id, status="done", summary=None,
                                                               review_after=None, details=None, append=None)))
    s = sub.add_parser("session-start", help="for the SessionStart hook")
    s.set_defaults(fn=cmd_session_start)
    a = p.parse_args(argv)
    with __import__("contextlib").suppress(Exception):
        if a.cmd == "session-start" and not sys.stdin.isatty():
            sys.stdin.read()
    a.fn(a)


def main():
    cli(sys.argv[1:], prog="backlog.py")


if __name__ == "__main__":
    main()

"""Interactive review of proposed memories (`mem review`)."""
import json
import os
import shutil
import sys
import textwrap
from functools import lru_cache

# One key per type, shown in the "change type" prompt.
TYPE_KEYS = {"f": "fact", "d": "decision", "x": "dead_end", "v": "event",
             "o": "todo", "p": "preference", "b": "feedback"}

HELP = ("[a] approve   [r] reject   [e] edit text   [t] change type   "
        "[s] skip   [u] undo   [q] quit")


class Style:
    def __init__(self, enabled):
        codes = {"bold": "1", "dim": "2", "cyan": "36", "green": "32",
                 "red": "31", "yellow": "33", "magenta": "35"}
        for name, code in codes.items():
            setattr(self, name, (lambda c: lambda s: f"\033[{c}m{s}\033[0m")(code) if enabled
                    else (lambda s: s))


@lru_cache(maxsize=None)
def session_title(transcript_path):
    """The title Claude Code gave the session (last `ai-title` line), if any."""
    title = None
    try:
        with open(transcript_path) as f:
            for line in f:
                if '"ai-title"' in line:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    title = d.get("aiTitle") or d.get("title") or title
    except OSError:
        pass
    return title


def context_of(db, r, search):
    """Where the memory came from, and up to two stored memories that look related."""
    source = r["source"]
    if r["session_id"]:
        row = db.execute("SELECT transcript_path FROM ingested WHERE session_id = ?",
                         (r["session_id"],)).fetchone()
        title = session_title(row[0]) if row else None
        source = f"session “{title}”" if title else f"session {r['session_id'][:8]}"
    related = [x for x in search(db, r["text"], limit=3) if x["id"] != r["id"]][:2]
    return source, related


def render(r, pos, total, source, related, st, width):
    width = max(40, min(width, 100))
    wrap = lambda s, ind="  ": textwrap.fill(s, width, initial_indent=ind, subsequent_indent=ind)
    head = f" {pos}/{total} "
    lines = [
        st.dim("─" * 3 + head + "─" * (width - 3 - len(head))),
        "  " + "  ·  ".join([
            st.bold(st.magenta(r["type"])), st.cyan(r["scope"]), r["created_at"][:10],
            f"confidence {r['confidence']:.1f}", st.dim(f"#{r['id']}")]),
        "",
        wrap(r["text"]),
        "",
    ]
    if r["tags"]:
        lines.append(st.dim(f"  tags: {r['tags']}"))
    lines.append(st.dim(f"  from: {source}"))
    if related:
        lines.append(st.dim("  looks related to:"))
        for x in related:
            short = textwrap.shorten(x["text"], width - 16, placeholder="…")
            lines.append(st.dim(f"    #{x['id']} {x['status']:<8} {short}"))
    lines += ["", "  " + st.yellow(HELP)]
    return "\n".join(lines)


def getkey():
    """Read one key press without Enter (falls back to a line when not on a terminal)."""
    if not sys.stdin.isatty():
        return (sys.stdin.readline() or "q")[:1]
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\x03", "\x04"):  # Ctrl-C, Ctrl-D
        return "q"
    return ch


def edit_line(prompt, current):
    """Prompt with the current text already filled in, editable with the arrow keys."""
    try:
        import readline
        readline.set_startup_hook(lambda: readline.insert_text(current))
    except ImportError:
        readline = None
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    finally:
        if readline:
            readline.set_startup_hook()


def review_loop(db, rows, search, needs_approval, *, key=getkey, edit=edit_line,
                out=print, st=None, width=None):
    """Walk through proposed memories. Returns a dict of counts."""
    st = st or Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    width = width or shutil.get_terminal_size((100, 24)).columns
    counts = {"approved": 0, "rejected": 0, "edited": 0, "retyped": 0, "skipped": 0}
    history = []  # (index, row before, count key) for each decision, so [u] can undo it

    def set_(r_id, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE memories SET {cols} WHERE id = ?", (*fields.values(), r_id))
        db.commit()

    i = 0
    while i < len(rows):
        r = db.execute("SELECT * FROM memories WHERE id = ?", (rows[i]["id"],)).fetchone()
        source, related = context_of(db, r, search)
        out("\n" + render(r, i + 1, len(rows), source, related, st, width))
        k = key().lower()
        if k == "u":
            if not history:
                out(st.dim("  nothing to undo"))
                continue
            i, before, done = history.pop()
            set_(before["id"], status=before["status"], type=before["type"])
            counts[done] -= 1
            out(st.yellow(f"  ↶ undone: #{before['id']} is {before['status']} again"))
            continue
        if k in ("a", "r", "s", "t"):
            history.append((i, r, {"a": "approved", "r": "rejected", "s": "skipped", "t": "retyped"}[k]))
        if k == "a":
            set_(r["id"], status="active")
            counts["approved"] += 1
            out(st.green("  ✓ approved"))
        elif k == "r":
            set_(r["id"], status="rejected")
            counts["rejected"] += 1
            out(st.red("  ✗ rejected"))
        elif k == "e":
            new = edit("  new text> ", r["text"])
            if new and new != r["text"]:
                set_(r["id"], text=new)
                counts["edited"] += 1
            continue  # show it again so it can be approved
        elif k == "t":
            out("  type: " + "  ".join(f"[{c}] {t}" for c, t in TYPE_KEYS.items()))
            new_type = TYPE_KEYS.get(key().lower())
            if not new_type or new_type == r["type"]:
                history.pop()
                continue
            # A memory moved out of the behaviour-shaping types doesn't need approval.
            status = "proposed" if new_type in needs_approval else "active"
            set_(r["id"], type=new_type, status=status)
            counts["retyped"] += 1
            if status == "proposed":
                history.pop()  # still on screen, nothing to undo yet
                continue
            out(st.green(f"  ✓ stored as {new_type}"))
        elif k == "q":
            break
        elif k in ("s", " ", "\r", "\n"):
            counts["skipped"] += 1
        else:
            continue  # unknown key: show the same entry again
        i += 1
    return counts

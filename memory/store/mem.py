#!/usr/bin/env python3
"""mem: the long-term store (tier 1) of the claudysha memory module, one SQLite file.

See README.md for the design. Run `mem -h` for the commands.
"""
import argparse
import contextlib
import datetime as dt
import difflib
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # memory/store/
sys.path.insert(0, str(ROOT.parent.parent / "plugin"))
sys.path.insert(0, str(ROOT.parent))
import claudysha  # noqa: E402  (the plugin: settings, data folder, lock)
import core  # noqa: E402  (memory module paths)

DATA_DIR = core.store_dir()                      # <data folder>/memory
DB_PATH = Path(os.environ.get("MEM_DB", DATA_DIR / "memory.db"))
LOG_PATH = DB_PATH.parent / "extract.log"
PROMPT_PATH = ROOT / "prompts" / "extract.md"
EXTRACT_MODEL = os.environ.get("MEM_EXTRACT_MODEL", "haiku")
CHUNK_CHARS = 80_000  # transcript text per extraction call
DEFAULT_PROJECTS = ["general"]  # plus `projects` from the config

TYPES = ["fact", "decision", "dead_end", "event", "todo", "preference", "feedback"]
# Types that shape how Claude behaves: extracted ones wait for the user's OK.
NEEDS_APPROVAL = {"preference", "feedback"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY,
    text          TEXT NOT NULL,
    type          TEXT NOT NULL,
    project       TEXT NOT NULL DEFAULT 'general',
    tags          TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.7,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'proposed', 'superseded', 'rejected')),
    superseded_by INTEGER REFERENCES memories(id),
    source        TEXT NOT NULL DEFAULT 'manual',   -- manual | extract
    session_id    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    last_used_at  TEXT,
    use_count     INTEGER NOT NULL DEFAULT 0,
    meta          TEXT NOT NULL DEFAULT '{}'        -- free-form JSON
);
CREATE INDEX IF NOT EXISTS memories_status ON memories(status, project, type);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, tags, project, content='memories', content_rowid='id',
    tokenize="unicode61 tokenchars '_'"
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, tags, project) VALUES (new.id, new.text, new.tags, new.project);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags, project)
    VALUES ('delete', old.id, old.text, old.tags, old.project);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text, tags, project ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags, project)
    VALUES ('delete', old.id, old.text, old.tags, old.project);
    INSERT INTO memories_fts(rowid, text, tags, project) VALUES (new.id, new.text, new.tags, new.project);
END;

-- How far each transcript has been read, so extraction is incremental.
CREATE TABLE IF NOT EXISTS ingested (
    session_id      TEXT PRIMARY KEY,
    transcript_path TEXT NOT NULL,
    lines_done      INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);
"""


def now():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def local_time(iso):
    """Transcript timestamps are UTC ('...Z'): convert to local 'YYYY-MM-DDTHH:MM:SS'."""
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def log(msg):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(f"{now()} {msg}\n")


# ---------------------------------------------------------------- search

def fts_query(text):
    """Each whitespace-separated term becomes a quoted phrase, OR-ed together.

    Quoting keeps exact tokens like 127.0.0.1:8080 or pnpm@10.15.1 working:
    FTS splits them inside the phrase and matches the pieces next to each other.
    """
    terms = [t.replace('"', '""') for t in text.split() if re.search(r"\w", t)]
    return " OR ".join(f'"{t}"' for t in terms)


def search(db, query, project=None, type_=None, limit=10, include_old=False):
    q = fts_query(query)
    if not q:
        return []
    sql = """SELECT m.*, bm25(memories_fts, 1.0, 0.6, 0.3) AS rank
             FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
             WHERE memories_fts MATCH ?"""
    args = [q]
    if not include_old:
        sql += " AND m.status IN ('active', 'proposed')"
    if project:
        sql += " AND m.project = ?"
        args.append(project)
    if type_:
        sql += " AND m.type = ?"
        args.append(type_)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit * 3)
    rows = db.execute(sql, args).fetchall()

    # bm25 is negative (lower is better). Nudge it with confidence and use.
    def score(r):
        return r["rank"] * (0.6 + 0.4 * r["confidence"]) * (1 + 0.05 * min(r["use_count"], 10))

    return sorted(rows, key=score)[:limit]


def mark_used(db, rows):
    db.executemany(
        "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
        [(now(), r["id"]) for r in rows],
    )
    db.commit()


def fmt(r, full=False):
    flags = ""
    if r["status"] != "active":
        flags = f" {r['status'].upper()}"
        if r["superseded_by"]:
            flags += f"→#{r['superseded_by']}"
    head = f"#{r['id']} [{r['type']}/{r['project']} {r['created_at'][:10]} c{r['confidence']:.1f}{flags}]"
    if not full:
        return f"{head} {r['text']}"
    lines = [head, r["text"]]
    if r["tags"]:
        lines.append(f"tags: {r['tags']}")
    lines.append(f"source: {r['source']}" + (f" (session {r['session_id']})" if r["session_id"] else ""))
    lines.append(f"used: {r['use_count']}x, last {r['last_used_at'] or 'never'}")
    if r["meta"] and r["meta"] != "{}":
        lines.append(f"meta: {r['meta']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- writing

def similar_existing(db, text, project, threshold=0.82):
    """Return an active/proposed memory that says nearly the same thing, if any."""
    for r in search(db, text, project=project, limit=5):
        if difflib.SequenceMatcher(None, r["text"].lower(), text.lower()).ratio() >= threshold:
            return r
    return None


def insert(db, text, type_, project="general", tags="", confidence=0.7,
           status="active", source="manual", session_id=None, meta=None, created_at=None):
    if type_ not in TYPES:
        raise SystemExit(f"unknown type {type_!r}, use one of: {', '.join(TYPES)}")
    cur = db.execute(
        """INSERT INTO memories (text, type, project, tags, confidence, status, source, session_id, meta,
                                 created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (text.strip(), type_, project or "general", tags.strip().lower(), confidence,
         status, source, session_id, json.dumps(meta or {}), created_at or now()),
    )
    db.commit()
    return cur.lastrowid


def get(db, id_):
    r = db.execute("SELECT * FROM memories WHERE id = ?", (id_,)).fetchone()
    if not r:
        raise SystemExit(f"no memory #{id_}")
    return r


# ---------------------------------------------------------------- extraction

def condense(transcript_path, start_line):
    """Turn transcript lines into plain dialogue text.

    Returns (text, last_line, date, humans): humans counts the messages the user typed
    (origin.kind == "human"); `claude -p` runs from scripts and tools have none.
    Other user-role messages (task notifications, summaries, messages from other
    sessions) are labelled as notes so they're never attributed to the user.
    """
    parts, date, n, humans = [], None, start_line, 0
    user = claudysha.user_name()
    with open(transcript_path) as f:
        for n, line in enumerate(f, 1):
            if n <= start_line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict) or d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
                continue
            date = local_time(d.get("timestamp")) or date
            content = (d.get("message") or {}).get("content")
            human = d["type"] == "user" and (d.get("origin") or {}).get("kind") == "human"
            if d["type"] == "assistant":
                role = "Claude"
            elif human:
                role = user
                humans += 1
            else:
                role = f"[note, not from {user}]"
            if isinstance(content, str):
                if d.get("isMeta") or content.startswith(("<local-command", "<command-")):
                    continue
                parts.append(f"{role}: {content.strip()}")
                continue
            for block in content or []:
                kind = block.get("type")
                if kind == "text" and block.get("text", "").strip():
                    parts.append(f"{role}: {block['text'].strip()}")
                elif kind == "tool_use":
                    inp = block.get("input") or {}
                    hint = inp.get("description") or inp.get("file_path") or inp.get("query") or ""
                    if hint:
                        parts.append(f"[Claude used {block.get('name')}: {str(hint)[:160]}]")
    return "\n\n".join(parts), n, date, humans


def chunks(text, size=CHUNK_CHARS):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip()


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "type": {"type": "string", "enum": TYPES},
            "project": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": ["text", "type", "project", "confidence"],
    }}},
    "required": ["items"],
}


RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "new": {"type": "integer"},
            "duplicate_of": {"type": ["integer", "null"]},
            "supersedes": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["new", "duplicate_of", "supersedes"],
    }}},
    "required": ["decisions"],
}


def prompt_text(path):
    """A prompt file with {{user}} and {{pronouns}} filled in from the config."""
    return (path.read_text().replace("{{user}}", claudysha.user_name())
            .replace("{{pronouns}}", claudysha.user_pronouns()))


def run_claude(system, prompt, schema):
    env = dict(os.environ, MEM_NO_HOOK="1")  # never let the child session trigger extraction
    proc = subprocess.run(
        ["claude", "-p", "--model", EXTRACT_MODEL, "--no-session-persistence",
         "--tools", "", "--setting-sources", "", "--system-prompt", system,
         "--output-format", "json", "--json-schema", json.dumps(schema)],
        input=prompt, capture_output=True, text=True, env=env, timeout=600,
        cwd=str(DB_PATH.parent),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude error: {out.get('result')}")
    return out.get("structured_output") or {}, out.get("total_cost_usd", 0) or 0


def call_model(excerpt, date, projects):
    prompt = (f"Excerpt date: {(date or 'unknown')[:10]}\n"
              f"Known projects: {', '.join(projects)}\n\n"
              f"<excerpt>\n{excerpt}\n</excerpt>\n\nExtract the memories.")
    out, cost = run_claude(prompt_text(PROMPT_PATH), prompt, EXTRACT_SCHEMA)
    return out.get("items", []), cost


def resolve(db, items, date):
    """Compare new items with the closest stored memories.

    Returns ({new_index: duplicate_id}, {new_index: [superseded ids]}, cost).
    """
    blocks, known = [], set()
    for i, it in enumerate(items):
        near = search(db, it["text"], limit=4)
        if not near:
            continue
        known.update(r["id"] for r in near)
        lines = "\n".join(f"  #{r['id']} ({r['created_at'][:10]}, {r['type']}) {r['text']}" for r in near)
        blocks.append(f"NEW {i} ({(date or '?')[:10]}, {it['type']}): {it['text']}\nclosest stored:\n{lines}")
    if not blocks:
        return {}, {}, 0.0
    system = prompt_text(PROMPT_PATH.parent / "resolve.md")
    out, cost = run_claude(system, "\n\n".join(blocks), RESOLVE_SCHEMA)
    dups, sups = {}, {}
    for d in out.get("decisions", []):
        i = d.get("new")
        if not isinstance(i, int) or not 0 <= i < len(items):
            continue
        if d.get("duplicate_of") in known:
            dups[i] = d["duplicate_of"]
        elif d.get("supersedes"):
            sups[i] = [s for s in d["supersedes"] if s in known]
    return dups, sups, cost


def mark_ingested(db, session_id, transcript_path, last_line):
    db.execute(
        """INSERT INTO ingested (session_id, transcript_path, lines_done, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET lines_done = excluded.lines_done,
               transcript_path = excluded.transcript_path, updated_at = excluded.updated_at""",
        (session_id, transcript_path, last_line, now()),
    )
    db.commit()


def ingest(db, transcript_path, session_id, dry_run=False):
    transcript_path = str(Path(transcript_path).expanduser())
    session_id = session_id or Path(transcript_path).stem
    lock = open(DB_PATH.parent / f".ingest-{session_id}.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # PreCompact and SessionEnd may race
    home_lock = claudysha.home_lock()  # shared: `claudysha load` waits for us, and we for it
    home_lock.__enter__()
    try:
        row = db.execute("SELECT lines_done FROM ingested WHERE session_id = ?", (session_id,)).fetchone()
        start = row["lines_done"] if row else 0
        text, last_line, date, humans = condense(transcript_path, start)
        if not text.strip() or (start == 0 and humans == 0):
            # Nothing new, or a session the user never typed in (claude -p from a script):
            # remember where we are, extract nothing.
            if not dry_run:
                mark_ingested(db, session_id, transcript_path, last_line)
            return 0, 0, 0, 0.0
        projects = sorted(set(DEFAULT_PROJECTS) | set(claudysha.projects()) | {
            r[0] for r in db.execute("SELECT DISTINCT project FROM memories")})
        added = skipped = superseded = 0
        cost = 0.0
        for part in chunks(text):
            items, c = call_model(part, date, projects)
            cost += c
            items = [it for it in items
                     if (it.get("text") or "").strip() and it.get("type") in TYPES]
            if dry_run:
                for it in items:
                    print(f"[{it['type']}/{it.get('project')} c{it.get('confidence', 0):.1f}] {it['text']}")
                added += len(items)
                continue
            dups, sups, c = resolve(db, items, date)
            cost += c
            for i, it in enumerate(items):
                body = it["text"].strip()
                dup_id = dups.get(i)
                if dup_id is None:
                    dup = similar_existing(db, body, it.get("project"))
                    dup_id = dup["id"] if dup else None
                if dup_id is not None:
                    skipped += 1
                    db.execute("UPDATE memories SET use_count = use_count + 1 WHERE id = ?", (dup_id,))
                    continue
                new_id = insert(db, body, it["type"], it.get("project") or "general",
                                " ".join(it.get("tags") or []),
                                max(0.0, min(1.0, float(it.get("confidence", 0.6)))),
                                status="proposed" if it["type"] in NEEDS_APPROVAL else "active",
                                source="extract", session_id=session_id, created_at=date)
                added += 1
                for old in sups.get(i, []):
                    db.execute("""UPDATE memories SET status = 'superseded', superseded_by = ?
                                  WHERE id = ? AND status IN ('active', 'proposed')""", (new_id, old))
                    superseded += 1
            db.commit()
        if not dry_run:
            mark_ingested(db, session_id, transcript_path, last_line)
        return added, skipped, superseded, cost
    finally:
        home_lock.__exit__(None, None, None)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


# ---------------------------------------------------------------- commands

def cmd_search(db, a):
    rows = search(db, " ".join(a.query), a.project, a.type, a.limit, a.all)
    for r in rows:
        print(fmt(r, a.full))
        if a.full:
            print()
    if not rows:
        print("(no match)")
    mark_used(db, rows)


def cmd_add(db, a):
    dup = None if a.force else similar_existing(db, a.text, a.project)
    if dup:
        print(f"skipped, already stored:\n{fmt(dup)}\n(use --force to add anyway, or `mem supersede {dup['id']} ...`)")
        return
    id_ = insert(db, a.text, a.type, a.project, a.tags, a.confidence)
    print(f"added #{id_}")


def cmd_get(db, a):
    for id_ in a.ids:
        print(fmt(get(db, id_), full=True))
        print()


def cmd_list(db, a):
    sql = "SELECT * FROM memories WHERE 1=1"
    args = []
    if not a.all:
        sql += " AND status IN ('active', 'proposed')"
    if a.project:
        sql += " AND project = ?"
        args.append(a.project)
    if a.type:
        sql += " AND type = ?"
        args.append(a.type)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(a.limit)
    for r in db.execute(sql, args):
        print(fmt(r))


def cmd_supersede(db, a):
    old = get(db, a.id)
    if a.by:
        new_id = get(db, a.by)["id"]
    else:
        if not a.text:
            raise SystemExit("give the new text, or --by ID")
        new_id = insert(db, a.text, a.type or old["type"], a.project or old["project"],
                        old["tags"], old["confidence"])
    db.execute("UPDATE memories SET status = 'superseded', superseded_by = ? WHERE id = ?", (new_id, old["id"]))
    db.commit()
    print(f"#{old['id']} superseded by #{new_id}")


def cmd_set_status(status):
    def run(db, a):
        for id_ in a.ids:
            get(db, id_)
            db.execute("UPDATE memories SET status = ? WHERE id = ?", (status, id_))
        db.commit()
        word = {"active": "approved", "rejected": "rejected"}.get(status, status)
        print(f"{word}: " + ", ".join(f"#{i}" for i in a.ids))
    return run


def cmd_edit(db, a):
    r = get(db, a.id)
    db.execute(
        "UPDATE memories SET text = ?, type = ?, project = ?, tags = ?, confidence = ? WHERE id = ?",
        (a.text or r["text"], a.type or r["type"], a.project or r["project"],
         a.tags if a.tags is not None else r["tags"],
         a.confidence if a.confidence is not None else r["confidence"], r["id"]),
    )
    db.commit()
    print(fmt(get(db, r["id"])))


def cmd_review(db, a):
    rows = db.execute("SELECT * FROM memories WHERE status = 'proposed' ORDER BY project, type, id").fetchall()
    if not rows:
        print("nothing to review")
        return
    if a.list or not sys.stdin.isatty():
        project = None
        for r in rows:
            if r["project"] != project:
                project = r["project"]
                print(f"\n## {project}")
            print(f"\n#{r['id']} {r['type']}, {r['created_at'][:10]}, confidence {r['confidence']:.1f}")
            print(textwrap.fill(r["text"], 100, initial_indent="  ", subsequent_indent="  "))
        print(f"\n{len(rows)} proposed. Review them one by one with `mem review` in a terminal.")
        return
    import review
    c = review.review_loop(db, rows, search, NEEDS_APPROVAL)
    left = db.execute("SELECT COUNT(*) FROM memories WHERE status = 'proposed'").fetchone()[0]
    print(f"\n{c['approved']} approved, {c['rejected']} rejected, {c['edited']} edited, "
          f"{c['retyped']} retyped, {c['skipped']} skipped. {left} still proposed.")


def cmd_ingest(db, a):
    if a.hook:
        if os.environ.get("MEM_NO_HOOK"):
            return
        data = json.load(sys.stdin)
        path, session = data.get("transcript_path"), data.get("session_id")
        try:
            added, skipped, sup, cost = ingest(db, path, session)
            log(f"{data.get('hook_event_name')} {session}: +{added} new, {skipped} duplicates, "
                f"{sup} superseded, ${cost:.4f}")
        except Exception as e:  # a hook must never break the session
            log(f"ERROR {session}: {e}")
        return
    added, skipped, sup, cost = ingest(db, a.transcript, a.session, a.dry_run)
    print(f"{'would add' if a.dry_run else 'added'} {added}, skipped {skipped} duplicates, "
          f"superseded {sup}, cost ${cost:.4f}")


def cmd_hook(db, a):
    """Entry point for Claude Code hooks: hand the hook JSON to a detached `ingest --hook`."""
    if os.environ.get("MEM_NO_HOOK"):
        return
    payload = sys.stdin.read()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as logf:
        proc = subprocess.Popen([sys.executable, str(ROOT / "mem.py"), "ingest", "--hook"],
                                stdin=subprocess.PIPE, stdout=logf, stderr=logf,
                                start_new_session=True, text=True)
        proc.stdin.write(payload)
        proc.stdin.close()


def cmd_session_start(db, a):
    """SessionStart hook: a short note so every session knows the store exists."""
    if os.environ.get("MEM_NO_HOOK"):
        return
    with contextlib.suppress(Exception):
        sys.stdin.read()  # the hook JSON; not needed
    active, proposed = db.execute(
        """SELECT COUNT(*) FILTER (WHERE status = 'active'),
                  COUNT(*) FILTER (WHERE status = 'proposed') FROM memories""").fetchone()
    note = (f"Long-term memory: {active} memories from past sessions are stored in the claudysha plugin. "
            "Search them (claudysha:recall skill) before answering about past work or redoing something.")
    if proposed:
        note += (f" {proposed} proposed memories are waiting for {claudysha.user_name()}'s review: "
                 "mention it once (/claudysha:memory-review).")
    print(note)


def cmd_backfill(db, a):
    base = Path("~/.claude/projects").expanduser()
    files = sorted(base.glob(f"{a.project_dir or '*'}/*.jsonl"), key=lambda p: p.stat().st_mtime)
    total = 0.0
    for f in files:
        if f.stem.startswith("agent-"):
            continue
        added, skipped, sup, cost = ingest(db, f, f.stem)
        total += cost
        print(f"{f.parent.name}/{f.name}: +{added}, {skipped} dup, {sup} superseded, ${cost:.4f}", flush=True)
    print(f"total ${total:.4f}")


def cmd_stats(db, a):
    print(f"db: {DB_PATH}")
    for row in db.execute("SELECT status, COUNT(*) FROM memories GROUP BY status"):
        print(f"  {row[0]}: {row[1]}")
    print("by project/type (active + proposed):")
    for row in db.execute("""SELECT project, type, COUNT(*) FROM memories
                             WHERE status IN ('active','proposed') GROUP BY 1, 2 ORDER BY 1, 2"""):
        print(f"  {row[0]:<16} {row[1]:<11} {row[2]}")
    print(f"transcripts ingested: {db.execute('SELECT COUNT(*) FROM ingested').fetchone()[0]}")


def cmd_export(db, a):
    for r in db.execute("SELECT * FROM memories ORDER BY id"):
        print(json.dumps(dict(r), ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(prog="mem", description="Claude's long-term memory store.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="full-text search (words OR-ed, exact tokens kept)")
    s.add_argument("query", nargs="+")
    s.add_argument("-p", "--project")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("-a", "--all", action="store_true", help="include superseded and rejected")
    s.add_argument("-f", "--full", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("add", help="store a memory")
    s.add_argument("text")
    s.add_argument("-t", "--type", choices=TYPES, default="fact")
    s.add_argument("-p", "--project", default="general")
    s.add_argument("--tags", default="")
    s.add_argument("-c", "--confidence", type=float, default=0.9)
    s.add_argument("--force", action="store_true", help="add even if a near-duplicate exists")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("get", help="show memories in full")
    s.add_argument("ids", nargs="+", type=int)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("list", help="latest memories")
    s.add_argument("-p", "--project")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("-a", "--all", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("supersede", help="replace a memory with a newer one")
    s.add_argument("id", type=int)
    s.add_argument("text", nargs="?")
    s.add_argument("--by", type=int, help="existing memory that replaces it")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-p", "--project")
    s.set_defaults(fn=cmd_supersede)

    s = sub.add_parser("edit", help="change a memory in place")
    s.add_argument("id", type=int)
    s.add_argument("--text")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-p", "--project")
    s.add_argument("--tags")
    s.add_argument("-c", "--confidence", type=float)
    s.set_defaults(fn=cmd_edit)

    for name, status in (("approve", "active"), ("reject", "rejected")):
        s = sub.add_parser(name, help=f"mark proposed memories {status}")
        s.add_argument("ids", nargs="+", type=int)
        s.set_defaults(fn=cmd_set_status(status))

    s = sub.add_parser("review", help="go through proposed memories one by one (approve, reject, edit)")
    s.add_argument("-l", "--list", action="store_true", help="just list them")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("ingest", help="extract memories from a session transcript")
    s.add_argument("transcript", nargs="?")
    s.add_argument("--session")
    s.add_argument("--hook", action="store_true", help="read Claude Code hook JSON from stdin")
    s.add_argument("--dry-run", action="store_true", help="print candidates, store nothing")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("backfill", help="ingest every transcript under ~/.claude/projects")
    s.add_argument("--project-dir", help="only this folder, e.g. -home-nevysha-brain")
    s.set_defaults(fn=cmd_backfill)

    sub.add_parser("hook", help="for Claude Code hooks: run ingest in the background").set_defaults(fn=cmd_hook)
    sub.add_parser("session-start", help="for the SessionStart hook: print a short note").set_defaults(
        fn=cmd_session_start)

    sub.add_parser("stats", help="counts").set_defaults(fn=cmd_stats)
    sub.add_parser("export", help="dump all memories as JSONL").set_defaults(fn=cmd_export)

    a = p.parse_args()
    if a.cmd == "ingest" and not a.hook and not a.transcript:
        p.error("ingest needs a transcript path or --hook")
    with contextlib.closing(connect()) as db:
        a.fn(db, a)


if __name__ == "__main__":
    main()

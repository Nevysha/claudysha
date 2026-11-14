#!/usr/bin/env python3
"""mem: the long-term store (tier 1) of the claudysha memory module, one SQLite file.

See README.md for the design. Run `mem -h` for the commands.
"""
import argparse
import collections
import contextlib
import datetime as dt
import difflib
import fcntl
import json
import math
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
EVERY_LINES = 300     # Stop hook: extract once this many new transcript lines piled up
GLOBAL = claudysha.GLOBAL_SCOPE  # the scope of memories that hold in every session
RULES_SHOWN = 15                 # session start: approved preferences/feedback shown per scope
OTHER_SCOPES_SHOWN = 20          # session start: other scopes listed

TYPES = ["fact", "decision", "dead_end", "event", "todo", "preference", "feedback"]
# Types that shape how Claude behaves: extracted ones wait for the user's OK.
NEEDS_APPROVAL = {"preference", "feedback"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY,
    text          TEXT NOT NULL,
    type          TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'global',    -- repository name, folder path, or global
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
CREATE INDEX IF NOT EXISTS memories_status ON memories(status, scope, type);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, tags, scope, content='memories', content_rowid='id',
    tokenize="unicode61 tokenchars '_'"
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, tags, scope) VALUES (new.id, new.text, new.tags, new.scope);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags, scope)
    VALUES ('delete', old.id, old.text, old.tags, old.scope);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text, tags, scope ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags, scope)
    VALUES ('delete', old.id, old.text, old.tags, old.scope);
    INSERT INTO memories_fts(rowid, text, tags, scope) VALUES (new.id, new.text, new.tags, new.scope);
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
    migrate(db)
    db.executescript(SCHEMA)
    return db


def migrate(db):
    """Stores made before scopes used a free-form `project` label: rename it to `scope` and
    'general' to 'global'. `mem rescope` then files the rest by the folder of their session."""
    def old_layout():
        return "project" in {r[1] for r in db.execute("PRAGMA table_info(memories)")}
    if not old_layout():
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        if old_layout():  # another process may have migrated while we waited
            for stmt in ("DROP TRIGGER IF EXISTS memories_ai", "DROP TRIGGER IF EXISTS memories_ad",
                         "DROP TRIGGER IF EXISTS memories_au", "DROP TABLE IF EXISTS memories_fts",
                         "DROP INDEX IF EXISTS memories_status",
                         "ALTER TABLE memories RENAME COLUMN project TO scope"):
                db.execute(stmt)
            db.execute("UPDATE memories SET scope = ? WHERE scope = 'general'", (GLOBAL,))
            db.commit()
            db.executescript(SCHEMA)
            db.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
        db.commit()
    except BaseException:
        db.rollback()
        raise


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


def search(db, query, scopes=None, type_=None, limit=10, include_old=False):
    """Full-text search. `scopes`: a list to search only those, None for every scope."""
    q = fts_query(query)
    if not q:
        return []
    sql = """SELECT m.*, bm25(memories_fts, 1.0, 0.6, 0.3) AS rank
             FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
             WHERE memories_fts MATCH ?"""
    args = [q]
    if not include_old:
        sql += " AND m.status IN ('active', 'proposed')"
    if scopes is not None:
        sql += f" AND m.scope IN ({','.join('?' * len(scopes))})"
        args.extend(scopes)
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


def fmt(r, full=False, here=None):
    """One memory as text. With `here` (the session's scope), memories from another scope
    are marked: they describe that place, they're not rules for this one."""
    flags = ""
    if r["status"] != "active":
        flags = f" {r['status'].upper()}"
        if r["superseded_by"]:
            flags += f"→#{r['superseded_by']}"
    head = f"#{r['id']} [{r['type']}/{r['scope']} {r['created_at'][:10]} c{r['confidence']:.1f}{flags}]"
    if here and r["scope"] not in (here, GLOBAL):
        head += " (other scope)"
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

def similar_existing(db, text, scope, threshold=0.82):
    """Return an active/proposed memory of that scope that says nearly the same thing, if any."""
    for r in search(db, text, scopes=[scope or GLOBAL], limit=5):
        if difflib.SequenceMatcher(None, r["text"].lower(), text.lower()).ratio() >= threshold:
            return r
    return None


WORD = re.compile(r"[\w.:/@-]*\w")


def words(text):
    return {w for w in WORD.findall(text.lower()) if len(w) > 2}


def similar_groups(db, scope, min_score=0.3):
    """Groups of current memories of `scope` that look alike (with `global` ones they repeat).

    Score of a pair: the share of the shorter entry's words found in the other, each word weighted
    by how rare it is in the scope (a name in every entry counts for little). It catches an entry
    that repeats another one and adds something, which plain text similarity misses.
    Returns [(score, [rows])], best first.
    """
    rows = db.execute("SELECT * FROM memories WHERE status IN ('active', 'proposed') AND scope IN (?, ?)",
                      (scope, GLOBAL)).fetchall()
    bag = {r["id"]: words(r["text"]) for r in rows}
    df = collections.Counter(w for ws in bag.values() for w in ws)
    idf = {w: math.log((len(rows) + 1) / n) for w, n in df.items()}
    by_id = {r["id"]: r for r in rows}
    pairs = {}
    for r in rows:
        if r["scope"] != scope:
            continue
        for h in search(db, r["text"], scopes=[scope, GLOBAL], limit=8):
            if h["id"] == r["id"] or (h["id"], r["id"]) in pairs:
                continue
            a, b = bag[r["id"]], bag.get(h["id"], set())
            weight = min(sum(idf[w] for w in a), sum(idf[w] for w in b))
            if not weight:
                continue
            score = sum(idf[w] for w in a & b) / weight
            if score >= min_score:
                pairs[(r["id"], h["id"])] = score
    # Complete linkage: an entry joins a group only if it looks like every member, so a chain
    # (A like B, B like C) doesn't pull unrelated entries together.
    def alike(a, b):
        return pairs.get((a, b)) or pairs.get((b, a)) or 0

    group_of, groups = {}, []
    for a, b in sorted(pairs, key=pairs.get, reverse=True):
        ga, gb = group_of.get(a), group_of.get(b)
        if ga is None and gb is None:
            groups.append([a, b])
            group_of[a] = group_of[b] = len(groups) - 1
        elif (ga is None) != (gb is None):
            g, new = (gb, a) if ga is None else (ga, b)
            if all(alike(new, m) for m in groups[g]):
                groups[g].append(new)
                group_of[new] = g
    out = [(max(alike(x, y) for x in ids for y in ids if x != y), [by_id[i] for i in sorted(ids)])
           for ids in groups]
    return sorted(out, key=lambda g: -g[0])


def insert(db, text, type_, scope=GLOBAL, tags="", confidence=0.7,
           status="active", source="manual", session_id=None, meta=None, created_at=None):
    if type_ not in TYPES:
        raise SystemExit(f"unknown type {type_!r}, use one of: {', '.join(TYPES)}")
    cur = db.execute(
        """INSERT INTO memories (text, type, scope, tags, confidence, status, source, session_id, meta,
                                 created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (text.strip(), type_, scope or GLOBAL, tags.strip().lower(), confidence,
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


def session_cwd(transcript_path):
    """The folder the session worked in, or None.

    Claude Code files a transcript under ~/.claude/projects/<project folder, with every
    character but letters and digits turned into '-'>. Among the folders the transcript
    records (`cwd`, which follows the shell), the one matching that name is the session's
    folder, even when the session was resumed from another one. Else the most frequent.
    """
    seen = {}
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and isinstance(d.get("cwd"), str) and d["cwd"]:
                    seen[d["cwd"]] = seen.get(d["cwd"], 0) + 1
    except OSError:
        return None
    folder = Path(transcript_path).parent.name
    for cwd in seen:
        if re.sub(r"[^A-Za-z0-9]", "-", cwd) == folder:
            return cwd
    return max(seen, key=seen.get) if seen else None


def session_scope(transcript_path):
    """The scope of the session behind a transcript (see claudysha.scope_of)."""
    cwd = session_cwd(transcript_path)
    # No cwd recorded (very old transcripts): Claude Code's own folder name for the project.
    return claudysha.scope_of(cwd) if cwd else Path(transcript_path).parent.name


def chunks(text, size=CHUNK_CHARS):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip()


def extract_schema(scope):
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "type": {"type": "string", "enum": TYPES},
                "scope": {"type": "string", "enum": [scope, GLOBAL]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": ["text", "type", "scope", "confidence"],
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
            "keep_only": {"type": ["string", "null"]},
        },
        "required": ["new", "duplicate_of", "supersedes", "keep_only"],
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


def call_model(excerpt, date, scope):
    prompt = (f"Excerpt date: {(date or 'unknown')[:10]}\n"
              f"This session's scope: {scope}\n\n"
              f"<excerpt>\n{excerpt}\n</excerpt>\n\nExtract the memories.")
    out, cost = run_claude(prompt_text(PROMPT_PATH), prompt, extract_schema(scope))
    return out.get("items", []), cost


def resolve(db, items, date, scopes):
    """Compare new items with the closest stored memories of these scopes.

    Returns ({new_index: duplicate_id}, {new_index: [superseded ids]},
             {new_index: text to store instead, without what's already stored}, cost).
    """
    blocks, known = [], set()
    for i, it in enumerate(items):
        near = search(db, it["text"], scopes=scopes, limit=4)
        if not near:
            continue
        known.update(r["id"] for r in near)
        lines = "\n".join(f"  #{r['id']} ({r['created_at'][:10]}, {r['type']}) {r['text']}" for r in near)
        blocks.append(f"NEW {i} ({(date or '?')[:10]}, {it['type']}): {it['text']}\nclosest stored:\n{lines}")
    if not blocks:
        return {}, {}, {}, 0.0
    system = prompt_text(PROMPT_PATH.parent / "resolve.md")
    out, cost = run_claude(system, "\n\n".join(blocks), RESOLVE_SCHEMA)
    dups, sups, trims = {}, {}, {}
    for d in out.get("decisions", []):
        i = d.get("new")
        if not isinstance(i, int) or not 0 <= i < len(items):
            continue
        if d.get("duplicate_of") in known:
            dups[i] = d["duplicate_of"]
            continue
        if d.get("supersedes"):
            sups[i] = [s for s in d["supersedes"] if s in known]
        if (d.get("keep_only") or "").strip():
            trims[i] = d["keep_only"].strip()
    return dups, sups, trims, cost


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
        scope = session_scope(transcript_path)
        added = skipped = superseded = 0
        cost = 0.0
        for part in chunks(text):
            items, c = call_model(part, date, scope)
            cost += c
            items = [it for it in items
                     if (it.get("text") or "").strip() and it.get("type") in TYPES]
            for it in items:
                it["scope"] = GLOBAL if it.get("scope") == GLOBAL else scope
            if dry_run:
                for it in items:
                    print(f"[{it['type']}/{it['scope']} c{it.get('confidence', 0):.1f}] {it['text']}")
                added += len(items)
                continue
            dups, sups, trims, c = resolve(db, items, date, [scope, GLOBAL])
            cost += c
            for i, it in enumerate(items):
                body = trims.get(i) or it["text"].strip()
                dup_id = dups.get(i)
                if dup_id is None:
                    dup = similar_existing(db, body, it["scope"])
                    dup_id = dup["id"] if dup else None
                if dup_id is not None:
                    skipped += 1
                    db.execute("UPDATE memories SET use_count = use_count + 1 WHERE id = ?", (dup_id,))
                    continue
                new_id = insert(db, body, it["type"], it["scope"],
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
    here = claudysha.current_scope()
    scopes = None if a.all_scopes else (a.scope or [here, GLOBAL])
    rows = search(db, " ".join(a.query), scopes, a.type, a.limit, a.all)
    for r in rows:
        print(fmt(r, a.full, here))
        if a.full:
            print()
    if not rows:
        print("(no match" + ("" if scopes is None else f" in {', '.join(scopes)}; -A searches every scope") + ")")
    elif any(r["scope"] not in (here, GLOBAL) for r in rows):
        print(f"(other scope: describes another repository or folder, not a rule for `{here}`)")
    mark_used(db, rows)


def cmd_add(db, a):
    scope = a.scope or claudysha.current_scope()
    dup = None if a.force else similar_existing(db, a.text, scope)
    if dup:
        print(f"skipped, already stored:\n{fmt(dup)}\n(use --force to add anyway, or `mem supersede {dup['id']} ...`)")
        return
    id_ = insert(db, a.text, a.type, scope, a.tags, a.confidence)
    print(f"added #{id_} ({scope})")


def cmd_get(db, a):
    for id_ in a.ids:
        print(fmt(get(db, id_), full=True))
        print()


def cmd_list(db, a):
    sql = "SELECT * FROM memories WHERE 1=1"
    args = []
    if not a.all:
        sql += " AND status IN ('active', 'proposed')"
    if a.scope:
        sql += " AND scope = ?"
        args.append(a.scope)
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
        new_id = insert(db, a.text, a.type or old["type"], a.scope or old["scope"],
                        old["tags"], old["confidence"])
    retire(db, old, "superseded", new_id)
    db.commit()
    print(f"#{old['id']} superseded by #{new_id}")


def meta_of(r):
    try:
        return json.loads(r["meta"] or "{}")
    except ValueError:
        return {}


def retire(db, r, status, by=None, **extra):
    """Mark a memory superseded or rejected, remembering its status so `mem restore` can undo it."""
    meta = {**meta_of(r), **extra}
    if r["status"] in ("active", "proposed"):
        meta["was"] = r["status"]
    db.execute("UPDATE memories SET status = ?, superseded_by = ?, meta = ? WHERE id = ?",
               (status, by, json.dumps(meta), r["id"]))


def revive(db, r):
    meta = meta_of(r)
    status = meta.pop("was", "active")
    meta.pop("split_into", None)
    db.execute("UPDATE memories SET status = ?, superseded_by = NULL, meta = ? WHERE id = ?",
               (status, json.dumps(meta), r["id"]))
    return status


def cmd_restore(db, a):
    """Undo a supersede, merge, split or reject: the change that replaced the memory, as a whole."""
    for id_ in a.ids:
        r = get(db, id_)
        if r["status"] in ("active", "proposed"):
            print(f"#{id_} is already {r['status']}")
            continue
        newer = get(db, r["superseded_by"]) if r["superseded_by"] else None
        nm = meta_of(newer) if newer else {}
        if newer and id_ in nm.get("merged_from", []):
            sources, made = nm["merged_from"], [newer["id"]]
        elif newer and nm.get("split_from") == id_:
            sources, made = [id_], meta_of(r).get("split_into", [newer["id"]])
        else:
            sources, made = [id_], []
        for m in made:
            row_ = get(db, m)
            if row_["status"] in ("active", "proposed"):
                retire(db, row_, "rejected")
        back = [f"#{i} ({revive(db, x)})" for i in sources
                if (x := get(db, i))["status"] not in ("active", "proposed")]
        print("restored " + ", ".join(back) + (f"; removed {', '.join(f'#{m}' for m in made)}" if made else ""))
    db.commit()


def current(db, id_):
    r = get(db, id_)
    if r["status"] not in ("active", "proposed"):
        raise SystemExit(f"#{id_} is {r['status']}: only current memories can be replaced")
    return r


def cmd_merge(db, a):
    """Several memories that say the same thing (or overlap) become one entry."""
    olds = [current(db, i) for i in dict.fromkeys(a.ids)]
    if len(olds) < 2:
        raise SystemExit("give at least two memories to merge")
    first = olds[0]
    tags = " ".join(dict.fromkeys(" ".join(r["tags"] for r in olds).split()))
    status = "proposed" if any(r["status"] == "proposed" for r in olds) else "active"
    new_id = insert(db, a.text, a.type or first["type"], a.scope or first["scope"], tags,
                    max(r["confidence"] for r in olds), status=status,
                    meta={"merged_from": [r["id"] for r in olds]})
    for r in olds:
        retire(db, r, "superseded", new_id)
    db.commit()
    print(f"merged {', '.join(f'#{r[0]}' for r in olds)} into #{new_id}"
          + (" (proposed: waiting for the user's review)" if status == "proposed" else ""))


def cmd_split(db, a):
    """A memory that states several things becomes one entry per thing."""
    old = current(db, a.id)
    texts = [t.strip() for t in a.texts if t.strip()]
    if len(texts) < 2:
        raise SystemExit("give at least two texts, one per entry")
    new_ids = [insert(db, t, a.type or old["type"], old["scope"], old["tags"], old["confidence"],
                      status=old["status"], meta={"split_from": old["id"]}) for t in texts]
    retire(db, old, "superseded", new_ids[0], split_into=new_ids)
    db.commit()
    print(f"#{old['id']} split into " + ", ".join(f"#{i}" for i in new_ids))


def cmd_similar(db, a):
    scope = a.scope or claudysha.current_scope()
    groups = similar_groups(db, scope, a.min_score)
    for score, rows in groups[:a.limit]:
        print(f"-- looks alike ({score:.2f})")
        for r in rows:
            print(fmt(r))
        print()
    if not groups:
        print(f"(nothing alike in {scope})")
    elif len(groups) > a.limit:
        print(f"({len(groups) - a.limit} more groups: -n {len(groups)} shows them all)")


def cmd_set_status(status):
    def run(db, a):
        for id_ in a.ids:
            r = get(db, id_)
            if status == "rejected":
                retire(db, r, status)
            else:
                db.execute("UPDATE memories SET status = ? WHERE id = ?", (status, id_))
        db.commit()
        word = {"active": "approved", "rejected": "rejected"}.get(status, status)
        print(f"{word}: " + ", ".join(f"#{i}" for i in a.ids))
    return run


def cmd_edit(db, a):
    r = get(db, a.id)
    db.execute(
        "UPDATE memories SET text = ?, type = ?, scope = ?, tags = ?, confidence = ? WHERE id = ?",
        (a.text or r["text"], a.type or r["type"], a.scope or r["scope"],
         a.tags if a.tags is not None else r["tags"],
         a.confidence if a.confidence is not None else r["confidence"], r["id"]),
    )
    db.commit()
    print(fmt(get(db, r["id"])))


def cmd_review(db, a):
    rows = db.execute("SELECT * FROM memories WHERE status = 'proposed' ORDER BY scope, type, id").fetchall()
    if not rows:
        print("nothing to review")
        return
    if a.list or not sys.stdin.isatty():
        scope = None
        for r in rows:
            if r["scope"] != scope:
                scope = r["scope"]
                print(f"\n## {scope}")
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
        if not path or not Path(path).expanduser().exists():
            return  # Claude Code writes no transcript for a session closed before its first message
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


def every_lines():
    """`memory_every_lines` from the config (0 turns the Stop trigger off)."""
    raw = claudysha.read_config().get("memory_every_lines", "")
    try:
        return int(raw) if raw else EVERY_LINES
    except ValueError:
        return EVERY_LINES


def hook_due(db, payload):
    """SessionEnd and PreCompact always extract. Stop fires after every reply, so it only
    extracts once `every_lines()` new transcript lines piled up since the last extraction."""
    try:
        data = json.loads(payload)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("hook_event_name") != "Stop":
        return True
    every, path = every_lines(), data.get("transcript_path")
    if every <= 0 or not path:
        return False
    row = db.execute("SELECT lines_done FROM ingested WHERE session_id = ?",
                     (data.get("session_id") or Path(path).stem,)).fetchone()
    try:
        with open(Path(path).expanduser(), "rb") as f:
            total = sum(1 for _ in f)
    except OSError:
        return False
    return total - (row["lines_done"] if row else 0) >= every


def cmd_hook(db, a):
    """Entry point for Claude Code hooks: hand the hook JSON to a detached `ingest --hook`."""
    if os.environ.get("MEM_NO_HOOK"):
        return
    payload = sys.stdin.read()
    if not hook_due(db, payload):
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as logf:
        proc = subprocess.Popen([sys.executable, str(ROOT / "mem.py"), "ingest", "--hook"],
                                stdin=subprocess.PIPE, stdout=logf, stderr=logf,
                                start_new_session=True, text=True)
        proc.stdin.write(payload)
        proc.stdin.close()


def rules(db, scope):
    """Approved preferences and feedback of a scope, the most confident first."""
    return db.execute(
        f"""SELECT * FROM memories WHERE scope = ? AND status = 'active'
            AND type IN ({','.join('?' * len(NEEDS_APPROVAL))})
            ORDER BY confidence DESC, id DESC""", (scope, *sorted(NEEDS_APPROVAL))).fetchall()


def commands_note(user):
    """The memory commands, so every session knows them from the start (the claudysha:recall
    skill has the details and the rules). Commands only: never where or how memory is kept."""
    m = claudysha.REPO / "mem"
    return (f"\nMemory commands (`mem` is `{m}`; the claudysha:recall skill has the details):\n"
            "- `mem search <words...> [-s <scope> | -A] [-t <type>]`: several words, exact tokens kept; "
            "`mem get <id...>` shows entries in full, `mem list [-s <scope>] [-t <type>]` the latest\n"
            "- `mem add \"<one fact, self-contained, with the why>\" -t <type> [-s global]`: types fact, "
            "decision, dead_end, event, todo, preference, feedback; default scope is this session's\n"
            "- `mem supersede <id> \"<corrected text>\"`: when a memory turned out wrong or outdated; "
            "`mem merge`, `mem split` for duplicates and entries stating several things\n"
            f"- `mem core show|add|edit|remove`: core memory, changed only with {user}'s OK\n"
            "- `mem backlog list|show|add|update|done`: postponed ideas with a review date\n"
            "- `mem log`, `mem stats`: recent memory runs and errors, counts per scope")


def session_note(db, here):
    """What the SessionStart hook prints for a session in scope `here`."""
    user = claudysha.user_name()
    active, proposed = db.execute(
        """SELECT COUNT(*) FILTER (WHERE status = 'active'),
                  COUNT(*) FILTER (WHERE status = 'proposed') FROM memories""").fetchone()
    out = [f"Long-term memory: you remember {active} things from past sessions. "
           f"This session's scope is `{here}` (the repository, or else the folder, it works in). "
           "Search your memory (claudysha:recall skill) before answering about past work or redoing something: "
           "a search covers this scope and `global` by default."]
    out.append(commands_note(user))
    for scope, title in ((GLOBAL, f"{user}'s preferences and feedback for every session (global)"),
                         (here, f"{user}'s preferences and feedback for `{here}`")):
        rows = rules(db, scope)
        if not rows:
            continue
        out.append(f"\n{title}:")
        out += [f"- {r['text']} (#{r['id']})" for r in rows[:RULES_SHOWN]]
        if len(rows) > RULES_SHOWN:
            out.append(f"- ... {len(rows) - RULES_SHOWN} more: `mem list -s {scope} -t preference` "
                       "and `-t feedback`")
    others = db.execute("""SELECT scope, COUNT(*) FROM memories WHERE status = 'active' AND scope NOT IN (?, ?)
                           GROUP BY scope ORDER BY COUNT(*) DESC""", (here, GLOBAL)).fetchall()
    if others:
        listed = ", ".join(f"`{s}` ({n})" for s, n in others[:OTHER_SCOPES_SHOWN])
        more = f" and {len(others) - OTHER_SCOPES_SHOWN} more" if len(others) > OTHER_SCOPES_SHOWN else ""
        out.append(f"\nOther scopes in your memory: {listed}{more}. Search one when it may help "
                   "(`mem search <words> -s <scope>`, or `-A` for all), but what you find there describes "
                   "that repository or folder: use it as information, never as a rule for this session.")
    if proposed:
        out.append(f"\n{proposed} proposed memories are waiting for {user}'s review: "
                   "mention it once (/claudysha:memory-review).")
    return "\n".join(out)


def cmd_session_start(db, a):
    """SessionStart hook: the store, this scope's rules, and the other scopes."""
    if os.environ.get("MEM_NO_HOOK"):
        return
    data = {}
    with contextlib.suppress(Exception):
        data = json.loads(sys.stdin.read())  # the hook JSON: its `cwd` is the session's folder
    data = data if isinstance(data, dict) else {}
    # The dashboard's session preview names the scope to show (Claude Code never sends this).
    scope = data.get("claudysha_preview_scope")
    if not (isinstance(scope, str) and scope.strip()):
        cwd = data.get("cwd")
        scope = claudysha.scope_of(cwd) if cwd else claudysha.current_scope()
    print(session_note(db, scope.strip()))


def cmd_rescope(db, a):
    """File every memory not in `global` under the scope of the session it came from."""
    scopes = {}
    for r in db.execute("SELECT session_id, transcript_path FROM ingested"):
        if Path(r["transcript_path"]).exists() and session_cwd(r["transcript_path"]):
            scopes[r["session_id"]] = session_scope(r["transcript_path"])
    moves, changes, unresolved = {}, [], []
    for r in db.execute("SELECT * FROM memories WHERE scope != ? ORDER BY id", (GLOBAL,)):
        new = scopes.get(r["session_id"])
        if new is None:
            unresolved.append(r)
        elif new != r["scope"]:
            changes.append((new, r["id"]))
            moves[(r["scope"], new)] = moves.get((r["scope"], new), 0) + 1
    for (old, new), n in sorted(moves.items()):
        print(f"  {old} -> {new}: {n}")
    if not moves:
        print("  nothing to move")
    if unresolved:
        print(f"\n{len(unresolved)} without a session transcript (scope kept; fix with `mem edit <id> -s <scope>`):")
        for r in unresolved:
            print(f"  {fmt(r)[:160]}")
    if a.apply:
        db.executemany("UPDATE memories SET scope = ? WHERE id = ?", changes)
        db.commit()
        print(f"\nmoved {len(changes)} memories")
    elif changes:
        print(f"\ndry run: {len(changes)} memories would move. Run again with --apply.")


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


def cmd_log(db, a):
    """The last lines of the extraction log (runs, errors), so nobody needs to open it."""
    try:
        lines = LOG_PATH.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    print("\n".join(lines[-a.lines:]) if lines else "(no extraction has run yet)")


def cmd_stats(db, a):
    for row in db.execute("SELECT status, COUNT(*) FROM memories GROUP BY status"):
        print(f"  {row[0]}: {row[1]}")
    print("by scope/type (active + proposed):")
    for row in db.execute("""SELECT scope, type, COUNT(*) FROM memories
                             WHERE status IN ('active','proposed') GROUP BY 1, 2 ORDER BY 1, 2"""):
        print(f"  {row[0]:<16} {row[1]:<11} {row[2]}")
    print(f"transcripts ingested: {db.execute('SELECT COUNT(*) FROM ingested').fetchone()[0]}")


def cmd_export(db, a):
    for r in db.execute("SELECT * FROM memories ORDER BY id"):
        print(json.dumps(dict(r), ensure_ascii=False))


def main():
    # `mem core ...` and `mem backlog ...`: the memory module's other parts, one command for sessions.
    if len(sys.argv) > 1 and sys.argv[1] in ("core", "backlog"):
        if sys.argv[1] == "core":
            core.cli(sys.argv[2:])
        else:
            import backlog
            backlog.cli(sys.argv[2:])
        return
    p = argparse.ArgumentParser(prog="mem", description="Claude's memory: long-term memories (the commands "
                                "below), core memory (`mem core -h`) and the backlog (`mem backlog -h`).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="full-text search (words OR-ed, exact tokens kept)")
    s.add_argument("query", nargs="+")
    s.add_argument("-s", "--scope", action="append",
                   help="search this scope (repeatable; default: this session's scope and global)")
    s.add_argument("-A", "--all-scopes", action="store_true", help="search every scope")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("-a", "--all", action="store_true", help="include superseded and rejected")
    s.add_argument("-f", "--full", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("add", help="store a memory")
    s.add_argument("text")
    s.add_argument("-t", "--type", choices=TYPES, default="fact")
    s.add_argument("-s", "--scope", help=f"default: this session's scope; {GLOBAL} for every session")
    s.add_argument("--tags", default="")
    s.add_argument("-c", "--confidence", type=float, default=0.9)
    s.add_argument("--force", action="store_true", help="add even if a near-duplicate exists")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("get", help="show memories in full")
    s.add_argument("ids", nargs="+", type=int)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("list", help="latest memories")
    s.add_argument("-s", "--scope")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("-a", "--all", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("supersede", help="replace a memory with a newer one")
    s.add_argument("id", type=int)
    s.add_argument("text", nargs="?")
    s.add_argument("--by", type=int, help="existing memory that replaces it")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-s", "--scope")
    s.set_defaults(fn=cmd_supersede)

    s = sub.add_parser("edit", help="change a memory in place")
    s.add_argument("id", type=int)
    s.add_argument("--text")
    s.add_argument("-t", "--type", choices=TYPES)
    s.add_argument("-s", "--scope")
    s.add_argument("--tags")
    s.add_argument("-c", "--confidence", type=float)
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("merge", help="turn memories that repeat or overlap into one entry")
    s.add_argument("ids", nargs="+", type=int)
    s.add_argument("--text", required=True, help="the one entry that replaces them")
    s.add_argument("-t", "--type", choices=TYPES, help="default: the first memory's")
    s.add_argument("-s", "--scope", help="default: the first memory's")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("split", help="turn a memory that states several things into one entry per thing")
    s.add_argument("id", type=int)
    s.add_argument("texts", nargs="+", metavar="text")
    s.add_argument("-t", "--type", choices=TYPES, help="default: the memory's")
    s.set_defaults(fn=cmd_split)

    s = sub.add_parser("restore", help="undo the supersede, merge, split or reject that replaced memories")
    s.add_argument("ids", nargs="+", type=int)
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("similar", help="groups of memories that look alike (duplicates, overlaps)")
    s.add_argument("-s", "--scope", help="default: this session's scope (compared with global too)")
    s.add_argument("-n", "--limit", type=int, default=30, help="groups shown")
    s.add_argument("--min-score", type=float, default=0.3, help="0 to 1: share of words in common")
    s.set_defaults(fn=cmd_similar)

    for name, status, what in (("approve", "active", "mark proposed memories active"),
                               ("reject", "rejected", "reject proposed memories, or forget wrong ones")):
        s = sub.add_parser(name, help=what)
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
    s.add_argument("--project-dir", help="only this folder, e.g. -home-me-code")
    s.set_defaults(fn=cmd_backfill)

    sub.add_parser("hook", help="for Claude Code hooks: run ingest in the background").set_defaults(fn=cmd_hook)
    sub.add_parser("session-start", help="for the SessionStart hook: print this scope's rules").set_defaults(
        fn=cmd_session_start)

    s = sub.add_parser("rescope", help="file memories under the scope of the session they came from")
    s.add_argument("--apply", action="store_true", help="write the changes (default: show them)")
    s.set_defaults(fn=cmd_rescope)

    s = sub.add_parser("log", help="recent extraction runs and errors")
    s.add_argument("-n", "--lines", type=int, default=30)
    s.set_defaults(fn=cmd_log)
    sub.add_parser("stats", help="counts").set_defaults(fn=cmd_stats)
    sub.add_parser("export", help="dump all memories as JSONL").set_defaults(fn=cmd_export)

    if len(sys.argv) == 1:
        p.print_help()
        return
    a = p.parse_args()
    if a.cmd == "ingest" and not a.hook and not a.transcript:
        p.error("ingest needs a transcript path or --hook")
    with contextlib.closing(connect()) as db:
        a.fn(db, a)


if __name__ == "__main__":
    main()

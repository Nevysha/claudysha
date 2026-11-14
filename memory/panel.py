"""Memory module: its dashboard panels (see plugin/dashboard.py for the spec).

Two panels, in memory/web/memory.js: the review queue (proposed memories) and the memory
browser. The routes below reuse the store's own functions, so the dashboard and `mem` behave
the same. Browsing doesn't count as use (only a session's `mem search` does), and every write
takes the plugin's shared lock, so it never overlaps a `claudysha load`.
"""
import contextlib
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "store"))
sys.path.insert(0, str(HERE))
import claudysha  # noqa: E402
import mem  # noqa: E402
import review  # noqa: E402

STATUSES = ("active", "proposed", "superseded", "rejected")
SET_STATUSES = ("active", "rejected")
LIST_LIMIT = 50


@contextlib.contextmanager
def db():
    """A connection per request: `load` may replace the data folder while the server runs."""
    conn = mem.connect()
    try:
        yield conn
    finally:
        conn.close()


def row(r):
    d = dict(r)
    d.pop("rank", None)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except ValueError:
        d["meta"] = {}
    return d


def ids_of(body):
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ValueError("give `ids`, a list of memory numbers")
    return ids


def source_of(conn, r):
    """Where a memory came from: the session's title when there is one."""
    source, _ = review.context_of(conn, r, lambda *a, **k: [])
    return source


# ---------------------------------------------------------------- reading

def summary(query, body):
    with db() as conn:
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM memories GROUP BY status").fetchall())
        scopes = conn.execute("""SELECT scope, COUNT(*) AS n FROM memories WHERE status IN ('active', 'proposed')
                                 GROUP BY scope ORDER BY (scope = ?) DESC, n DESC""", (mem.GLOBAL,)).fetchall()
        return {"counts": {s: counts.get(s, 0) for s in STATUSES},
                "scopes": [{"scope": s, "count": n} for s, n in scopes],
                "types": mem.TYPES, "needs_approval": sorted(mem.NEEDS_APPROVAL), "global": mem.GLOBAL}


def proposed(query, body):
    """The review queue, with where each memory came from and what looks related."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM memories WHERE status = 'proposed' ORDER BY scope, type, id").fetchall()
        out = []
        for r in rows:
            source, related = review.context_of(conn, r, mem.search)
            out.append({**row(r), "source_label": source, "related": [row(x) for x in related]})
        return {"items": out}


def listing(query, body):
    """Memories by filters; with `q`, a full-text search ranked like `mem search`."""
    status = query.get("status") or "current"
    if status not in (*STATUSES, "current", "all"):
        raise ValueError(f"unknown status {status!r}")
    limit = max(1, min(int(query.get("limit") or LIST_LIMIT), 200))
    offset = max(0, int(query.get("offset") or 0))
    scope, type_, source = query.get("scope") or None, query.get("type") or None, query.get("source") or None
    unused_days = int(query["unused_days"]) if query.get("unused_days") else None
    if type_ and type_ not in mem.TYPES:
        raise ValueError(f"unknown type {type_!r}")
    with db() as conn:
        if (query.get("q") or "").strip():
            rows = mem.search(conn, query["q"], [scope] if scope else None, type_, limit=200,
                              include_old=status not in ("current", "active", "proposed"))
            keep = (lambda r: r["status"] in ("active", "proposed")) if status == "current" else \
                   (lambda r: True) if status == "all" else (lambda r: r["status"] == status)
            rows = [r for r in rows if keep(r) and (not source or r["source"] == source)
                    and (unused_days is None or (r["use_count"] == 0 and r["created_at"] < cutoff(unused_days)))]
            return {"items": [row(r) for r in rows[offset:offset + limit]], "total": len(rows), "ranked": True}
        where, args = ["1=1"], []
        if status == "current":
            where.append("status IN ('active', 'proposed')")
        elif status != "all":
            where.append("status = ?")
            args.append(status)
        for col, val in (("scope", scope), ("type", type_), ("source", source)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if unused_days is not None:
            where.append("use_count = 0 AND created_at < ?")
            args.append(cutoff(unused_days))
        sql = " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {sql}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM memories WHERE {sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                            (*args, limit, offset)).fetchall()
        return {"items": [row(r) for r in rows], "total": total, "ranked": False}


def cutoff(days):
    return (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def item(query, body):
    """One memory in full: where it came from, what replaced it, what it replaced."""
    with db() as conn:
        r = mem.get(conn, int(query.get("id") or 0))
        newer = mem.get(conn, r["superseded_by"]) if r["superseded_by"] else None
        older = conn.execute("SELECT * FROM memories WHERE superseded_by = ? ORDER BY id", (r["id"],)).fetchall()
        related = [x for x in mem.search(conn, r["text"], limit=4) if x["id"] != r["id"]][:3]
        return {"item": {**row(r), "source_label": source_of(conn, r)},
                "superseded_by": row(newer) if newer else None,
                "supersedes": [row(x) for x in older], "related": [row(x) for x in related]}


# ---------------------------------------------------------------- writing

def set_status(query, body):
    """Approve (`active`) or reject memories; `active` also restores a rejected one."""
    ids, status = ids_of(body), body.get("status")
    if status not in SET_STATUSES:
        raise ValueError(f"status must be one of {', '.join(SET_STATUSES)}")
    with claudysha.home_lock(), db() as conn:
        for i in ids:
            if mem.get(conn, i)["status"] == "superseded":
                raise ValueError(f"#{i} was superseded: change the memory that replaced it instead")
        conn.executemany("UPDATE memories SET status = ? WHERE id = ?", [(status, i) for i in ids])
        conn.commit()
    return {"ids": ids, "status": status}


def edit(query, body):
    """Change a memory's text, type, scope, tags or confidence in place (like `mem edit`)."""
    fields = {}
    if "text" in body:
        if not str(body["text"]).strip():
            raise ValueError("the text can't be empty")
        fields["text"] = str(body["text"]).strip()
    if "type" in body:
        if body["type"] not in mem.TYPES:
            raise ValueError(f"unknown type {body['type']!r}")
        fields["type"] = body["type"]
    if "scope" in body:
        if not str(body["scope"]).strip():
            raise ValueError("the scope can't be empty (use global for every session)")
        fields["scope"] = str(body["scope"]).strip()
    if "tags" in body:
        fields["tags"] = " ".join(str(body["tags"]).lower().split())
    if "confidence" in body:
        c = float(body["confidence"])
        if not 0 <= c <= 1:
            raise ValueError("confidence goes from 0 to 1")
        fields["confidence"] = c
    if not fields:
        raise ValueError("nothing to change")
    with claudysha.home_lock(), db() as conn:
        r = mem.get(conn, int(body.get("id") or 0))
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE memories SET {cols} WHERE id = ?", (*fields.values(), r["id"]))
        conn.commit()
        return {"item": row(mem.get(conn, r["id"]))}


def supersede(query, body):
    """Replace a memory with a newer text (like `mem supersede`): the old one is kept, marked."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise ValueError("give the new text")
    with claudysha.home_lock(), db() as conn:
        old = mem.get(conn, int(body.get("id") or 0))
        if old["status"] not in ("active", "proposed"):
            raise ValueError(f"#{old['id']} is {old['status']}: only current memories can be superseded")
        new_id = mem.insert(conn, text, old["type"], old["scope"], old["tags"], old["confidence"])
        conn.execute("UPDATE memories SET status = 'superseded', superseded_by = ? WHERE id = ?", (new_id, old["id"]))
        conn.commit()
        return {"old": old["id"], "new": new_id}


# ---------------------------------------------------------------- overview

def last_extraction():
    try:
        lines = mem.LOG_PATH.read_text(errors="replace").splitlines()
    except OSError:
        return None
    return lines[-1] if lines else None


def overview_cards():
    with db() as conn:
        active, proposed_n, scopes = conn.execute(
            """SELECT COUNT(*) FILTER (WHERE status = 'active'), COUNT(*) FILTER (WHERE status = 'proposed'),
                      COUNT(DISTINCT scope) FILTER (WHERE status = 'active') FROM memories""").fetchone()
        sessions = conn.execute("SELECT COUNT(*) FROM ingested").fetchone()[0]
    last = last_extraction()
    return [
        {"label": "Memories", "value": active, "hint": f"across {scopes} scopes", "panel": "memories"},
        {"label": "Waiting for review", "value": proposed_n, "hint": "proposed preferences and feedback",
         "panel": "review", "accent": proposed_n > 0},
        {"label": "Sessions read", "value": sessions,
         "hint": f"last run {last[:16].replace('T', ' ')}" if last else "no extraction yet"},
    ]


def spec():
    return {
        "static": HERE / "web",
        "scripts": ["memory.js"],
        "routes": {
            ("GET", "/api/memory/summary"): summary,
            ("GET", "/api/memory/proposed"): proposed,
            ("GET", "/api/memory/list"): listing,
            ("GET", "/api/memory/item"): item,
            ("POST", "/api/memory/status"): set_status,
            ("POST", "/api/memory/edit"): edit,
            ("POST", "/api/memory/supersede"): supersede,
        },
        "overview": overview_cards,
        "scopes": lambda: summary({}, {})["scopes"],
        "session_labels": {"memory/core.py": "Core memory", "mem": "Long-term memory",
                           "memory/backlog.py": "Backlog"},
    }

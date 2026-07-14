"""Persistent SQLite cache of events with delta sync, new/dirty flags, and
pruning. See the plan for the full algorithm -- short version:

- `sync_candidates()` reconciles a fresh batch of scraped candidates against
  the DB: exact matches refresh in place, same title+venue-but-different-date
  matches are treated as reschedules (updated in place), unmatched candidates
  are inserted as new (flips the `dirty` flag), and DB rows from a re-fetched
  source that weren't touched this run are deleted (no longer listed).
- `prune_and_clean()` is meant to run once at launch, only when `dirty` is
  set: deletes expired rows, clears all `is_new` flags, clears `dirty`.
- `get_active_events()` is what rendering reads from -- always filtered to
  `end_date >= today` regardless of prune/dirty state.
"""

import hashlib
import os
import re
import sqlite3
import time
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "events.db")


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def fingerprint(title, venue, date_str):
    key = f"{normalize(title)}|{normalize(venue)}|{(date_str or '').strip().lower()}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def get_connection(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            date TEXT,
            end_date TEXT NOT NULL,
            time TEXT,
            venue TEXT,
            neighborhood TEXT,
            category TEXT,
            bucket TEXT,
            url TEXT,
            description TEXT,
            source TEXT,
            is_new INTEGER NOT NULL DEFAULT 0,
            first_seen_at REAL,
            last_seen_at REAL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (dirty INTEGER NOT NULL DEFAULT 0)")
    if conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
        conn.execute("INSERT INTO meta (dirty) VALUES (0)")
    conn.commit()


def is_dirty(conn):
    return bool(conn.execute("SELECT dirty FROM meta").fetchone()[0])


def prune_and_clean(conn):
    """Run on launch when dirty: drop expired events, clear new flags, clear dirty."""
    today = date.today().isoformat()
    conn.execute("DELETE FROM events WHERE end_date < ?", (today,))
    conn.execute("UPDATE events SET is_new = 0")
    conn.execute("UPDATE meta SET dirty = 0")
    conn.commit()


def sync_candidates(conn, candidates, fetched_sources):
    """Reconcile freshly scraped candidates into the DB. Returns counts dict."""
    today = date.today().isoformat()
    now = time.time()
    touched_ids = set()
    new_count = 0

    for c in candidates:
        end_date = (c.get("end_date") or "").strip()
        if not end_date or end_date < today:
            continue

        fp = fingerprint(c.get("title", ""), c.get("venue", ""), c.get("date", ""))
        row = conn.execute("SELECT * FROM events WHERE fingerprint = ?", (fp,)).fetchone()

        if row:
            conn.execute(
                """UPDATE events SET description=?, url=?, time=?, neighborhood=?,
                   category=?, last_seen_at=? WHERE id=?""",
                (
                    c.get("description", ""), c.get("url", ""), c.get("time", ""),
                    c.get("neighborhood", ""), c.get("category", ""), now, row["id"],
                ),
            )
            touched_ids.add(row["id"])
            continue

        # Reschedule check: same normalized title+venue, still-active row from
        # a source we refetched this run, not yet touched by another candidate.
        norm_title = normalize(c.get("title", ""))
        norm_venue = normalize(c.get("venue", ""))
        reschedule_row = None
        for r in conn.execute(
            "SELECT * FROM events WHERE source = ? AND end_date >= ?",
            (c.get("source", ""), today),
        ):
            if r["id"] in touched_ids:
                continue
            if normalize(r["title"]) == norm_title and normalize(r["venue"]) == norm_venue:
                reschedule_row = r
                break

        if reschedule_row:
            conn.execute(
                """UPDATE events SET date=?, end_date=?, fingerprint=?, description=?,
                   url=?, time=?, neighborhood=?, category=?, last_seen_at=? WHERE id=?""",
                (
                    c.get("date", ""), end_date, fp, c.get("description", ""),
                    c.get("url", ""), c.get("time", ""), c.get("neighborhood", ""),
                    c.get("category", ""), now, reschedule_row["id"],
                ),
            )
            touched_ids.add(reschedule_row["id"])
            continue

        cur = conn.execute(
            """INSERT INTO events (fingerprint, title, date, end_date, time, venue,
               neighborhood, category, bucket, url, description, source, is_new,
               first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (
                fp, c.get("title", ""), c.get("date", ""), end_date, c.get("time", ""),
                c.get("venue", ""), c.get("neighborhood", ""), c.get("category", ""),
                "", c.get("url", ""), c.get("description", ""), c.get("source", ""),
                now, now,
            ),
        )
        touched_ids.add(cur.lastrowid)
        new_count += 1

    if new_count:
        conn.execute("UPDATE meta SET dirty = 1")

    # Reconcile deletions: only for sources actually fetched this run.
    deleted_count = 0
    if fetched_sources:
        placeholders = ",".join("?" for _ in fetched_sources)
        rows = conn.execute(
            f"SELECT id FROM events WHERE source IN ({placeholders}) AND end_date >= ?",
            (*fetched_sources, today),
        ).fetchall()
        for r in rows:
            if r["id"] not in touched_ids:
                conn.execute("DELETE FROM events WHERE id = ?", (r["id"],))
                deleted_count += 1

    conn.commit()
    return {"new": new_count, "deleted": deleted_count, "touched": len(touched_ids)}


def update_buckets(conn, concert_ids, event_ids):
    if concert_ids:
        placeholders = ",".join("?" for _ in concert_ids)
        conn.execute(f"UPDATE events SET bucket='concerts' WHERE id IN ({placeholders})", concert_ids)
    if event_ids:
        placeholders = ",".join("?" for _ in event_ids)
        conn.execute(f"UPDATE events SET bucket='events' WHERE id IN ({placeholders})", event_ids)
    conn.commit()


def get_active_events(conn):
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM events WHERE end_date >= ? ORDER BY date", (today,)
    ).fetchall()
    return [dict(r) for r in rows]

"""Manual-trigger entrypoint: fetch NYC events, rank them by taste, render the
digest, and serve it locally with working Like/Dislike buttons.

Usage: python app.py
"""

import html
import json
import os
import time
import webbrowser
from datetime import date, datetime

import claude_agent
import db
from sources import SOURCES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PREFERENCES_PATH = os.path.join(DATA_DIR, "preferences.json")
EVENTS_LATEST_PATH = os.path.join(DATA_DIR, "events_latest.json")
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "page.html")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
MAX_PER_TAB = 100


def load_preferences():
    if not os.path.exists(PREFERENCES_PATH):
        return []
    with open(PREFERENCES_PATH) as f:
        return json.load(f)


def taste_profile(prefs):
    liked = [f"{p['title']} ({p.get('category', '')})" for p in prefs if p.get("action") == "like"]
    disliked = [f"{p['title']} ({p.get('category', '')})" for p in prefs if p.get("action") == "dislike"]
    return liked, disliked


def has_future_end_date(event):
    end_date = (event.get("end_date") or "").strip()
    try:
        return date.fromisoformat(end_date) >= date.today()
    except ValueError:
        return False


def fetch_all_candidates():
    """Fetch every source. Returns (candidates, fetched_source_names) --
    fetched_source_names only includes sources that didn't hard-fail, so
    db.sync_candidates() knows which sources are safe to reconcile deletions
    against."""
    candidates = []
    seen_fingerprints = set()
    fetched_sources = []
    for source in SOURCES:
        print(f"Fetching {source['name']}...")
        events = claude_agent.extract_events(source)
        if events is None:
            print("  -> fetch failed, skipping (leaving its cached events untouched)")
            continue
        fetched_sources.append(source["name"])
        print(f"  -> {len(events)} events")
        for e in events:
            if not e.get("title") or not has_future_end_date(e):
                continue
            fp = db.fingerprint(e.get("title", ""), e.get("venue", ""), e.get("date", ""))
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            candidates.append(e)
    return candidates, fetched_sources


def render_card(event, liked_ids, disliked_ids):
    title = html.escape(event.get("title", "Untitled"))
    url = html.escape(event.get("url", ""), quote=True)
    date = html.escape(event.get("date", ""))
    time_ = html.escape(event.get("time", ""))
    venue = html.escape(event.get("venue", ""))
    neighborhood = html.escape(event.get("neighborhood", ""))
    category = html.escape(event.get("category", ""))
    source = html.escape(event.get("source", ""))
    desc = html.escape(event.get("description", ""))
    eid = event["id"]

    meta_parts = [p for p in [date, time_, venue, neighborhood] if p]
    meta = " &middot; ".join(meta_parts)

    like_active = " active" if eid in liked_ids else ""
    dislike_active = " active" if eid in disliked_ids else ""

    title_html = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
    new_badge = '<span class="new-badge">New</span>' if event.get("is_new") else ""

    return f"""
<div class="card" data-title="{title}" data-category="{category}">
  <h2>{title_html}{new_badge}</h2>
  <div class="meta">{meta}</div>
  <div><span class="tag">{category}</span><span class="tag">{source}</span></div>
  <div class="desc">{desc}</div>
  <div class="actions">
    <button class="like{like_active}" onclick="react('{eid}','like',this)">Like</button>
    <button class="dislike{dislike_active}" onclick="react('{eid}','dislike',this)">Dislike</button>
  </div>
</div>"""


def render_section(events, liked_ids, disliked_ids, empty_message):
    if events:
        return "\n".join(render_card(e, liked_ids, disliked_ids) for e in events)
    return f'<div class="empty">{empty_message}</div>'


def render_page(concerts, events, prefs):
    liked_ids = {p["id"] for p in prefs if p.get("action") == "like"}
    disliked_ids = {p["id"] for p in prefs if p.get("action") == "dislike"}

    with open(TEMPLATE_PATH) as f:
        template = f.read()

    events_html = render_section(events, liked_ids, disliked_ids, "No events found this run.")
    concerts_html = render_section(concerts, liked_ids, disliked_ids, "No concerts found this run.")

    date_str = datetime.now().strftime("%A, %B %d, %Y")
    page = (
        template.replace("<!--EVENTS-->", events_html)
        .replace("<!--CONCERTS-->", concerts_html)
        .replace("<!--EVENTS_COUNT-->", str(len(events)))
        .replace("<!--CONCERTS_COUNT-->", str(len(concerts)))
        .replace("<!--DATE-->", date_str)
    )

    with open(INDEX_PATH, "w") as f:
        f.write(page)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = db.get_connection()

    if db.is_dirty(conn):
        print("DB is dirty from a previous run -- pruning expired events and clearing NEW flags...")
        db.prune_and_clean(conn)

    prefs = load_preferences()
    liked, disliked = taste_profile(prefs)

    candidates, fetched_sources = fetch_all_candidates()
    print(f"\n{len(candidates)} total candidates after dedupe (future events only).")

    sync_stats = db.sync_candidates(conn, candidates, fetched_sources)
    print(f"Synced to DB: {sync_stats['new']} new, {sync_stats['deleted']} removed, {sync_stats['touched']} touched.")

    active_events = db.get_active_events(conn)
    for e in active_events:
        e["id"] = str(e["id"])  # curate_events/Claude deal in string ids

    if active_events:
        print(f"Classifying and ranking {len(active_events)} active events against your taste history...")
        concert_ids, event_ids = claude_agent.curate_events(active_events, liked, disliked, cap=MAX_PER_TAB)
        by_id = {e["id"]: e for e in active_events}
        concerts = [by_id[i] for i in concert_ids if i in by_id]
        events = [by_id[i] for i in event_ids if i in by_id]
        db.update_buckets(conn, [int(i) for i in concert_ids], [int(i) for i in event_ids])
    else:
        concerts, events = [], []

    conn.close()

    with open(EVENTS_LATEST_PATH, "w") as f:
        json.dump({"generated_at": time.time(), "concerts": concerts, "events": events}, f, indent=2)

    render_page(concerts, events, prefs)
    print(f"\nWrote {INDEX_PATH} with {len(concerts)} concerts and {len(events)} events.")

    import server

    url = "http://127.0.0.1:5050/"
    webbrowser.open(url)
    print(f"Serving at {url} (Ctrl+C to stop)")
    server.run_server()


if __name__ == "__main__":
    main()

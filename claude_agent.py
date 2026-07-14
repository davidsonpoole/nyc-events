"""Wraps `claude -p` headless invocations used by the pipeline.

Uses the Claude Code CLI in print/non-interactive mode so the app reuses the
user's existing `claude` login instead of requiring a separate Anthropic API
key. Each call is scoped to the WebFetch tool only and structured output is
requested via --json-schema so we get parsed JSON back directly.
"""

import json
import subprocess

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "time": {"type": "string"},
                    "venue": {"type": "string"},
                    "neighborhood": {"type": "string"},
                    "category": {"type": "string"},
                    "url": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["events"],
}

CURATE_SCHEMA = {
    "type": "object",
    "properties": {
        "concerts": {"type": "array", "items": {"type": "string"}},
        "events": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["concerts", "events"],
}


def _run_claude(prompt, schema, timeout=120):
    cmd = [
        "claude",
        "-p",
        prompt,
        "--json-schema",
        json.dumps(schema),
        "--output-format",
        "json",
        "--allowedTools",
        "WebFetch",
        "--no-session-persistence",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return envelope.get("structured_output")


def extract_events(source):
    """Fetch a single source URL and extract upcoming events from it.

    Returns None on a hard failure (timeout/error/bad JSON) so callers can
    tell "fetch failed" apart from "fetch succeeded, page had zero events" --
    that distinction matters for safe deletion-reconciliation in db.py.
    """
    prompt = (
        f"Fetch {source['url']} and extract EVERY event happening in the next 30 "
        "days that is visible on the page (concerts, meetups, classes, markets, "
        "exhibits, comedy shows, etc). Don't stop at a handful -- list as many as "
        "the page actually shows, up to 50. For each event capture: title, date "
        "(display text, e.g. 'July 12, 13, 16 & 18, 2026'), end_date (the LAST "
        "day the event occurs, as a strict ISO 8601 date YYYY-MM-DD -- for a "
        "single-day event this equals that day; skip the event if you can't "
        "confidently determine an end_date), time, venue, neighborhood, "
        "category, a direct url if one is visible (otherwise use the source "
        f"page url {source['url']}), and a one-sentence description. Skip "
        "anything without a clear title. If you can't find any events, return "
        "an empty events list rather than guessing."
    )
    result = _run_claude(prompt, EXTRACT_SCHEMA, timeout=180)
    if result is None:
        return None
    events = result.get("events", [])
    for e in events:
        e["source"] = source["name"]
        e.setdefault("category", source["category"])
    return events


MAJOR_VENUES = (
    "madison square garden", "msg", "barclays center", "ubs arena",
    "radio city music hall", "yankee stadium", "citi field",
    "forest hills stadium", "prudential center", "nassau coliseum",
    "beacon theatre", "terminal 5",
)


def curate_events(candidates, liked, disliked, cap=100):
    """Classify candidates into concerts vs. local events, and rank each
    bucket by relevance to the user's taste history. Returns
    (concert_ids, event_ids), each ordered most-to-least relevant."""
    if not candidates:
        return [], []

    taste_lines = []
    if liked:
        taste_lines.append("Liked in the past: " + "; ".join(liked))
    if disliked:
        taste_lines.append("Disliked in the past: " + "; ".join(disliked))
    taste_block = "\n".join(taste_lines) if taste_lines else "No history yet -- use general judgment."

    candidate_block = json.dumps(
        [
            {
                "id": c["id"],
                "title": c["title"],
                "date": c.get("date", ""),
                "venue": c.get("venue", ""),
                "category": c.get("category", ""),
                "description": c.get("description", ""),
                "source": c.get("source", ""),
                "likely_bucket": (
                    "concerts"
                    if c.get("source") == "Songkick NYC"
                    or any(v in (c.get("venue") or "").lower() for v in MAJOR_VENUES)
                    else "unknown"
                ),
            }
            for c in candidates
        ]
    )

    prompt = (
        "You are sorting a candidate list of NYC events into two tabs for a "
        "daily digest: 'concerts' and 'events'.\n\n"
        "'concerts' = big-name touring concerts, large comedy specials/famous "
        "comedians, or anything at a major arena/stadium/large theater (e.g. "
        "Madison Square Garden, Barclays Center, UBS Arena, Radio City Music "
        "Hall, Yankee Stadium, Citi Field, Forest Hills Stadium). Treat "
        "everything with likely_bucket 'concerts' as a concert unless it's "
        "obviously wrong. 'events' = everything else -- local/community "
        "events, meetups, markets, gallery openings, small venue shows, "
        "classes, etc.\n\n"
        "If the same real-world event/artist appears more than once across "
        "different candidates (e.g. picked up from two sources), include it "
        "only once -- prefer the Songkick-sourced id for concerts if there's "
        "a duplicate.\n\n"
        f"User taste history:\n{taste_block}\n\n"
        f"Candidates (JSON): {candidate_block}\n\n"
        "Return two id lists: 'concerts' and 'events', each ordered from most "
        f"to least relevant to this specific person, each capped at {cap} ids. "
        "Drop events clearly similar to ones they disliked."
    )
    result = _run_claude(prompt, CURATE_SCHEMA, timeout=240)
    valid_ids = {c["id"] for c in candidates}
    if not result:
        # Fallback: everything from Songkick is a concert, rest are events.
        concerts = [c["id"] for c in candidates if c.get("source") == "Songkick NYC"]
        events = [c["id"] for c in candidates if c.get("source") != "Songkick NYC"]
        return concerts[:cap], events[:cap]

    concerts = [i for i in result.get("concerts", []) if i in valid_ids][:cap]
    events = [i for i in result.get("events", []) if i in valid_ids][:cap]
    return concerts, events

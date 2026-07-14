# NYC Events

A personal digest of NYC events and concerts. Fetches listings from a curated
set of sources, classifies and ranks them against your like/dislike history,
and renders a local web page with two tabs: Events and Concerts.

## How it works

1. `app.py` fetches each source in `sources.py` using the `claude` CLI in
   headless mode (`claude -p`) with the WebFetch tool, extracting upcoming
   events as structured JSON.
2. Fresh results are reconciled into a local SQLite database
   (`data/events.db`) instead of being thrown away each run:
   - Unchanged events are left alone.
   - An event that reappears under the same title/venue but a different date
     is treated as rescheduled and updated in place.
   - Genuinely new events are inserted and flagged "New".
   - Events that disappear from a source that was successfully refetched are
     removed (canceled/delisted).
3. All currently upcoming events (not just this run's fresh scrape) are
   classified into Concerts vs. Events and ranked against your taste history
   via another `claude -p` call.
4. The result is rendered to `index.html` and served locally with working
   Like/Dislike buttons.

Like/Dislike feedback is stored in `data/preferences.json` and factored into
ranking on subsequent runs.

## Requirements

- Python 3
- The `claude` CLI, logged in (this app shells out to `claude -p`; it does
  not need a separate `ANTHROPIC_API_KEY`)

## Setup

```
pip3 install -r requirements.txt
```

## Usage

```
python3 app.py
```

This fetches all sources, updates the local database, renders the digest,
and opens it in your browser at `http://127.0.0.1:5050/`. The process keeps
running to serve the page and handle Like/Dislike clicks; stop it with
Ctrl+C.

Each run costs a small amount of Claude usage (multiple `claude -p` calls:
one per source plus one classification/ranking pass), drawn from your
`claude` login's usage rather than separate API billing.

## Files

- `app.py` -- entrypoint; orchestrates fetch, DB sync, classify/rank, render
- `sources.py` -- curated list of source URLs and categories
- `claude_agent.py` -- wraps `claude -p` calls for extraction and
  classification/ranking
- `db.py` -- SQLite persistence: delta sync, pruning, new/dirty flag logic
- `server.py` -- local Flask server: serves the page and the feedback
  endpoint
- `templates/page.html` -- page layout and styling
- `data/events.db` -- local event cache (not committed)
- `data/preferences.json` -- like/dislike history
- `data/events_latest.json` -- snapshot of the most recent run's output, for
  debugging

## Notes

- Sources are fetched independently; if one fails, its cached events are
  left untouched rather than being deleted.
- The database is pruned (expired events removed, "New" flags cleared) at
  the start of a run only if it was left in a "dirty" state (i.e. new events
  were added) by the previous run.
- This is a manual-trigger tool for now. Scheduling and email delivery are
  not yet implemented.

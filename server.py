"""Tiny local Flask server: serves the generated digest and captures feedback."""

import json
import os
import time

from flask import Flask, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
PREFERENCES_PATH = os.path.join(BASE_DIR, "data", "preferences.json")

app = Flask(__name__)


def load_preferences():
    if not os.path.exists(PREFERENCES_PATH):
        return []
    with open(PREFERENCES_PATH) as f:
        return json.load(f)


def save_preferences(prefs):
    os.makedirs(os.path.dirname(PREFERENCES_PATH), exist_ok=True)
    with open(PREFERENCES_PATH, "w") as f:
        json.dump(prefs, f, indent=2)


@app.route("/")
def index():
    if not os.path.exists(INDEX_PATH):
        return "No digest generated yet -- run app.py first.", 404
    with open(INDEX_PATH) as f:
        return f.read()


@app.route("/feedback", methods=["POST"])
def feedback():
    body = request.get_json(force=True)
    event_id = body.get("id")
    action = body.get("action")
    if not event_id or action not in ("like", "dislike"):
        return jsonify({"status": "error", "message": "invalid payload"}), 400

    prefs = load_preferences()
    prefs = [p for p in prefs if p.get("id") != event_id]
    prefs.append(
        {
            "id": event_id,
            "title": body.get("title", ""),
            "category": body.get("category", ""),
            "action": action,
            "timestamp": time.time(),
        }
    )
    save_preferences(prefs)
    return jsonify({"status": "ok"})


def run_server(port=5050):
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    run_server()

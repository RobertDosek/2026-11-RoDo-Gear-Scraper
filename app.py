"""Small web app serving the guitar watchlist page and its JSON API."""

import os
import threading

from flask import Flask, abort, jsonify, request, send_from_directory

import db
import scraper

app = Flask(__name__, static_folder="static", static_url_path="/static")
refresh_lock = threading.Lock()


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/ads")
def get_ads():
    return jsonify({"ads": db.list_ads(), "last_run": db.last_run()})


@app.post("/api/ads/<ad_id>")
def set_ad(ad_id: str):
    body = request.get_json(force=True)
    target = body.get("target_price")
    try:
        ad = db.update_ad(ad_id, body.get("status", ""), int(target) if target not in (None, "") else None)
    except ValueError as e:
        abort(400, str(e))
    if ad is None:
        abort(404)
    return jsonify(ad)


@app.post("/api/refresh")
def refresh():
    if not refresh_lock.acquire(blocking=False):
        abort(409, "A check is already running.")
    try:
        result = db.sync(scraper.scrape())
    except Exception as e:  # surface scrape errors to the page
        return jsonify({"error": str(e)}), 502
    finally:
        refresh_lock.release()
    return jsonify(result)


if __name__ == "__main__":
    scraper.log_to_file_if_windowless("app")
    # Debug only when asked for: the always-on scheduled instance must not run the debugger.
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")

"""Web app: a main page linking to one watchlist per scraped source, plus their JSON APIs.

    /                         main page
    /kytary-boutique/         kytary.cz boutique guitars
    /hudebnibazar/            hudebnibazar.cz ads found by your keywords
    /hudebnibazar/keywords    keywords, excluded words and price limits per category, direct alerts, and scans
    /sold/                    everything you moved to sold, from every source, with the price it sold for
    /inventory/               gear you bought: costs, planned selling prices, sales and profit

The app also runs the direct level (scrapers/direct.py): frequent scans of categories with direct
keywords, phone alerts through Telegram and the timer for automatic answers.
"""

import os
import threading

from flask import Flask, abort, jsonify, request, send_from_directory

from scrapers import (direct, hudebnibazar, hudebnibazar_db, inventory_db, kytary_boutique, kytary_boutique_db, hb_reply,
                      sold_db, telegram)
from scrapers.common import log_to_file_if_windowless

app = Flask(__name__, static_folder="static", static_url_path="/static")


def _summary(ads: list[dict], last_run: dict | None) -> dict:
    listed = [a for a in ads if a["listed"]]
    return {
        "new": sum(a["status"] == "new" for a in ads),
        "watching": sum(a["status"] in ("watch", "price") for a in ads),
        "listed": len(listed),
        "last_run": last_run,
    }


def _describe_later(fill, ad: dict | None) -> None:
    """Save the ad's full description from its page in the background once you watch it, so the
    text is still there when the ad is gone and you move it to sold."""
    if ad and ad.get("status") in ("watch", "price") and ad.get("listed"):
        threading.Thread(target=fill, args=([ad["id"]],), daemon=True).start()


def _status_body() -> tuple[str, int | None]:
    body = request.get_json(force=True)
    target = body.get("target_price")
    return body.get("status", ""), int(target) if target not in (None, "") else None


# ---------- pages ----------

@app.get("/")
def hub():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/kytary-boutique/")
def kytary_page():
    return send_from_directory(os.path.join(app.static_folder, "kytary_boutique"), "index.html")


@app.get("/hudebnibazar/")
def hudebnibazar_page():
    return send_from_directory(os.path.join(app.static_folder, "hudebnibazar"), "index.html")


@app.get("/hudebnibazar/keywords")
def hudebnibazar_keywords_page():
    return send_from_directory(os.path.join(app.static_folder, "hudebnibazar"), "keywords.html")


@app.get("/sold/")
def sold_page():
    return send_from_directory(os.path.join(app.static_folder, "sold"), "index.html")


@app.get("/inventory/")
def inventory_page():
    return send_from_directory(os.path.join(app.static_folder, "inventory"), "index.html")


@app.get("/sold-images/<path:name>")
def sold_image(name: str):
    return send_from_directory(sold_db.IMAGE_DIR, name, max_age=86400)


@app.get("/api/summary")
def summary():
    sold = sold_db.list_sold()
    return jsonify({
        "sold": {"count": len(sold), "priced": sum(s["sold_price"] is not None for s in sold),
                 "last": sold[0]["sold_at"] if sold else None},
        "inventory": inventory_db.summary(),
        "kytary_boutique": _summary(kytary_boutique_db.list_ads(), kytary_boutique_db.last_run()),
        "hudebnibazar": {**_summary(hudebnibazar_db.list_ads(), hudebnibazar_db.last_run()),
                         "keywords": len(hudebnibazar_db.list_keywords()),
                         "running": hb_scans.snapshot()["running"]},
    })


# ---------- kytary.cz boutique ----------

kytary_lock = threading.Lock()


@app.get("/api/kytary-boutique/ads")
def kytary_ads():
    return jsonify({"ads": kytary_boutique_db.list_ads(), "last_run": kytary_boutique_db.last_run(),
                    "sold_ids": sold_db.recorded_ids("kytary_boutique")})


@app.post("/api/kytary-boutique/ads/<ad_id>")
def kytary_set_ad(ad_id: str):
    try:
        ad = kytary_boutique_db.update_ad(ad_id, *_status_body())
    except ValueError as e:
        abort(400, str(e))
    if ad is None:
        abort(404)
    if not ad.get("description"):
        _describe_later(kytary_boutique.fill_descriptions, ad)
    return jsonify(ad)


@app.post("/api/kytary-boutique/refresh")
def kytary_refresh():
    if not kytary_lock.acquire(blocking=False):
        abort(409, "A check is already running.")
    try:
        result = kytary_boutique_db.sync(kytary_boutique.scrape())
    except Exception as e:  # surface scrape errors to the page
        return jsonify({"error": str(e)}), 502
    finally:
        kytary_lock.release()
    return jsonify(result)


# ---------- hudebnibazar.cz ----------

class ScanQueue:
    """Category scans take seconds to minutes, so they run one at a time in a background thread.
    Requests made while a scan is running wait in the queue; the page polls /api/hudebnibazar/job."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queue: list[dict] = []   # {"group": slug, "mode": "full" | "new" | "auto"} or {"group": None, "mode": "recheck"}
        self.state = {"running": False, "current": None, "message": "", "results": [], "error": None}

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state, "queue": list(self.queue)}

    def _set(self, **changes):
        with self.lock:
            self.state.update(changes)

    def add(self, groups: list[str], mode: str) -> None:
        with self.lock:
            for slug in groups:
                task = {"group": slug, "mode": mode}
                if task not in self.queue and self.state["current"] != task:
                    self.queue.append(task)
            if self.state["running"]:
                return
            self.state = {"running": True, "current": None, "message": "Starting…", "results": [], "error": None}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.state.update(running=False, current=None, message="")
                    return
                task = self.queue.pop(0)
                self.state["current"] = task
            try:
                if task["mode"] == "recheck":
                    result = [hudebnibazar.recheck(progress=lambda m: self._set(message=m))]
                else:
                    result = hudebnibazar.scan([task["group"]], task["mode"], progress=lambda m: self._set(message=m))
                with self.lock:
                    self.state["results"] = self.state["results"] + result
            except Exception as e:  # keep going with the rest of the queue
                self._set(error=str(e))
            try:
                direct.evaluate()  # new matches for the direct level go to the phone right away
            except Exception as e:
                print(f"Direct alerts failed: {e}")


hb_scans = ScanQueue()
direct_worker = direct.Worker(scan=lambda groups: hb_scans.add(groups, "new"),
                              idle=lambda: not hb_scans.snapshot()["running"])


@app.get("/api/hudebnibazar/ads")
def hb_ads():
    return jsonify({"ads": hudebnibazar_db.list_ads(), "last_run": hudebnibazar_db.last_run(),
                    "groups": hudebnibazar_db.GROUPS, "job": hb_scans.snapshot(),
                    "sold_ids": sold_db.recorded_ids("hudebnibazar")})


@app.post("/api/hudebnibazar/ads/<ad_id>")
def hb_set_ad(ad_id: str):
    try:
        ad = hudebnibazar_db.update_ad(ad_id, *_status_body())
    except ValueError as e:
        abort(400, str(e))
    if ad is None:
        abort(404)
    if not ad.get("full_description"):
        _describe_later(hudebnibazar.fill_descriptions, ad)
    return jsonify(ad)


@app.put("/api/hudebnibazar/ads/<ad_id>/note")
def hb_set_note(ad_id: str):
    try:
        found = hudebnibazar_db.set_note(ad_id, (request.get_json(force=True) or {}).get("note", ""))
    except ValueError as e:
        abort(400, str(e))
    if not found:
        abort(404)
    return jsonify({"id": ad_id, "note": (request.get_json(force=True) or {}).get("note", "").strip() or None})


@app.post("/api/hudebnibazar/scan")
def hb_scan():
    """Body: {"group": slug or null for every category, "mode": "new" | "full" | "auto" | "recheck"}.
    "recheck" opens the page of every ad on your lists to update prices and find deleted ads."""
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "new")
    if mode not in ("new", "full", "auto", "recheck"):
        abort(400, f"Unknown scan type: {mode}")
    if mode == "recheck":
        hb_scans.add([None], "recheck")
        return jsonify(hb_scans.snapshot()), 202
    group = body.get("group")
    if group and group not in hudebnibazar_db.GROUPS_BY_SLUG:
        abort(400, f"Unknown category: {group}")
    hb_scans.add([group] if group else [g["slug"] for g in hudebnibazar_db.GROUPS], mode)
    return jsonify(hb_scans.snapshot()), 202


@app.get("/api/hudebnibazar/job")
def hb_job_status():
    return jsonify(hb_scans.snapshot())


@app.get("/api/hudebnibazar/keywords")
def hb_keywords():
    return jsonify({"keywords": hudebnibazar_db.list_keywords(), "groups": hudebnibazar_db.group_status(),
                    "job": hb_scans.snapshot()})


@app.post("/api/hudebnibazar/keywords")
def hb_add_keyword():
    try:
        kw = hudebnibazar_db.add_keyword(request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(kw), 201


@app.put("/api/hudebnibazar/keywords/<int:keyword_id>")
def hb_update_keyword(keyword_id: int):
    try:
        kw = hudebnibazar_db.update_keyword(keyword_id, request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if kw is None:
        abort(404)
    return jsonify(kw)


@app.delete("/api/hudebnibazar/keywords/<int:keyword_id>")
def hb_delete_keyword(keyword_id: int):
    if not hudebnibazar_db.delete_keyword(keyword_id):
        abort(404)
    return "", 204


def _direct_state() -> dict:
    ready, reason = hb_reply.ready()
    return {"settings": hudebnibazar_db.get_settings(), "modes": hudebnibazar_db.DIRECT_MODES,
            "telegram": telegram.configured(), "can_send": ready, "send_reason": reason,
            "events": hudebnibazar_db.recent_events(10)}


@app.get("/api/hudebnibazar/direct")
def hb_direct():
    return jsonify(_direct_state())


@app.put("/api/hudebnibazar/direct")
def hb_set_direct():
    try:
        hudebnibazar_db.set_settings(request.get_json(force=True) or {})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(_direct_state())


@app.post("/api/hudebnibazar/direct/test")
def hb_direct_test():
    if not telegram.configured():
        abort(400, "Telegram is not set up: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env and restart the app.")
    try:
        telegram.send("👋 Test from the gear scraper. Direct alerts will arrive here.\n\n" + direct.status_text())
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"ok": True})


# ---------- sold (all sources) ----------

def _kytary_snapshot(ad_id: str) -> dict | None:
    ad = next((a for a in kytary_boutique_db.list_ads() if a["id"] == ad_id), None)
    if ad is None:
        return None
    brand, name = ad["brand"] or "", ad["name"] or ""
    return {**ad, "title": name, "brand": brand or None,
            "make": (name[len(brand):].strip() if brand and name.startswith(brand) else name) or None,
            "category": ad["type"], "asking_price": ad["price_czk"]}


def _hb_snapshot(ad_id: str) -> dict | None:
    ad = hudebnibazar_db.get_ad(ad_id)
    if ad is None:
        return None
    names = {g["slug"]: g["name"] for g in hudebnibazar_db.GROUPS}
    return {**ad, "category": ", ".join(names.get(g, g) for g in ad["groups"]) or None,
            "asking_price": ad["price_czk"], "description": ad.get("full_description") or ad.get("description")}


# URL name -> (sold_db source, snapshot of the ad, status update)
SOLD_SITES = {
    "kytary-boutique": ("kytary_boutique", _kytary_snapshot, kytary_boutique_db.update_ad),
    "hudebnibazar": ("hudebnibazar", _hb_snapshot, hudebnibazar_db.update_ad),
}


@app.post("/api/<site>/ads/<ad_id>/sold")
def move_to_sold(site: str, ad_id: str):
    """Body: {brand, make, note, sold_price, where_sold}. Records the sale and moves the ad to sold."""
    if site not in SOLD_SITES:
        abort(404)
    source, snapshot, set_status = SOLD_SITES[site]
    ad = snapshot(ad_id)
    if ad is None:
        abort(404)
    try:
        record = sold_db.record(source, ad, request.get_json(force=True) or {})
    except ValueError as e:
        abort(400, str(e))
    return jsonify({"ad": set_status(ad_id, "sold"), "sold": record})


@app.delete("/api/<site>/ads/<ad_id>/sold")
def unrecord_sold(site: str, ad_id: str):
    """Undo of Move to sold: forget the sale (the page restores the ad's status itself)."""
    if site not in SOLD_SITES:
        abort(404)
    sold_db.delete(SOLD_SITES[site][0], ad_id)
    return "", 204


@app.get("/api/sold")
def sold_list():
    return jsonify({"items": sold_db.list_sold(), "sources": sold_db.SOURCES})


@app.put("/api/sold/<int:sold_id>")
def sold_update(sold_id: int):
    try:
        item = sold_db.update(sold_id, request.get_json(force=True) or {})
    except ValueError as e:
        abort(400, str(e))
    if item is None:
        abort(404)
    return jsonify(item)


@app.delete("/api/sold/<int:sold_id>")
def sold_delete(sold_id: int):
    item = sold_db.get(sold_id)
    if item is None:
        abort(404)
    sold_db.delete(item["source"], item["ad_id"])
    return "", 204


# ---------- inventory (your own gear) ----------

@app.get("/api/inventory")
def inventory_list():
    return jsonify({"items": inventory_db.list_items()})


@app.post("/api/inventory")
def inventory_add():
    try:
        item = inventory_db.add(request.get_json(force=True) or {})
    except ValueError as e:
        abort(400, str(e))
    return jsonify(item), 201


@app.put("/api/inventory/<int:item_id>")
def inventory_update(item_id: int):
    try:
        item = inventory_db.update(item_id, request.get_json(force=True) or {})
    except ValueError as e:
        abort(400, str(e))
    if item is None:
        abort(404)
    return jsonify(item)


@app.delete("/api/inventory/<int:item_id>")
def inventory_delete(item_id: int):
    if not inventory_db.delete(item_id):
        abort(404)
    return "", 204


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
def json_error(e):
    return jsonify({"error": e.description}), e.code


if __name__ == "__main__":
    log_to_file_if_windowless("app")
    debug = os.environ.get("FLASK_DEBUG") == "1"
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN"):  # the debug reloader would start it twice
        direct_worker.start()
    # Debug only when asked for: the always-on scheduled instance must not run the debugger.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=debug)

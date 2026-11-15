"""The direct level for hudebnibazar.cz: ads that a keyword marks as urgent go to your phone
(Telegram) and, depending on the global mode, get the prepared answer automatically.

    Level 1 - every keyword match shows in the browser (as before).
    Level 2 - keywords with a direct alert ("always", or "price up to N Kč") also go direct.

Global modes (db.DIRECT_MODES):
    away    - answer at once, then tell you
    normal  - tell you; answer automatically after delay_minutes unless you tap Skip or answer yourself
    hunting - tell you only; you call or tap "Send prepared answer"
    off     - nothing goes direct

Every ad goes direct at most once. The prepared answer is the keyword's own; a keyword without one
only alerts. Ads without a price ("dohodou") never get an automatic answer either.
While dry_run is on (the default), answers are only shown in Telegram, never sent to the seller.

From Telegram you can:
    tap  📨 Send prepared answer / ⏭ Skip under an alert
    reply to an alert with your own text - that text is sent instead of the prepared one
    send /away /normal /hunting /off to switch the mode, /status to see it
"""

import threading
import time
import traceback
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from . import hb_reply, telegram
from . import hudebnibazar_db as db
from .common import HEADERS, now

MODE_LABELS = {
    "away": "Away: answers go out automatically at once",
    "normal": "Normal: alert first, automatic answer after the delay",
    "hunting": "Hunting: alerts only, you answer yourself",
    "off": "Off: nothing goes to the phone",
}
COMMANDS = [("normal", "Alert, then automatic answer after a delay"), ("hunting", "Alerts only, I answer myself"),
            ("away", "Answer automatically at once"), ("off", "Nothing to the phone"),
            ("status", "Current mode and waiting answers")]

_lock = threading.Lock()  # one evaluation at a time inside this process


def czk(price) -> str:
    return f"{price:,} Kč".replace(",", " ") if price is not None else "no price"


def fill(template: str, ad: dict) -> str:
    """Prepared answer with {title}, {price}, {url}, {location}, {seller} filled in."""
    values = {"title": ad["title"], "price": czk(ad["price_czk"]), "url": ad["url"],
              "location": ad.get("location") or "", "seller": ad.get("seller") or ""}
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def fetch_phone(url: str) -> str | None:
    """The seller's phone number from the ad page, when the ad shows one."""
    try:
        page = requests.get(url, headers=HEADERS, timeout=20)
        link = BeautifulSoup(page.text, "html.parser").select_one('a[href^="tel:"]')
        return link["href"][4:].strip() if link else None
    except Exception:
        return None


def answer_text(ad: dict, kw: dict | None) -> str | None:
    """The keyword's own prepared answer, filled in. None when the keyword has none."""
    template = (kw or {}).get("reply_text")
    return fill(template, ad) if template else None


def can_auto(ad: dict, kw: dict | None) -> bool:
    """An automatic answer needs a price to check and a prepared answer to send."""
    return ad["price_czk"] is not None and bool((kw or {}).get("reply_text"))


# ---------- Telegram messages ----------

def _alert_text(ad: dict, kw: dict, event: dict, settings: dict) -> str:
    e = telegram.esc
    cond = f" (≤ {czk(kw['direct_max_price'])})" if kw["direct"] == "price" else ""
    lines = [f"⚡ <b>{e(ad['title'])}</b>", f"💰 <b>{e(czk(ad['price_czk']))}</b>"
             + (f"  <s>{e(czk(ad['previous_price']))}</s>" if ad.get("previous_price") else "")]
    where = " · ".join(x for x in (ad.get("location"), ad.get("seller")) if x)
    if where:
        lines.append(f"📍 {e(where)}")
    if event.get("phone"):
        lines.append(f"📞 {e(event['phone'])}")
    lines.append(f"🔎 {e(kw['phrase'])}{e(cond)}")
    lines.append("")
    state, mode = event["state"], event["mode"]
    if not kw.get("reply_text"):
        lines.append("✍️ No prepared answer for this keyword: call, or reply to this message with your text.")
    elif ad["price_czk"] is None and mode in ("away", "normal"):
        lines.append("No price in the ad, so no automatic answer. Answer yourself or tap the button.")
    elif state == "waiting" and mode == "normal":
        due = datetime.fromisoformat(event["reply_due_at"])
        lines.append(f"⏱ The prepared answer goes out at <b>{due:%H:%M}</b> unless you tap Skip.")
    elif mode == "away":
        lines.append("🤖 Away mode: sending the prepared answer now.")
    else:
        lines.append("🎯 Hunting mode: call, or tap Send prepared answer.")
    if mode != "away":
        lines.append("<i>Reply to this message with your own text to send that instead.</i>")
    if settings["dry_run"]:
        lines.append("🧪 <i>Dry run: nothing is really sent to the seller.</i>")
    return "\n".join(lines)


def _alert_buttons(ad: dict, kw: dict, mode: str) -> list[list[dict]]:
    rows = []
    if not kw.get("reply_text"):
        rows.append([{"text": "⏭ Skip", "callback_data": f"skip:{ad['id']}"}])
    elif mode != "away" or ad["price_czk"] is None:
        rows.append([{"text": "📨 Send prepared answer", "callback_data": f"send:{ad['id']}"},
                     {"text": "⏭ Skip", "callback_data": f"skip:{ad['id']}"}])
    rows.append([{"text": "🔗 Open ad", "url": ad["url"]}])
    return rows


def _follow_up(event: dict, text: str, done: bool = True) -> None:
    """A short note under the alert (and the alert's buttons removed once the ad is handled)."""
    if not telegram.configured():
        return
    try:
        if done and event.get("tg_message_id"):
            telegram.clear_buttons(event["tg_message_id"])
        telegram.send(text, reply_to=event.get("tg_message_id"))
    except Exception as e:
        print(f"Telegram follow-up failed: {e}")


# ---------- the direct level ----------

def evaluate() -> int:
    """Send every new direct candidate to the phone (and answer it, depending on the mode).
    Called after every scan. Returns how many ads went direct."""
    with _lock:
        settings = db.get_settings()
        mode = settings["mode"]
        count = 0
        for ad, kw in db.direct_candidates():
            has_price = can_auto(ad, kw)  # price known and the keyword has a prepared answer
            due = None
            if mode == "off":
                db.claim_event(ad, kw, mode, "muted")
                continue
            if mode == "normal" and has_price:
                state, due = "waiting", (datetime.now() + timedelta(minutes=settings["delay_minutes"])).isoformat(timespec="seconds")
            elif mode == "away" and has_price:
                state, due = "waiting", now()
            else:
                state = "notified"
            if not db.claim_event(ad, kw, mode, state, due):
                continue  # another process already took it
            count += 1
            db.update_event(ad["id"], phone=fetch_phone(ad["url"]))
            event = db.get_event(ad["id"])
            if telegram.configured():
                try:
                    msg_id = telegram.send(_alert_text(ad, kw, event, settings), _alert_buttons(ad, kw, mode), photo=ad.get("image_url"))
                    db.update_event(ad["id"], tg_message_id=msg_id)
                except Exception as e:
                    print(f"Telegram alert failed for {ad['id']}: {e}")
            else:
                print(f"Direct: {ad['title']} ({czk(ad['price_czk'])}) - Telegram not configured")
            if mode == "away" and has_price:
                reply(ad["id"], auto=True)
        return count


def reply(ad_id: str, text: str | None = None, auto: bool = False) -> str:
    """Send the answer to the seller (or show it, in dry run). Returns a short result for the button."""
    settings = db.get_settings()
    event = db.get_event(ad_id)
    ad = db.get_ad(ad_id)
    if not event or not ad:
        return "Ad not found."
    if auto:
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        if db.auto_replies_since(midnight) >= settings["daily_cap"]:
            if db.set_event_state(ad_id, "notified", ("waiting",), error="daily limit of automatic answers reached"):
                _follow_up(event, f"✋ Daily limit of {settings['daily_cap']} automatic answers reached. "
                                  "Answer this one yourself.", done=False)
            return "Daily limit reached."
    allowed = ("waiting",) if auto else ("waiting", "notified", "failed")
    if not db.set_event_state(ad_id, "sending", allowed):
        return "Already handled."

    kw = db.get_keyword(event["keyword_id"]) if event["keyword_id"] else None
    message = text.strip() if text and text.strip() else answer_text(ad, kw)
    if not message:
        db.set_event_state(ad_id, "notified", ("sending",))
        return "No prepared answer: reply to the alert with your text."
    ok, reason = hb_reply.ready()
    who = "Automatic answer" if auto else "Answer"
    if settings["dry_run"] or not ok:
        why = "dry run is on" if settings["dry_run"] else reason
        db.update_event(ad_id, state="dry_run", replied_at=now(), reply_text=message, auto=int(auto), error=None)
        _follow_up(event, f"🧪 {who} NOT sent ({telegram.esc(why)}). It would say:\n\n<i>{telegram.esc(message)}</i>")
        return "Dry run: not sent."
    try:
        hb_reply.send(ad["url"], message)
    except Exception as e:
        db.update_event(ad_id, state="failed", reply_text=message, auto=int(auto), error=str(e))
        _follow_up(event, f"❌ {who} failed: {telegram.esc(e)}\nTap Send prepared answer to try again, or call.", done=False)
        return "Sending failed."
    db.update_event(ad_id, state="replied", replied_at=now(), reply_text=message, auto=int(auto), error=None)
    _follow_up(event, f"✅ {who} sent:\n\n<i>{telegram.esc(message)}</i>")
    return "Answer sent."


def skip(ad_id: str) -> str:
    event = db.get_event(ad_id)
    if not event or not db.set_event_state(ad_id, "skipped", ("waiting", "notified", "failed")):
        return "Already handled."
    _follow_up(event, "⏭ Skipped. No answer will be sent.")
    return "Skipped."


def tick() -> None:
    """Send the answers whose delay has run out (normal mode). If the mode was switched to hunting or
    off in the meantime, the automatic answer is cancelled and the alert stays for you to decide."""
    mode = db.get_settings()["mode"]
    for event in db.due_events(now()):
        if mode in ("normal", "away"):
            reply(event["ad_id"], auto=True)
        elif db.set_event_state(event["ad_id"], "notified", ("waiting",)):
            _follow_up(event, f"⏸ Automatic answer cancelled: the mode is now {mode}. Decide yourself.", done=False)


def set_mode(mode: str) -> dict:
    return db.set_settings({"mode": mode})


def status_text() -> str:
    s = db.get_settings()
    waiting = [e for e in db.recent_events(50) if e["state"] == "waiting"]
    ok, reason = hb_reply.ready()
    lines = [f"Mode: <b>{MODE_LABELS[s['mode']]}</b>",
             f"Delay before automatic answer: {s['delay_minutes']} min · scan every {s['scan_minutes']} min",
             f"Automatic answers per day: at most {s['daily_cap']}",
             "Sending: " + ("🧪 dry run (nothing is sent)" if s["dry_run"] else "live" if ok else f"not possible: {reason}")]
    if waiting:
        lines.append(f"Waiting to be answered: {len(waiting)}")
    return "\n".join(lines)


def handle_update(update: dict) -> None:
    """One Telegram update: a button tap, a command, or your own answer text."""
    me = telegram.chat_id()
    if cq := update.get("callback_query"):
        if str(cq.get("message", {}).get("chat", {}).get("id")) != me:
            return
        action, _, ad_id = (cq.get("data") or "").partition(":")
        result = reply(ad_id) if action == "send" else skip(ad_id) if action == "skip" else ""
        telegram.answer_button(cq["id"], result)
        return
    msg = update.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != me or not msg.get("text"):
        return
    text = msg["text"].strip()
    if text.startswith("/"):
        command = text[1:].split()[0].split("@")[0].lower()
        if command in db.DIRECT_MODES:
            set_mode(command)
            telegram.send(f"Mode switched. {MODE_LABELS[command]}.")
        elif command == "status":
            telegram.send(status_text())
        else:
            telegram.send("Commands: /normal /hunting /away /off /status\n"
                          "Reply to an alert with your own text to send it to the seller.")
        return
    if reply_to := msg.get("reply_to_message"):
        event = db.event_by_message(reply_to.get("message_id"))
        if event:
            reply(event["ad_id"], text=text)
            return
    telegram.send("Reply to an alert to answer that ad, or use /status.")


# ---------- background worker (runs inside the web app) ----------

class Worker:
    """Runs the answer timer, the frequent scans of categories with direct keywords, and the Telegram
    listener. scan(groups) must queue a new-ads scan; evaluate() is called after each scan finishes."""

    def __init__(self, scan, idle):
        self.scan, self.idle = scan, idle
        self.last_scan = 0.0

    def start(self) -> None:
        threading.Thread(target=self._timer, daemon=True, name="direct-timer").start()
        if telegram.configured():
            threading.Thread(target=self._telegram, daemon=True, name="direct-telegram").start()

    def _timer(self) -> None:
        while True:
            try:
                tick()
                settings = db.get_settings()
                due = time.monotonic() - self.last_scan >= settings["scan_minutes"] * 60
                if due and settings["mode"] != "off" and self.idle():
                    groups = db.direct_groups()
                    if groups:
                        self.scan(groups)
                    self.last_scan = time.monotonic()
            except Exception:
                traceback.print_exc()
            time.sleep(15)

    def _telegram(self) -> None:
        offset = None
        try:
            telegram.set_commands(COMMANDS)
            backlog = telegram.updates(-1, wait=0)  # forget taps and commands from while the app was off
            offset = backlog[-1]["update_id"] + 1 if backlog else None
        except Exception as e:
            print(f"Telegram start failed: {e}")
        while True:
            try:
                for update in telegram.updates(offset):
                    offset = update["update_id"] + 1
                    try:
                        handle_update(update)
                    except Exception:
                        traceback.print_exc()
            except Exception as e:
                print(f"Telegram polling: {e}")
                time.sleep(10)

"""Scan hudebnibazar.cz guitar categories (the "Hudební bazar" watchlist).

Two kinds of scan, per category group:
    full - every page of the category. Also finds ads that were deleted (sold) and price changes.
    new  - from page 1 (newest first) until the ads are older than the category's last scan.

Automatic runs (the hourly task) go category after category: a full scan when the category has
not had one for FULL_EVERY, otherwise a scan for new ads. Keywords are matched against the stored
ads afterwards, so scans don't depend on the keyword list.

After the scans, "recheck" opens the page of every ad you have on your lists (watched, waiting for
a price drop, and new matches) to pick up price changes and deletions within the hour, which the
new-ads scan can't see for older ads.
"""

import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import direct
from . import hudebnibazar_db as db
from .common import HEADERS, block_text, parse_price

PAGE_SIZE = 30                    # ads per listing page on the site
MAX_PAGES = 150                   # safety stop per category (the biggest has ~70 pages)
DELAY = 1.0                       # seconds between requests, to be polite
FULL_EVERY = timedelta(hours=24)  # automatic runs do a full scan this often
OVERLAP = timedelta(minutes=15)   # "new" scans re-read this much before the last scan, to be safe
RECHECK_SKIP = timedelta(minutes=20)  # ads seen in a listing this recently already have a fresh price

MONTHS = {"ledna": 1, "února": 2, "unora": 2, "března": 3, "brezna": 3, "dubna": 4, "května": 5, "kvetna": 5,
          "června": 6, "cervna": 6, "července": 7, "cervence": 7, "srpna": 8, "září": 9, "zari": 9,
          "října": 10, "rijna": 10, "listopadu": 11, "prosince": 12}


def parse_posted(text: str, ref: datetime) -> datetime | None:
    """Latest moment the ad could have been posted, from texts like 'před 21 min.', 'dnes v 15:26',
    'včera v 00:14', 'neděle 8. listopadu' or '12. srpna'. Date-only texts give the end of that day."""
    t = text.lower().strip()
    if m := re.search(r"před\s+(\d+)\s*min", t):
        return ref - timedelta(minutes=int(m[1]))
    if m := re.search(r"před\s+(\d+)\s*hod", t):
        return ref - timedelta(hours=int(m[1]))
    if "před" in t and ("chvíl" in t or "okamž" in t):
        return ref
    if m := re.search(r"(dnes|včera)\s+v\s+(\d{1,2}):(\d{2})", t):
        day = ref.date() - timedelta(days=1 if m[1] == "včera" else 0)
        return datetime(day.year, day.month, day.day, int(m[2]), int(m[3]))
    if m := re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", t):
        return datetime(int(m[3]), int(m[2]), int(m[1]), 23, 59, 59)
    if m := re.search(r"(\d{1,2})\.\s*([a-záčďéěíňóřšťúůýž]+)(?:\s+(\d{4}))?", t):
        month = MONTHS.get(m[2])
        if month:
            year = int(m[3]) if m[3] else ref.year
            when = datetime(year, month, int(m[1]), 23, 59, 59)
            if not m[3] and when.date() > ref.date():  # "28. prosince" seen in January
                when = when.replace(year=year - 1)
            return when
    return None


def _text(node) -> str:
    return " ".join(node.get_text(" ").split()) if node else ""


def parse_price_node(node) -> tuple[int | None, str]:
    """Price in CZK and its text from an .InzeratCena block (same markup in listings and on ad pages)."""
    if not node:
        return None, ""
    eur = node.select_one(".InzeratCenaEUR")
    if eur:
        eur.extract()
    text = _text(node)
    return (parse_price(text.split("Kč")[0]) if "Kč" in text else None), text


def parse_page(html: str, page_url: str, group_slug: str, ref: datetime) -> tuple[list[dict], int]:
    """Ads offered on one listing page, and how many ad cards the page had (including KOUPÍM ones)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".InzeratBody[data-inzid]")
    ads = []
    for card in cards:
        content = card.select_one(".InzeratContent")
        link = content.find("a", href=True) if content else None
        if not link:
            continue
        if _text(content.select_one(".label")).upper().startswith("KOUP"):  # "KOUPÍM" = wants to buy
            continue

        price, price_text = parse_price_node(content.select_one(".InzeratCena"))

        seller = content.select_one(".InzeratNick")
        location = ""
        if seller:
            location = " ".join(s.strip() for s in seller.parent.find_all(string=True, recursive=False)).strip()

        posted = _text(content.select_one(".InzeratZarazeno"))
        posted_at = parse_posted(posted, ref)
        img = card.select_one(".InzeratObr img")
        ads.append({
            "id": card["data-inzid"],
            "group_slug": group_slug,
            "title": _text(content.select_one(".InzeratNadpis")),
            "description": _text(content.select_one(".InzeratText")),
            "price_czk": price,
            "price_text": price_text,
            "location": location,
            "seller": _text(seller),
            "posted": posted,
            "posted_at": posted_at.isoformat(timespec="seconds") if posted_at else None,
            "url": urljoin(page_url, link["href"]),
            "image_url": img.get("src", "") if img else "",
        })
    return ads, len(cards)


class Scanner:
    def __init__(self, progress=print):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.progress = progress
        self._last = 0.0

    def get(self, url: str, **kwargs) -> requests.Response:
        wait = DELAY - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.session.get(url, timeout=30, **kwargs)
        finally:
            self._last = time.monotonic()

    def check_page(self, url: str) -> tuple[str, int | None, str, str]:
        """(state, price, price text, full description) for one ad page. state is 'gone', 'live' or 'unknown'.

        A deleted (sold) ad still answers 200, with a page saying "Inzerát byl smazán."; only ad IDs
        that never existed give 404. A live ad page has the detail blocks (description, price)."""
        response = self.get(url)
        if response.status_code in (404, 410):
            return "gone", None, "", ""
        response.raise_for_status()
        if "byl smazán" in response.text:
            return "gone", None, "", ""
        soup = BeautifulSoup(response.text, "html.parser")
        description = block_text(soup.select_one(".detail-desc .InzeratText"))
        price_node = soup.select_one(".InzeratCena")
        if price_node:
            return ("live", *parse_price_node(price_node), description)
        if soup.select_one(".detail-desc"):
            return "live", None, "", description
        return "unknown", None, "", ""  # unexpected page: decide nothing, check again later

    def page_state(self, url: str) -> str:
        return self.check_page(url)[0]

    def recheck(self) -> dict:
        """Open the page of every ad on your lists (watched, waiting for a price drop, new matches)
        that this run's scans didn't already see, and record price changes and deletions."""
        ads = db.ads_to_recheck(seen_before=(datetime.now() - RECHECK_SKIP).isoformat(timespec="seconds"))
        checked = changed = errors = 0
        gone = []
        for i, ad in enumerate(ads, 1):
            self.progress(f"Checking your ads: {i} of {len(ads)}")
            try:
                state, price, price_text, description = self.check_page(ad["url"])
            except Exception as e:  # one bad page shouldn't stop the rest
                errors += 1
                self.progress(f"Could not check “{ad['title']}”: {e}")
                continue
            checked += 1
            if state == "gone":
                gone.append(ad["id"])
            elif state == "live":
                if price_text:
                    changed += db.record_price(ad["id"], price, price_text)
                if description and ad["status"] in ("watch", "price") and not ad.get("full_description"):
                    db.set_full_description(ad["id"], description)
        return {"group_slug": None, "mode": "recheck", "checked": checked, "price_changes": changed,
                "gone_ads": db.mark_gone(gone), "errors": errors,
                "error": f"{errors} of {len(ads)} ad pages could not be loaded" if errors else None}

    def scan_group(self, slug: str, mode: str) -> dict:
        """Scan one category group. mode is 'full' or 'new'."""
        group = db.GROUPS_BY_SLUG[slug]
        cutoff = None
        if mode == "new":
            last = db.last_good_scan(slug)
            if last:
                cutoff = datetime.fromisoformat(last["started_at"]) - OVERLAP
            else:
                mode = "full"  # nothing to continue from

        scan_id = db.start_scan(slug, mode)
        if scan_id is None:
            self.progress(f"{group['name']}: already being scanned, skipped")
            return {"group_slug": slug, "mode": mode, "skipped": True}

        pages = found = new_ads = gone = 0
        seen: set[str] = set()
        complete = True
        try:
            for base in group["urls"]:
                for page in range(1, MAX_PAGES + 1):
                    self.progress(f"{group['name']}: {'full scan' if mode == 'full' else 'new ads'}, page {page}")
                    response = self.get(base, params={"p": page})
                    response.raise_for_status()
                    ads, cards = parse_page(response.text, response.url, slug, datetime.now())
                    pages += 1
                    fresh = [ad for ad in ads if ad["id"] not in seen]
                    seen.update(ad["id"] for ad in fresh)
                    found += len(fresh)
                    new_ads += db.save_ads(fresh)
                    if cards < PAGE_SIZE or (ads and not fresh):
                        break  # last page (the site repeats the last page past the end)
                    if cutoff and ads and all(
                        ad["posted_at"] and datetime.fromisoformat(ad["posted_at"]) < cutoff for ad in ads
                    ):
                        break  # everything from here on was already covered by the last scan
                else:
                    complete = False

            if mode == "full" and complete:
                gone = self._find_gone(slug, seen)
        except Exception as e:
            scan = db.finish_scan(scan_id, pages, found, new_ads, gone, error=str(e))
            self.progress(f"{group['name']}: scan failed: {e}")
            return {**scan, "mode": mode}
        return {**db.finish_scan(scan_id, pages, found, new_ads, gone), "mode": mode}

    def _find_gone(self, slug: str, seen: set[str]) -> int:
        """Handle the category's listed ads that a complete full scan didn't see.

        Ads you'd see on the page (keyword matches, watched) get their own page checked, because an
        ad bumped to page 1 mid-scan can slip past the scan. Other ads are decided without a request.
        An ad only leaves this category when it is confirmed listed somewhere else, so it can never
        end up in no category at all (and then never be checked again)."""
        keywords = db.list_keyword_set()
        gone = []
        for ad in db.missing_in_group(slug, seen):
            elsewhere = [g for g in ad["groups"] if g != slug]
            if db.is_interesting(ad, keywords):
                self.progress(f"Checking whether “{ad['title']}” is still listed")
                state = self.page_state(ad["url"])
                if state == "gone":
                    gone.append(ad["id"])
                elif state == "live" and elsewhere:
                    db.leave_group(ad["id"], slug)
                # live in this category only, or unknown: keep it; the next full scan looks again
            elif elsewhere:
                db.leave_group(ad["id"], slug)  # still listed in another category; its scans judge it
            else:
                gone.append(ad["id"])
        return db.mark_gone(gone)


def auto_mode(slug: str) -> str:
    last_full = db.last_good_scan(slug, "full")
    if not last_full or datetime.now() - datetime.fromisoformat(last_full["started_at"]) > FULL_EVERY:
        return "full"
    return "new"


def scan(groups: list[str] | None = None, mode: str = "auto", progress=print) -> list[dict]:
    """Scan the given category groups (default: all), one after another.
    mode: 'full', 'new', or 'auto' (full once a day per category, otherwise new)."""
    scanner = Scanner(progress)
    results = []
    for slug in groups or [g["slug"] for g in db.GROUPS]:
        results.append(scanner.scan_group(slug, auto_mode(slug) if mode == "auto" else mode))
    return results


def fill_descriptions(ad_ids: list[str] | None = None, progress=print) -> int:
    """Save the full description of ads you watch, from their own pages, while they're still up.
    Runs when you pick Watch / Price under… and after each hourly run for anything missed."""
    ads = db.ads_missing_description(ad_ids)
    scanner = Scanner(progress)
    saved = 0
    for ad in ads:
        try:
            state, _, _, description = scanner.check_page(ad["url"])
        except Exception as e:
            progress(f"Could not load the description of {ad['id']}: {e}")
            continue
        if state == "live" and description:
            db.set_full_description(ad["id"], description)
            saved += 1
    return saved


def recheck(progress=print) -> dict:
    """Check every ad on your lists for price changes and deletions."""
    return Scanner(progress).recheck()


def main() -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting hudebnibazar.cz scan")
    # Page-by-page progress is for the web page; the log only gets one line per category.
    for r in scan(progress=lambda m: None if ", page " in m else print(m)):
        if r.get("skipped"):
            continue
        status = f"failed: {r['error']}" if r["error"] else "ok"
        print(f"  {r['group_slug']}: {r['mode']} scan, {r['pages']} pages, {r['found']} ads, "
              f"{r['new_ads']} new, {r['gone_ads']} gone, {status}")
    r = recheck(progress=lambda m: None if m.startswith("Checking your ads") else print(m))
    print(f"  your ads: {r['checked']} checked, {r['price_changes']} price changes, {r['gone_ads']} gone"
          + (f", {r['errors']} failed" if r["errors"] else ""))
    described = fill_descriptions()
    if described:
        print(f"  descriptions saved: {described}")
    try:
        sent = direct.evaluate()
        if sent:
            print(f"  direct alerts: {sent}")
    except Exception as e:
        print(f"  direct alerts failed: {e}")
    print(f"Saved to {db.DB_PATH}")


if __name__ == "__main__":
    main()

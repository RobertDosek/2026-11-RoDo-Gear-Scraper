"""SQLite storage for hudebnibazar.cz: every scanned ad, your keywords, and the scan history.

How the pieces fit:
    ads      - every ad seen in a scanned category, with the user's watch decision
    ad_groups - which categories each ad is listed in (the site lists some ads in several)
    keywords - what to look for in one category group, plus filters (excluded words, price limits)
    scans    - one row per category scan ("full" = every page, "new" = only ads since the last scan)

Keywords are matched against the stored ads when the page asks for them, so adding or editing a
keyword shows its results straight away, based on the ads from the latest scans.
"""

import json
import os
import unicodedata
from datetime import datetime, timedelta

from .common import BASE_DIR, STATUSES, connect as _connect, now, price_stats

DB_PATH = os.environ.get("HUDEBNIBAZAR_DB", os.path.join(BASE_DIR, "hudebnibazar.db"))

# Category groups on hudebnibazar.cz, scanned in this order. A group can span several site
# categories (acoustic and electro-acoustic guitars are treated as one).
GROUPS = [
    {"slug": "elektricke", "name": "Elektrické kytary",
     "urls": ["https://hudebnibazar.cz/elektricke-kytary/110100/"]},
    {"slug": "akusticke", "name": "Akustické a elektroakustické kytary",
     "urls": ["https://hudebnibazar.cz/akusticke-kytary/110200/",
              "https://hudebnibazar.cz/elektroakusticke-kytary/110210/"]},
    {"slug": "komba", "name": "Kytarová komba",
     "urls": ["https://hudebnibazar.cz/kytarova-komba/110400/"]},
    {"slug": "efekty", "name": "Kytarové efekty",
     "urls": ["https://hudebnibazar.cz/kytarove-efekty/110500/"]},
    {"slug": "snimace", "name": "Snímače pro kytaru",
     "urls": ["https://hudebnibazar.cz/snimace-pro-kytaru/110501/"]},
    {"slug": "prislusenstvi", "name": "Příslušenství ke kytaře",
     "urls": ["https://hudebnibazar.cz/prislusenstvi-ke-kytare/110600/"]},
]
GROUPS_BY_SLUG = {g["slug"]: g for g in GROUPS}

# A scan row without a finish time older than this is treated as crashed, not running.
STALE_SCAN = timedelta(hours=2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_slug  TEXT NOT NULL,
    phrase      TEXT NOT NULL,
    excludes    TEXT NOT NULL DEFAULT '[]',
    min_price   INTEGER,
    max_price   INTEGER,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS ads (
    id             TEXT PRIMARY KEY,
    group_slug     TEXT,
    title          TEXT,
    description    TEXT,
    price_czk      INTEGER,
    price_text     TEXT,
    previous_price INTEGER,
    location       TEXT,
    seller         TEXT,
    posted         TEXT,
    posted_at      TEXT,
    url            TEXT,
    image_url      TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    target_price   INTEGER,
    listed         INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT,
    last_seen      TEXT,
    gone_at        TEXT,
    sold_at        TEXT,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS ads_group ON ads (group_slug, listed);
-- The site lists some ads in several categories, so an ad can belong to several groups.
CREATE TABLE IF NOT EXISTS ad_groups (
    ad_id      TEXT NOT NULL,
    group_slug TEXT NOT NULL,
    last_seen  TEXT,
    PRIMARY KEY (ad_id, group_slug)
);
CREATE TABLE IF NOT EXISTS price_history (
    ad_id     TEXT NOT NULL,
    price_czk INTEGER,
    seen_at   TEXT
);
CREATE INDEX IF NOT EXISTS price_history_ad ON price_history (ad_id, seen_at);
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_slug  TEXT NOT NULL,
    mode        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    pages       INTEGER,
    found       INTEGER,
    new_ads     INTEGER,
    gone_ads    INTEGER,
    error       TEXT
);
"""


def connect():
    return _connect(DB_PATH, SCHEMA)


def migrate() -> None:
    """Bring a database from the earlier keyword-search version up to date."""
    with connect() as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(ads)")}
        if "posted_at" not in columns:
            conn.execute("ALTER TABLE ads ADD COLUMN posted_at TEXT")
        if "note" not in columns:
            conn.execute("ALTER TABLE ads ADD COLUMN note TEXT")
        # Every ad needs at least one category, or full scans never look at it again. Only fill in
        # ads with none, so categories an ad really left are not added back.
        conn.execute("INSERT OR IGNORE INTO ad_groups SELECT id, group_slug, last_seen FROM ads "
                     "WHERE id NOT IN (SELECT ad_id FROM ad_groups)")
        conn.execute("DROP TABLE IF EXISTS matches")
        conn.execute("DROP TABLE IF EXISTS runs")


migrate()


# ---------- matching ----------

def fold(text: str) -> str:
    """Lower-case and strip diacritics so 'Jazzmaster', 'JAZZMASTER' and 'jazzmáster' compare equal."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(ad: dict, keyword: dict) -> bool:
    """The ad is listed in the keyword's category and every word of the keyword appears in its title
    (any order, ignoring case and accents)."""
    if keyword["group_slug"] not in ad["groups"]:
        return False
    title = fold(ad["title"])
    return all(word in title for word in fold(keyword["phrase"]).split())


def passes(ad: dict, keyword: dict) -> bool:
    """True when the ad survives the keyword's excluded words and price limits."""
    title = fold(ad["title"])
    if any(fold(word) in title for word in keyword["excludes"] if word.strip()):
        return False
    lo, hi, price = keyword["min_price"], keyword["max_price"], ad["price_czk"]
    if lo is not None or hi is not None:
        if price is None:  # "cena dohodou" can't be checked against a limit
            return False
        if lo is not None and price < lo:
            return False
        if hi is not None and price > hi:
            return False
    return True


# ---------- keywords ----------

def _keyword(row) -> dict:
    kw = {k: row[k] for k in ("id", "group_slug", "phrase", "excludes", "min_price", "max_price", "created_at")}
    kw["excludes"] = json.loads(kw["excludes"] or "[]")
    return kw


def _clean_keyword(data: dict) -> dict:
    phrase = " ".join(str(data.get("phrase", "")).split())
    if not phrase:
        raise ValueError("Enter a word or phrase to look for.")
    group = data.get("group_slug")
    if group not in GROUPS_BY_SLUG:
        raise ValueError(f"Unknown category: {group}")
    excludes = data.get("excludes") or []
    if isinstance(excludes, str):
        excludes = excludes.split(",")
    excludes = list(dict.fromkeys(" ".join(e.split()) for e in excludes if e and e.strip()))

    def price(key):
        value = data.get(key)
        if value in (None, ""):
            return None
        value = int(value)
        if value < 0:
            raise ValueError("Prices can't be negative.")
        return value

    lo, hi = price("min_price"), price("max_price")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("The minimum price is higher than the maximum.")
    return {"group_slug": group, "phrase": phrase, "excludes": excludes, "min_price": lo, "max_price": hi}


def _load_ads(conn, where: str = "", args=()) -> list[dict]:
    ads = [dict(r) for r in conn.execute(f"SELECT * FROM ads {where} ORDER BY COALESCE(posted_at, first_seen) DESC", args)]
    groups: dict[str, list[str]] = {}
    for ad_id, slug in conn.execute("SELECT ad_id, group_slug FROM ad_groups"):
        groups.setdefault(ad_id, []).append(slug)
    stats = price_stats(conn)
    for ad in ads:
        ad["groups"] = groups.get(ad["id"]) or [ad["group_slug"]]
        ad.update(stats.get(ad["id"], {}))
    return ads


def _all_keywords(conn) -> list[dict]:
    return [_keyword(r) for r in conn.execute("SELECT * FROM keywords ORDER BY group_slug, phrase COLLATE NOCASE")]


def list_keywords() -> list[dict]:
    """Keywords with counts of listed ads they match, and how many of those pass the filters."""
    with connect() as conn:
        keywords = _all_keywords(conn)
        ads = _load_ads(conn, "WHERE listed=1")
    for kw in keywords:
        matched = [ad for ad in ads if matches(ad, kw)]
        kw["matched"] = len(matched)
        kw["shown"] = sum(passes(ad, kw) for ad in matched)
    return keywords


def get_keyword(keyword_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM keywords WHERE id=?", (keyword_id,)).fetchone()
    return _keyword(row) if row else None


def add_keyword(data: dict) -> dict:
    kw = _clean_keyword(data)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO keywords (group_slug, phrase, excludes, min_price, max_price, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kw["group_slug"], kw["phrase"], json.dumps(kw["excludes"], ensure_ascii=False),
             kw["min_price"], kw["max_price"], now()),
        )
        keyword_id = cur.lastrowid
    return get_keyword(keyword_id)


def update_keyword(keyword_id: int, data: dict) -> dict | None:
    old = get_keyword(keyword_id)
    if old is None:
        return None
    kw = _clean_keyword({**old, **data})
    with connect() as conn:
        conn.execute(
            "UPDATE keywords SET group_slug=?, phrase=?, excludes=?, min_price=?, max_price=? WHERE id=?",
            (kw["group_slug"], kw["phrase"], json.dumps(kw["excludes"], ensure_ascii=False),
             kw["min_price"], kw["max_price"], keyword_id),
        )
    return get_keyword(keyword_id)


def delete_keyword(keyword_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM keywords WHERE id=?", (keyword_id,)).rowcount > 0


# ---------- scans ----------

def start_scan(group_slug: str, mode: str) -> int | None:
    """Record a scan as started. Returns None when another scan of this category is still running."""
    with connect() as conn:
        running = conn.execute(
            "SELECT 1 FROM scans WHERE group_slug=? AND finished_at IS NULL AND started_at > ?",
            (group_slug, (datetime.now() - STALE_SCAN).isoformat(timespec="seconds")),
        ).fetchone()
        if running:
            return None
        return conn.execute("INSERT INTO scans (group_slug, mode, started_at) VALUES (?, ?, ?)",
                            (group_slug, mode, now())).lastrowid


def finish_scan(scan_id: int, pages: int, found: int, new_ads: int, gone_ads: int, error: str | None = None) -> dict:
    with connect() as conn:
        conn.execute("UPDATE scans SET finished_at=?, pages=?, found=?, new_ads=?, gone_ads=?, error=? WHERE id=?",
                     (now(), pages, found, new_ads, gone_ads, error, scan_id))
        return dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone())


def last_good_scan(group_slug: str, mode: str | None = None) -> dict | None:
    """The latest finished scan without an error, of any mode or just the given one."""
    sql = "SELECT * FROM scans WHERE group_slug=? AND finished_at IS NOT NULL AND error IS NULL"
    args = [group_slug]
    if mode:
        sql += " AND mode=?"
        args.append(mode)
    with connect() as conn:
        row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def group_status() -> list[dict]:
    """Each category with its latest scans, ad count and whether a scan is running."""
    stale = (datetime.now() - STALE_SCAN).isoformat(timespec="seconds")
    out = []
    with connect() as conn:
        for g in GROUPS:
            def latest(extra, args=()):
                row = conn.execute(f"SELECT * FROM scans WHERE group_slug=? {extra} ORDER BY id DESC LIMIT 1",
                                   (g["slug"], *args)).fetchone()
                return dict(row) if row else None
            out.append({
                **g,
                "last_scan": latest("AND finished_at IS NOT NULL"),
                "last_full": latest("AND finished_at IS NOT NULL AND error IS NULL AND mode='full'"),
                "running": latest("AND finished_at IS NULL AND started_at > ?", (stale,)),
                "listed": conn.execute("SELECT COUNT(*) FROM ad_groups ag JOIN ads a ON a.id = ag.ad_id "
                                       "WHERE ag.group_slug=? AND a.listed=1", (g["slug"],)).fetchone()[0],
            })
    return out


def last_run() -> dict | None:
    """The latest finished scan of any category, for the 'last checked' line."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ---------- storing scanned ads ----------

def save_ads(ads: list[dict]) -> int:
    """Insert or refresh ads from one listing page. Returns how many were new."""
    ts = now()
    new_count = 0
    with connect() as conn:
        for ad in ads:
            old = conn.execute("SELECT price_czk, previous_price, status FROM ads WHERE id=?", (ad["id"],)).fetchone()
            if old is None:
                conn.execute(
                    """INSERT INTO ads (id, group_slug, title, description, price_czk, price_text, location, seller,
                                        posted, posted_at, url, image_url, status, listed, first_seen, last_seen)
                       VALUES (:id, :group_slug, :title, :description, :price_czk, :price_text, :location, :seller,
                               :posted, :posted_at, :url, :image_url, 'new', 1, :ts, :ts)""",
                    {**ad, "ts": ts},
                )
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (ad["id"], ad["price_czk"], ts))
                conn.execute("INSERT OR REPLACE INTO ad_groups VALUES (?, ?, ?)", (ad["id"], ad["group_slug"], ts))
                new_count += 1
                continue
            previous_price = old["previous_price"]
            if ad["price_czk"] != old["price_czk"]:
                previous_price = old["price_czk"]
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (ad["id"], ad["price_czk"], ts))
            status = "new" if old["status"] == "sold" else old["status"]
            conn.execute(
                """UPDATE ads SET title=?, description=?, price_czk=?, price_text=?, previous_price=?,
                                  location=?, seller=?, posted=?, posted_at=?, url=?, image_url=?, status=?,
                                  listed=1, last_seen=?, gone_at=NULL,
                                  sold_at=CASE WHEN ?='sold' THEN sold_at ELSE NULL END
                   WHERE id=?""",
                (ad["title"], ad["description"], ad["price_czk"], ad["price_text"], previous_price,
                 ad["location"], ad["seller"], ad["posted"], ad["posted_at"], ad["url"], ad["image_url"], status,
                 ts, status, ad["id"]),
            )
            conn.execute("INSERT OR REPLACE INTO ad_groups VALUES (?, ?, ?)", (ad["id"], ad["group_slug"], ts))
    return new_count


def missing_in_group(group_slug: str, seen_ids: set[str]) -> list[dict]:
    """Listed ads of the category that a complete full scan did not see (with all their categories).
    Nothing is changed here; the scanner decides per ad."""
    with connect() as conn:
        ads = _load_ads(conn, "WHERE listed=1")
    return [ad for ad in ads if group_slug in ad["groups"] and ad["id"] not in seen_ids]


def leave_group(ad_id: str, group_slug: str) -> None:
    """Take an ad out of one category. Never removes its last category."""
    with connect() as conn:
        others = conn.execute("SELECT COUNT(*) FROM ad_groups WHERE ad_id=? AND group_slug!=?",
                              (ad_id, group_slug)).fetchone()[0]
        if others:
            conn.execute("DELETE FROM ad_groups WHERE ad_id=? AND group_slug=?", (ad_id, group_slug))


def ads_to_recheck(seen_before: str) -> list[dict]:
    """Listed ads on your lists (watched, waiting for a price drop, new keyword matches) that no
    listing has shown since seen_before."""
    with connect() as conn:
        keywords = _all_keywords(conn)
        ads = _load_ads(conn, "WHERE listed=1 AND last_seen < ?", (seen_before,))
    return [ad for ad in ads
            if ad["status"] in ("watch", "price")
            or (ad["status"] == "new" and any(matches(ad, kw) and passes(ad, kw) for kw in keywords))]


def record_price(ad_id: str, price: int | None, price_text: str) -> int:
    """Store the price read from the ad's own page. Returns 1 when it changed."""
    ts = now()
    with connect() as conn:
        old = conn.execute("SELECT price_czk FROM ads WHERE id=?", (ad_id,)).fetchone()
        if old is None:
            return 0
        if price == old["price_czk"]:
            conn.execute("UPDATE ads SET price_text=?, last_seen=? WHERE id=?", (price_text, ts, ad_id))
            return 0
        conn.execute("UPDATE ads SET price_czk=?, price_text=?, previous_price=?, last_seen=? WHERE id=?",
                     (price, price_text, old["price_czk"], ts, ad_id))
        conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (ad_id, price, ts))
        return 1


def mark_gone(ad_ids: list[str]) -> int:
    ts = now()
    with connect() as conn:
        for ad_id in ad_ids:
            # Same rule as the kytary.cz watchlist: watched ads stay visible labelled as sold until the
            # user moves them; undecided and "don't watch" ads go straight to sold.
            conn.execute(
                "UPDATE ads SET listed=0, gone_at=?, "
                "status=CASE WHEN status IN ('new','ignore') THEN 'sold' ELSE status END, "
                "sold_at=CASE WHEN status IN ('new','ignore') THEN ? ELSE sold_at END WHERE id=?",
                (ts, ts, ad_id),
            )
    return len(ad_ids)


# ---------- listing ads for the page ----------

def is_interesting(ad: dict, keywords: list[dict]) -> bool:
    """Shown on the page: matched by a keyword and passing its filters, or already decided on."""
    return any(matches(ad, kw) and passes(ad, kw) for kw in keywords) or ad["status"] in ("watch", "price")


def list_ads() -> list[dict]:
    """Ads that a keyword matches and whose filters they pass, plus ads you watch, newest first."""
    with connect() as conn:
        keywords = _all_keywords(conn)
        ads = _load_ads(conn)
    out = []
    for ad in ads:
        hits = [kw for kw in keywords if matches(ad, kw) and passes(ad, kw)]
        if hits or ad["status"] in ("watch", "price"):
            ad["keywords"] = [{"id": kw["id"], "phrase": kw["phrase"]} for kw in hits]
            out.append(ad)
    return out


def list_keyword_set() -> list[dict]:
    with connect() as conn:
        return _all_keywords(conn)


NOTE_MAX = 2000


def set_note(ad_id: str, note: str) -> bool:
    """Save your own note on an ad (empty text removes it). Scans never touch it."""
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        raise ValueError(f"Notes can be at most {NOTE_MAX} characters.")
    with connect() as conn:
        return conn.execute("UPDATE ads SET note=? WHERE id=?", (note or None, ad_id)).rowcount > 0


def update_ad(ad_id: str, status: str, target_price: int | None = None) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    with connect() as conn:
        conn.execute(
            "UPDATE ads SET status=?, target_price=COALESCE(?, target_price), "
            "sold_at=CASE WHEN ?='sold' THEN COALESCE(sold_at, ?) ELSE sold_at END WHERE id=?",
            (status, target_price, status, now(), ad_id),
        )
    return next((ad for ad in list_ads() if ad["id"] == ad_id), None)

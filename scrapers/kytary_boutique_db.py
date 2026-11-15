"""SQLite storage for kytary.cz boutique ads and the user's watch decisions."""

import os

from .common import BASE_DIR, STATUSES, connect as _connect, now, price_stats

DB_PATH = os.environ.get("KYTARY_DB", os.path.join(BASE_DIR, "kytary.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    id             TEXT PRIMARY KEY,
    brand          TEXT,
    name           TEXT,
    type           TEXT,
    price_czk      INTEGER,
    previous_price INTEGER,
    availability   TEXT,
    url            TEXT,
    image_url      TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    target_price   INTEGER,
    listed         INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT,
    last_seen      TEXT,
    gone_at        TEXT,
    sold_at        TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    ad_id     TEXT NOT NULL REFERENCES ads(id),
    price_czk INTEGER,
    seen_at   TEXT
);
CREATE INDEX IF NOT EXISTS price_history_ad ON price_history (ad_id, seen_at);
CREATE TABLE IF NOT EXISTS runs (
    run_at   TEXT,
    found    INTEGER,
    new_ads  INTEGER,
    gone_ads INTEGER
);
"""


def connect():
    return _connect(DB_PATH, SCHEMA)


def sync(ads: list[dict]) -> dict:
    """Merge one complete scrape into the database and detect new / gone ads."""
    if not ads:
        raise ValueError("Scrape returned no ads; refusing to mark everything as sold.")

    ts = now()
    seen_ids = {ad["id"] for ad in ads}
    new_count = 0
    with connect() as conn:
        existing = {row["id"]: row for row in conn.execute("SELECT * FROM ads")}

        for ad in ads:
            old = existing.get(ad["id"])
            if old is None:
                conn.execute(
                    """INSERT INTO ads (id, brand, name, type, price_czk, availability, url, image_url,
                                        status, listed, first_seen, last_seen)
                       VALUES (:id, :brand, :name, :type, :price_czk, :availability, :url, :image_url,
                               'new', 1, :ts, :ts)""",
                    {**ad, "ts": ts},
                )
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (ad["id"], ad["price_czk"], ts))
                new_count += 1
                continue

            previous_price = old["previous_price"]
            if ad["price_czk"] != old["price_czk"]:
                previous_price = old["price_czk"]
                conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (ad["id"], ad["price_czk"], ts))

            # An ad that comes back after being sold needs a fresh decision.
            status = "new" if old["status"] == "sold" else old["status"]
            conn.execute(
                """UPDATE ads SET brand=?, name=?, type=?, price_czk=?, previous_price=?, availability=?,
                                  url=?, image_url=?, status=?, listed=1, last_seen=?, gone_at=NULL,
                                  sold_at=CASE WHEN ?='sold' THEN sold_at ELSE NULL END
                   WHERE id=?""",
                (ad["brand"], ad["name"], ad["type"], ad["price_czk"], previous_price, ad["availability"],
                 ad["url"], ad["image_url"], status, ts, status, ad["id"]),
            )

        gone = [row for row in existing.values() if row["listed"] and row["id"] not in seen_ids]
        for row in gone:
            # Watched ads stay visible labelled as sold until the user moves them;
            # undecided and "don't watch" ads go straight to sold.
            if row["status"] in ("new", "ignore"):
                conn.execute("UPDATE ads SET listed=0, gone_at=?, status='sold', sold_at=? WHERE id=?",
                             (ts, ts, row["id"]))
            else:
                conn.execute("UPDATE ads SET listed=0, gone_at=? WHERE id=?", (ts, row["id"]))

        conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (ts, len(ads), new_count, len(gone)))

    return {"run_at": ts, "found": len(ads), "new_ads": new_count, "gone_ads": len(gone)}


def list_ads() -> list[dict]:
    with connect() as conn:
        stats = price_stats(conn)
        return [{**dict(row), **stats.get(row["id"], {})}
                for row in conn.execute("SELECT * FROM ads ORDER BY first_seen DESC, rowid")]


def last_run() -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def update_ad(ad_id: str, status: str, target_price: int | None = None) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    with connect() as conn:
        conn.execute(
            "UPDATE ads SET status=?, target_price=COALESCE(?, target_price), "
            "sold_at=CASE WHEN ?='sold' THEN COALESCE(sold_at, ?) ELSE sold_at END WHERE id=?",
            (status, target_price, status, now(), ad_id),
        )
        row = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
        return dict(row) if row else None

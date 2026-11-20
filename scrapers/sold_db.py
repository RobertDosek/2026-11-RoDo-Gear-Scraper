"""SQLite storage for sold gear, shared by every source.

When an ad you watched is sold, "Move to sold" on its watchlist records it here together with what
you fill in (brand, make, note, the price it sold for and where). The row keeps a copy of the ad as
it was (title, link, asking prices, picture), so it stays useful after the ad leaves the site.
The picture is downloaded into sold_images/ because sold ads and their photos disappear.
"""

import os
import re

import requests

from .common import BASE_DIR, HEADERS, add_columns, connect as _connect, now

DB_PATH = os.environ.get("SOLD_DB", os.path.join(BASE_DIR, "sold.db"))
IMAGE_DIR = os.environ.get("SOLD_IMAGES", os.path.join(BASE_DIR, "sold_images"))

SOURCES = {"kytary_boutique": "kytary.cz boutique", "hudebnibazar": "hudebnibazar.cz"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sold (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    ad_id        TEXT NOT NULL,
    title        TEXT,
    brand        TEXT,
    make         TEXT,
    category     TEXT,
    note         TEXT,
    sold_price   INTEGER,
    where_sold   TEXT,
    asking_price INTEGER,
    price_start  INTEGER,
    price_min    INTEGER,
    price_max    INTEGER,
    url          TEXT,
    image_url    TEXT,
    image_file   TEXT,
    first_seen   TEXT,
    sold_at      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    description  TEXT,
    UNIQUE (source, ad_id)
);
"""

EDITABLE = ("brand", "make", "note", "sold_price", "where_sold", "sold_at")
TEXT_MAX = 2000
NOTE_MAX = 10000  # the note starts as the ad's whole description, which can be long


def connect():
    return _connect(DB_PATH, SCHEMA)


with connect() as _conn:  # sold lists from before descriptions were kept
    add_columns(_conn, "sold", {"description": "TEXT"})


def _clean(data: dict) -> dict:
    """User-filled fields: trimmed text (empty -> None) and a whole, positive sold price."""
    out = {}
    for key in EDITABLE:
        if key not in data:
            continue
        value = data[key]
        if key == "sold_price":
            if value in (None, ""):
                out[key] = None
                continue
            try:
                value = int(str(value).replace(" ", "").replace(" ", ""))
            except ValueError:
                raise ValueError("Sold price must be a whole number of Kč.")
            if value < 0:
                raise ValueError("Sold price can't be negative.")
            out[key] = value
        else:
            value = (str(value) if value is not None else "").strip()
            limit = NOTE_MAX if key == "note" else TEXT_MAX
            if len(value) > limit:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} can be at most {limit} characters.")
            out[key] = value or None
    if "sold_at" in out and out["sold_at"] is None:
        del out["sold_at"]  # the date can be changed, not removed
    return out


def _download_image(source: str, ad_id: str, url: str | None) -> str | None:
    """Keep a local copy of the ad's picture; the site deletes it with the ad. Best effort."""
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        kind = r.headers.get("Content-Type", "").split(";")[0].strip()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "image/avif": ".avif"}.get(kind)
        if not ext:
            return None
        os.makedirs(IMAGE_DIR, exist_ok=True)
        name = f"{source}_{re.sub(r'[^A-Za-z0-9_-]', '_', ad_id)}{ext}"
        with open(os.path.join(IMAGE_DIR, name), "wb") as f:
            f.write(r.content)
        return name
    except Exception as e:  # a missing picture must never block recording the sale
        print(f"Could not save the picture of {source}/{ad_id}: {e}")
        return None


def record(source: str, ad: dict, data: dict) -> dict:
    """Add (or update) the sale of one ad. `ad` is the snapshot from the source watchlist:
    title, brand, make, category, asking_price, price_start/min/max, url, image_url, first_seen, description."""
    if source not in SOURCES:
        raise ValueError(f"Unknown source: {source}")
    fields = _clean(data)
    ts = now()
    with connect() as conn:
        old = conn.execute("SELECT image_file FROM sold WHERE source=? AND ad_id=?", (source, ad["id"])).fetchone()
    image_file = old["image_file"] if old and old["image_file"] else _download_image(source, ad["id"], ad.get("image_url"))
    row = {
        "source": source, "ad_id": ad["id"], "title": ad.get("title"), "category": ad.get("category"),
        "brand": fields.get("brand", ad.get("brand")), "make": fields.get("make", ad.get("make")),
        "note": fields.get("note"), "sold_price": fields.get("sold_price"), "where_sold": fields.get("where_sold"),
        "asking_price": ad.get("asking_price"), "price_start": ad.get("price_start"),
        "price_min": ad.get("price_min"), "price_max": ad.get("price_max"),
        "url": ad.get("url"), "image_url": ad.get("image_url"), "image_file": image_file,
        "first_seen": ad.get("first_seen"), "description": ad.get("description"), "sold_at": fields.get("sold_at") or ad.get("sold_at") or ad.get("gone_at") or ts,
        "created_at": ts,
    }
    cols = ", ".join(row)
    with connect() as conn:
        conn.execute(
            f"""INSERT INTO sold ({cols}) VALUES ({", ".join(":" + c for c in row)})
                ON CONFLICT (source, ad_id) DO UPDATE SET
                  {", ".join(f"{c}=excluded.{c}" for c in row if c not in ("source", "ad_id", "created_at"))}""",
            row,
        )
        return _row(conn.execute("SELECT * FROM sold WHERE source=? AND ad_id=?", (source, ad["id"])).fetchone())


def _row(r) -> dict:
    d = dict(r)
    d["source_name"] = SOURCES.get(d["source"], d["source"])
    d["image"] = f"/sold-images/{d['image_file']}" if d["image_file"] else d["image_url"]
    return d


def list_sold() -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM sold ORDER BY sold_at DESC, id DESC")]


def recorded_ids(source: str) -> list[str]:
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT ad_id FROM sold WHERE source=?", (source,))]


def update(sold_id: int, data: dict) -> dict | None:
    fields = _clean(data)
    with connect() as conn:
        if fields:
            conn.execute(f"UPDATE sold SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         (*fields.values(), sold_id))
        r = conn.execute("SELECT * FROM sold WHERE id=?", (sold_id,)).fetchone()
        return _row(r) if r else None


def delete(source: str, ad_id: str) -> bool:
    """Remove a record (undo of "Move to sold", or delete on the sold page). Its picture goes too."""
    with connect() as conn:
        r = conn.execute("SELECT image_file FROM sold WHERE source=? AND ad_id=?", (source, ad_id)).fetchone()
        if not r:
            return False
        conn.execute("DELETE FROM sold WHERE source=? AND ad_id=?", (source, ad_id))
    if r["image_file"]:
        try:
            os.remove(os.path.join(IMAGE_DIR, r["image_file"]))
        except OSError:
            pass
    return True


def get(sold_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM sold WHERE id=?", (sold_id,)).fetchone()
        return _row(r) if r else None

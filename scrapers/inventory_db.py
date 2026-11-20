"""SQLite storage for the inventory: gear you bought, what it cost you, what you hope to sell it for,
and what it actually sold for. Everything here is typed in by hand on the /inventory/ page.

Money is whole Kč. Dates are YYYY-MM-DD. An item is "in stock" until it has a sold date or a sold price.
While in stock it can be marked "for sale", with the date you started selling it (listed_at).

Derived values (added to every row, not stored):
    cost        bought price + buying costs + extra costs
    profit      sold price - bought price
    net_profit  sold price - cost
"""

import os
import re

from .common import BASE_DIR, add_columns, connect as _connect, now

DB_PATH = os.environ.get("INVENTORY_DB", os.path.join(BASE_DIR, "inventory.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT,
    brand         TEXT,
    model         TEXT,
    bought_at     TEXT,
    bought_price  INTEGER,
    buy_costs     INTEGER,
    extra_costs   INTEGER,
    sell_min      INTEGER,
    sell_expected INTEGER,
    sell_max      INTEGER,
    for_sale      INTEGER NOT NULL DEFAULT 0,
    listed_at     TEXT,
    sold_at       TEXT,
    sold_price    INTEGER,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

TEXT_FIELDS = {"category": 200, "brand": 200, "model": 300, "note": 10000}
MONEY_FIELDS = ("bought_price", "buy_costs", "extra_costs", "sell_min", "sell_expected", "sell_max", "sold_price")
DATE_FIELDS = ("bought_at", "listed_at", "sold_at")
LABELS = {
    "bought_price": "Bought price", "buy_costs": "Buying costs", "extra_costs": "Extra costs",
    "sell_min": "Min. price", "sell_expected": "Expected price", "sell_max": "Max. price", "sold_price": "Sold price",
    "bought_at": "Bought date", "listed_at": "For sale since", "sold_at": "Sold date",
}


def connect():
    return _connect(DB_PATH, SCHEMA)


with connect() as _conn:  # inventories from before "for sale" was added
    add_columns(_conn, "inventory", {"for_sale": "INTEGER NOT NULL DEFAULT 0", "listed_at": "TEXT"})


def _clean(data: dict) -> dict:
    """Only the fields present in `data`: trimmed text (empty -> None), whole non-negative Kč, ISO dates."""
    out = {}
    for key, limit in TEXT_FIELDS.items():
        if key in data:
            value = (str(data[key]) if data[key] is not None else "").strip()
            if len(value) > limit:
                raise ValueError(f"{key.capitalize()} can be at most {limit} characters.")
            out[key] = value or None
    for key in MONEY_FIELDS:
        if key in data:
            value = data[key]
            if value in (None, ""):
                out[key] = None
                continue
            try:
                value = int(re.sub(r"[\s ]", "", str(value)))
            except ValueError:
                raise ValueError(f"{LABELS[key]} must be a whole number of Kč.")
            if value < 0:
                raise ValueError(f"{LABELS[key]} can't be negative.")
            out[key] = value
    for key in DATE_FIELDS:
        if key in data:
            value = (str(data[key]) if data[key] is not None else "").strip()[:10]
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(f"{LABELS[key]} must be a date (YYYY-MM-DD).")
            out[key] = value or None
    if "for_sale" in data:
        value = data["for_sale"]
        out["for_sale"] = 1 if value in (True, 1, "1", "true", "on", "yes") else 0
    return out


def _check(row: dict) -> None:
    if not (row.get("brand") or row.get("model")):
        raise ValueError("Fill in at least the brand or the model.")
    lo, mid, hi = row.get("sell_min"), row.get("sell_expected"), row.get("sell_max")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Min. price is higher than max. price.")
    if mid is not None and ((lo is not None and mid < lo) or (hi is not None and mid > hi)):
        raise ValueError("Expected price should be between min. and max. price.")
    if row.get("bought_at") and row.get("sold_at") and row["sold_at"] < row["bought_at"]:
        raise ValueError("Sold date is before the bought date.")
    if row.get("bought_at") and row.get("listed_at") and row["listed_at"] < row["bought_at"]:
        raise ValueError("For sale since is before the bought date.")


def _row(r) -> dict:
    d = dict(r)
    costs = [d["bought_price"], d["buy_costs"], d["extra_costs"]]
    d["cost"] = sum(c or 0 for c in costs) if any(c is not None for c in costs) else None
    d["sold"] = d["sold_at"] is not None or d["sold_price"] is not None
    d["for_sale"] = bool(d["for_sale"])
    sold = d["sold_price"]
    d["profit"] = sold - d["bought_price"] if sold is not None and d["bought_price"] is not None else None
    d["net_profit"] = sold - d["cost"] if sold is not None and d["cost"] is not None else None
    return d


def list_items() -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM inventory ORDER BY COALESCE(bought_at, created_at) DESC, id DESC")]


def get(item_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()
        return _row(r) if r else None


def add(data: dict) -> dict:
    fields = _clean(data)
    _check(fields)
    ts = now()
    row = {**fields, "created_at": ts, "updated_at": ts}
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO inventory ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                           tuple(row.values()))
        return _row(conn.execute("SELECT * FROM inventory WHERE id=?", (cur.lastrowid,)).fetchone())


def update(item_id: int, data: dict) -> dict | None:
    fields = _clean(data)
    with connect() as conn:
        r = conn.execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()
        if r is None:
            return None
        _check({**dict(r), **fields})
        if fields:
            fields["updated_at"] = now()
            conn.execute(f"UPDATE inventory SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         (*fields.values(), item_id))
        return _row(conn.execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone())


def delete(item_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM inventory WHERE id=?", (item_id,)).rowcount > 0


def summary() -> dict:
    items = list_items()
    stock = [i for i in items if not i["sold"]]
    sold = [i for i in items if i["sold"]]
    return {
        "in_stock": len(stock),
        "for_sale": sum(i["for_sale"] for i in stock),
        "sold": len(sold),
        "stock_cost": sum(i["cost"] or 0 for i in stock),
        "net_profit": sum(i["net_profit"] or 0 for i in sold),
    }

"""Helpers shared by all scrapers: HTTP defaults, price parsing, logging and SQLite access."""

import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str = os.path.join(BASE_DIR, ".env")) -> None:
    """Read KEY=value lines from .env (secrets such as the Telegram token) into os.environ.
    Values already set in the environment win. The file is git-ignored."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Ad status values shared by every watchlist:
#   new    - seen but not decided yet
#   watch  - watched until the user changes it
#   price  - watched, shown only when the price drops to target_price or below
#   ignore - "don't watch" (archive)
#   sold   - no longer on the site (or moved here by the user)
STATUSES = {"new", "watch", "price", "ignore", "sold"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_price(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def block_text(node) -> str:
    """Text of an HTML block with its line breaks kept (<br> becomes a new line), spaces tidied."""
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with("\x00")  # line breaks in the HTML source mean nothing; only <br> does
    lines = [" ".join(line.split()) for line in node.get_text().split("\x00")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def add_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Add columns that an older database file doesn't have yet."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


@contextmanager
def connect(path: str, schema: str):
    """Open a connection, make sure the schema exists, commit on success, and always close it."""
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(schema)
        with conn:
            yield conn
    finally:
        conn.close()


def price_stats(conn) -> dict[str, dict]:
    """Per ad: the first price we saw, and the lowest and highest since (prices of 'cena dohodou'
    and other non-numbers are left out). Both sources keep a price_history table."""
    rows = conn.execute(
        """SELECT ad_id,
                  (SELECT p2.price_czk FROM price_history p2
                    WHERE p2.ad_id = p.ad_id AND p2.price_czk IS NOT NULL
                    ORDER BY p2.seen_at, p2.rowid LIMIT 1) AS price_start,
                  MIN(price_czk) AS price_min,
                  MAX(price_czk) AS price_max,
                  COUNT(DISTINCT price_czk) AS price_count
           FROM price_history p WHERE price_czk IS NOT NULL GROUP BY ad_id"""
    ).fetchall()
    return {r["ad_id"]: {"price_start": r["price_start"], "price_min": r["price_min"],
                         "price_max": r["price_max"], "price_count": r["price_count"]} for r in rows}


def log_to_file_if_windowless(name: str) -> None:
    """Under pythonw (scheduled tasks) there is no console, so send output to logs/<name>.log."""
    if sys.stdout is None or sys.stderr is None:
        os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
        log = open(os.path.join(BASE_DIR, "logs", f"{name}.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

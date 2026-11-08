"""Scrape the list of boutique guitars from kytary.cz."""

import csv
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db

START_URL = "https://kytary.cz/boutique/boutique-kytary/"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_CSV = os.path.join(BASE_DIR, "guitars.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def parse_price(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def parse_page(html: str, page_url: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    guitars = []
    for article in soup.select("#k-item-container article"):
        link = article.find("a", href=True)
        title = article.select_one(".title")
        if not link or not title:
            continue
        parts = title.find_all("span", recursive=False)
        name_span = parts[0] if parts else title
        brand = name_span.find("strong")
        price = article.select_one(".price .sell")
        stock = article.select_one(".price .wst")
        img = article.select_one(".img img")
        guitars.append({
            "id": link["href"].rstrip("/").split("/")[-1],
            "brand": brand.get_text(strip=True) if brand else "",
            "name": name_span.get_text(" ", strip=True),
            "type": parts[1].get_text(strip=True) if len(parts) > 1 else "",
            "price_czk": parse_price(price.get_text()) if price else None,
            "availability": stock.get_text(strip=True) if stock else "",
            "url": urljoin(page_url, link["href"]),
            "image_url": img.get("src", "") if img else "",
        })

    next_link = soup.find("link", rel="next")
    return guitars, next_link["href"] if next_link else None


def scrape(start_url: str = START_URL) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    guitars, url = [], start_url
    while url:
        print(f"Fetching {url}")
        response = session.get(url, timeout=30)
        response.raise_for_status()
        page_guitars, url = parse_page(response.text, response.url)
        guitars.extend(page_guitars)
        time.sleep(1)  # be polite
    return guitars


def log_to_file_if_windowless(name: str) -> None:
    """Under pythonw (scheduled tasks) there is no console, so send output to logs/<name>.log."""
    if sys.stdout is None or sys.stderr is None:
        os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
        log = open(os.path.join(BASE_DIR, "logs", f"{name}.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log


def main() -> None:
    log_to_file_if_windowless("scraper")
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting scrape")
    guitars = scrape()
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(guitars[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(guitars)
    result = db.sync(guitars)
    print(f"Found {result['found']} guitars: {result['new_ads']} new, {result['gone_ads']} gone. "
          f"Saved to {db.DB_PATH} and {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

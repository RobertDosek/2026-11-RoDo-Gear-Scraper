"""Scrape the list of boutique guitars from kytary.cz (the "Boutique kytary" watchlist)."""

import csv
import os
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import kytary_boutique_db as db
from .common import BASE_DIR, HEADERS, block_text, log_to_file_if_windowless, parse_price

START_URL = "https://kytary.cz/boutique/boutique-kytary/"
OUTPUT_CSV = os.path.join(BASE_DIR, "guitars.csv")


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


def parse_description(html: str) -> str:
    """The "Popis produktu" text from a guitar's own page."""
    box = BeautifulSoup(html, "html.parser").select_one("#pdp-desc")
    if box is None:
        return ""
    for extra in box.select(".head3, .pdp-descrating"):
        extra.decompose()
    return block_text(box)


def fill_descriptions(ad_ids: list[str] | None = None) -> int:
    """Save the description of guitars you watch while their page is still up. Runs when you pick
    Watch / Price under... and after each hourly run for anything missed."""
    session = requests.Session()
    session.headers.update(HEADERS)
    saved = 0
    for ad in db.ads_missing_description(ad_ids):
        try:
            response = session.get(ad["url"], timeout=30)
            response.raise_for_status()
            text = parse_description(response.text)
        except Exception as e:
            print(f"Could not load the description of {ad['id']}: {e}")
            continue
        if text:
            db.set_description(ad["id"], text)
            saved += 1
        time.sleep(1)  # be polite
    return saved


def main() -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting scrape")
    guitars = scrape()
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(guitars[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(guitars)
    result = db.sync(guitars)
    print(f"Found {result['found']} guitars: {result['new_ads']} new, {result['gone_ads']} gone. "
          f"Saved to {db.DB_PATH} and {OUTPUT_CSV}")
    described = fill_descriptions()
    if described:
        print(f"Descriptions saved: {described}")


if __name__ == "__main__":
    main()

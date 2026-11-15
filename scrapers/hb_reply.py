"""Answering an ad on hudebnibazar.cz from your own account.

"Odpovědět na inzerát" needs a logged-in user, so this logs in with your account details from .env
(git-ignored):
    HUDEBNIBAZAR_EMAIL=you@example.com
    HUDEBNIBAZAR_PASSWORD=...

The answer form is only shown to logged-in users (see REPLY_FORM). If the site changes it, check the
current form structure with (nothing is sent):

    python -m scrapers.hb_reply probe https://hudebnibazar.cz/<some-ad>/ID123456/

and share the printed form structure (field names only, no values are printed).
"""

import os
import sys

import requests
from bs4 import BeautifulSoup

from .common import HEADERS

BASE = "https://hudebnibazar.cz"
LOGIN_URL = BASE + "/uzivatel/prihlasit4m/"
LOGGED_OUT_MARK = "Nejdříve se přihlaste"

# The answer form on an ad page, as shown to a logged-in user (mapped with probe on 2026-11-15):
#   <form id="form_inzerat_detail" action="/inzerat/detail4m/" method="post">
#     textarea Vzkaz, hidden form_uid (per session), form_akce, IDinzerat, submit submitbtn
REPLY_FORM = {"id": "form_inzerat_detail", "text": "Vzkaz"}
SENT_MARKS = ("odeslán", "odeslan")  # "Vzkaz byl odeslán" style confirmation, accents optional


def credentials() -> tuple[str, str]:
    return os.environ.get("HUDEBNIBAZAR_EMAIL", "").strip(), os.environ.get("HUDEBNIBAZAR_PASSWORD", "")


def ready() -> tuple[bool, str]:
    """Can answers really be sent? (ok, reason when not)"""
    email, password = credentials()
    if not (email and password):
        return False, "hudebnibazar.cz login is not set in .env"
    if REPLY_FORM is None:
        return False, "the answer form on hudebnibazar.cz is not mapped yet"
    return True, ""


def login(page_url: str = BASE + "/") -> requests.Session:
    """Log in the way the site's own login form does: open a page that carries the form, take all its
    fields (including the per-session form_uid token), fill in e-mail and password and post it."""
    email, password = credentials()
    if not (email and password):
        raise RuntimeError("Set HUDEBNIBAZAR_EMAIL and HUDEBNIBAZAR_PASSWORD in .env")
    session = requests.Session()
    session.headers.update(HEADERS)
    page = session.get(page_url, timeout=30)
    page.raise_for_status()
    form = BeautifulSoup(page.text, "html.parser").find("form", id="form_uzivatel_prihlasit")
    data = {}
    if form is not None:
        for field in form.find_all("input"):
            name = field.get("name")
            if not name or field.get("type") == "submit":
                continue
            if field.get("type") == "checkbox" and not field.has_attr("checked"):
                continue
            data[name] = field.get("value", "")
    data.update({"Email": email, "HesloNeHash": password, "chbox_trvale": "1", "trvale": "1",
                 "submitbtn": "Přihlásit se"})
    response = session.post(LOGIN_URL, data=data, headers={"Referer": page.url, "Origin": BASE}, timeout=30)
    response.raise_for_status()
    return session


def logged_in(html: str) -> bool:
    return LOGGED_OUT_MARK not in html and 'id="form_uzivatel_prihlasit"' not in html


def send(ad_url: str, text: str) -> None:
    """Send `text` to the seller through the ad's own answer form. Raises when it can't be confirmed."""
    ok, reason = ready()
    if not ok:
        raise RuntimeError(f"Can't send answers yet: {reason}")
    text = (text or "").strip()
    if not text:
        raise ValueError("The answer is empty")
    session = login(ad_url)
    page = session.get(ad_url, timeout=30)
    page.raise_for_status()
    if not logged_in(page.text):
        raise RuntimeError("Login to hudebnibazar.cz failed - check the e-mail and password in .env")
    form = BeautifulSoup(page.text, "html.parser").find("form", id=REPLY_FORM["id"])
    if form is None:
        raise RuntimeError("The answer form isn't on the ad page (sold, removed, or your own ad?)")
    data = {}
    for field in form.find_all(["input", "textarea"]):
        name = field.get("name")
        if name and field.get("type") != "submit":
            data[name] = field.get("value", "")
    submit = form.find("input", attrs={"type": "submit"})
    if submit is not None and submit.get("name"):
        data[submit["name"]] = submit.get("value", "")
    data[REPLY_FORM["text"]] = text
    action = requests.compat.urljoin(page.url, form.get("action") or page.url)
    response = session.post(action, data=data, headers={"Referer": page.url, "Origin": BASE}, timeout=30)
    response.raise_for_status()
    body = response.text.lower()
    if not any(mark in body for mark in SENT_MARKS):
        raise RuntimeError(f"Sent, but the site didn't confirm it (HTTP {response.status_code}) - "
                           "check your messages on hudebnibazar.cz before trying again")


def probe(ad_url: str) -> None:
    """Log in, open the ad and print the structure of its forms. Sends nothing."""
    session = login(ad_url)
    page = session.get(ad_url, timeout=30)
    print(f"Ad page: HTTP {page.status_code}")
    print("Logged in: " + ("yes" if logged_in(page.text) else
                           "NO - check the e-mail and password (it must be a password account, "
                           "not Google/Seznam/Facebook login)"))
    soup = BeautifulSoup(page.text, "html.parser")
    for form in soup.find_all("form"):
        print(f"\nFORM action={form.get('action')} method={form.get('method')} id={form.get('id')} class={form.get('class')}")
        for field in form.find_all(["input", "textarea", "select", "button"]):
            print(f"   {field.name} type={field.get('type')} name={field.get('name')} id={field.get('id')}")
    for node in soup.select("[onclick], [data-bs-target], dialog"):
        label = " ".join(node.get_text(" ").split())[:50]
        if any(w in label.lower() for w in ("odpov", "zpráv", "zprav", "napsat")):
            print(f"\nELEMENT {node.name} '{label}' onclick={node.get('onclick')} id={node.get('id')}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "probe":
        sys.stdout.reconfigure(encoding="utf-8")
        probe(sys.argv[2])
    else:
        print(__doc__)

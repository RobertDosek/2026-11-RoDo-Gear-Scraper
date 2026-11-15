"""Minimal Telegram Bot API client for direct alerts.

Set in .env (git-ignored):
    TELEGRAM_BOT_TOKEN=123456:ABC...   from @BotFather (/newbot)
    TELEGRAM_CHAT_ID=123456789         your own chat with the bot (see README / getUpdates)

Only messages and button taps from TELEGRAM_CHAT_ID are acted on; anything else is ignored.
"""

import html
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def configured() -> bool:
    return bool(token() and chat_id())


def call(method: str, timeout: float = 15, **params) -> dict:
    response = requests.post(API.format(token=token(), method=method), json=params, timeout=timeout)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description', response.status_code)}")
    return data["result"]


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def send(text: str, buttons: list[list[dict]] | None = None, photo: str | None = None,
         reply_to: int | None = None) -> int:
    """Send a message (with a photo when given) to your chat. Returns the message id."""
    params = {"chat_id": chat_id(), "parse_mode": "HTML"}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if photo and len(text) <= 1024:  # photo captions are limited to 1024 characters
        try:
            return call("sendPhoto", photo=photo, caption=text, **params)["message_id"]
        except RuntimeError:
            pass  # Telegram couldn't fetch the image: send the text alone
    return call("sendMessage", text=text, link_preview_options={"is_disabled": True}, **params)["message_id"]


def clear_buttons(message_id: int) -> None:
    try:
        call("editMessageReplyMarkup", chat_id=chat_id(), message_id=message_id, reply_markup={"inline_keyboard": []})
    except RuntimeError:
        pass  # message too old or already without buttons


def answer_button(callback_id: str, text: str = "") -> None:
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
    except RuntimeError:
        pass


def updates(offset: int | None, wait: int = 25) -> list[dict]:
    """Long-poll for new messages and button taps."""
    params = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return call("getUpdates", timeout=wait + 10, **params)


def set_commands(commands: list[tuple[str, str]]) -> None:
    call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

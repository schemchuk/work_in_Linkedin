"""Minimal Telegram sender for scripts that are not the bot process.

Used by project_agent.py to push post drafts and publish confirmations into
the chat. The interactive part (buttons) is handled by assistant_bot.py.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def send_telegram(text: str, buttons: list[list[dict]] | None = None) -> int | None:
    """Send an HTML-formatted message to the configured chat.

    Returns the Telegram message_id on success, None on failure.
    buttons: Telegram inline_keyboard structure, e.g.
        [[{"text": "OK", "callback_data": "x"}]]
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        return None

    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=30,
        )
        if r.status_code != 200:
            logger.error(f"Telegram send failed: {r.text}")
            return None
        return r.json()["result"]["message_id"]
    except requests.RequestException as e:
        logger.error(f"Telegram send failed: {e}")
        return None


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

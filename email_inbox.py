"""IMAP reader for LinkedIn notification emails (Gmail).

Uses the same Gmail app password as the failure notifications. Tracks the
last processed IMAP UID in bot_state.json so each notification is handled
exactly once.

Env vars (with fallbacks to the NOTIFY_* ones):
    LINKEDIN_NOTIF_IMAP_USER      - Gmail address that receives LinkedIn mail
    LINKEDIN_NOTIF_IMAP_PASSWORD  - Gmail app password for that account
"""

import email
import email.header
import email.message
import imaplib
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
STATE_FILE = Path(__file__).parent / "bot_state.json"

LINKEDIN_SENDERS = ("linkedin.com",)


def _imap_credentials() -> tuple[str, str] | None:
    user = os.getenv("LINKEDIN_NOTIF_IMAP_USER") or os.getenv("NOTIFY_EMAIL_FROM")
    password = os.getenv("LINKEDIN_NOTIF_IMAP_PASSWORD") or os.getenv("NOTIFY_EMAIL_PASSWORD")
    if not user or not password:
        return None
    return user, password


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("bot_state.json is corrupted, starting fresh")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    return "".join(
        p.decode(enc or "utf-8", errors="ignore") if isinstance(p, bytes) else p
        for p, enc in parts
    )


def _strip_html(html: str) -> str:
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # Keep hrefs — the post link lives in an <a> tag
    text = re.sub(r'<a[^>]+href="([^"]+)"[^>]*>', r" [link: \1] ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#8203;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_text(msg: email.message.Message) -> str:
    plain, html = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", errors="ignore"
            )
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = body
        elif ctype == "text/html" and html is None:
            html = body
    # LinkedIn's plain part often lacks links; prefer stripped HTML when present
    if html:
        return _strip_html(html)
    return plain or ""


def fetch_new_notifications(limit: int = 10) -> list[dict]:
    """Return new LinkedIn notification emails as [{uid, subject, text}]."""
    creds = _imap_credentials()
    if not creds:
        logger.warning("IMAP credentials not configured — skipping inbox poll")
        return []
    user, password = creds

    state = load_state()
    last_uid = int(state.get("last_uid", 0))

    notifications = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, password)
            imap.select("INBOX", readonly=True)

            _, data = imap.uid("search", None, "FROM", "linkedin.com")
            uids = [int(u) for u in data[0].split()] if data and data[0] else []
            new_uids = sorted(u for u in uids if u > last_uid)

            if last_uid == 0 and new_uids:
                # First run: don't flood the chat with the whole mailbox history
                state["last_uid"] = new_uids[-1]
                save_state(state)
                logger.info(f"First run: skipping {len(new_uids)} old LinkedIn emails")
                return []

            for uid in new_uids[-limit:]:
                _, msg_data = imap.uid("fetch", str(uid), "(RFC822)")
                if not msg_data or msg_data[0] is None:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sender = _decode_header(msg.get("From"))
                if not any(s in sender.lower() for s in LINKEDIN_SENDERS):
                    continue
                notifications.append({
                    "uid": uid,
                    "subject": _decode_header(msg.get("Subject")),
                    "text": _extract_text(msg),
                })

            if new_uids:
                state["last_uid"] = new_uids[-1]
                save_state(state)
    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        return []

    logger.info(f"Fetched {len(notifications)} new LinkedIn notifications")
    return notifications

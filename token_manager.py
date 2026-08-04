"""LinkedIn access-token lifetime tracking and (future) auto-refresh.

LinkedIn gives ordinary apps a 60-day access token WITHOUT a refresh token —
programmatic refresh is reserved for approved Marketing Developer Platform
partners. So true auto-refresh is impossible today; instead we:

    1. store the token expiry date in .env (LINKEDIN_TOKEN_EXPIRES_AT),
    2. warn via Telegram/email 7 days before it expires,
    3. if LinkedIn ever returns a refresh token (LINKEDIN_REFRESH_TOKEN in
       .env), try_refresh() will silently renew the access token — the code
       path is ready and becomes active automatically.
"""

import logging
import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

WARN_DAYS_BEFORE = 7
DATE_FORMAT = "%Y-%m-%d"


def _set_env_var(name: str, value: str) -> None:
    """Persist a variable into this project's .env (same logic as linkedin_auth)."""
    from linkedin_auth import set_env_var
    set_env_var(name, value)


def days_until_expiry() -> int | None:
    """Days left until the LinkedIn token expires, or None if date unknown."""
    expires_at = os.getenv("LINKEDIN_TOKEN_EXPIRES_AT")
    if not expires_at:
        return None
    try:
        expiry = datetime.strptime(expires_at, DATE_FORMAT)
    except ValueError:
        return None
    return (expiry - datetime.now()).days


def try_refresh() -> bool:
    """Renew the access token using a refresh token, if we have one.

    Returns True if the token was refreshed. With a standard LinkedIn app
    there is no refresh token, so this returns False and the caller should
    fall back to warning the user to re-run linkedin_auth.py --force.
    """
    refresh_token = os.getenv("LINKEDIN_REFRESH_TOKEN")
    if not refresh_token:
        return False

    response = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.getenv("LINKEDIN_CLIENT_ID"),
            "client_secret": os.getenv("LINKEDIN_CLIENT_SECRET"),
        },
        timeout=30,
    )
    if response.status_code != 200:
        logger.error(f"Token refresh failed: {response.text}")
        return False

    data = response.json()
    _set_env_var("LINKEDIN_ACCESS_TOKEN", data["access_token"])
    expires_at = datetime.now() + timedelta(seconds=data.get("expires_in", 60 * 86400))
    _set_env_var("LINKEDIN_TOKEN_EXPIRES_AT", expires_at.strftime(DATE_FORMAT))
    if data.get("refresh_token"):
        _set_env_var("LINKEDIN_REFRESH_TOKEN", data["refresh_token"])
    logger.info(f"LinkedIn token refreshed, valid until {expires_at.date()}")
    return True


def check_token() -> str | None:
    """Return a warning message if the token needs attention, else None.

    Tries a silent refresh first when the token is close to expiry.
    """
    days = days_until_expiry()

    if days is None:
        return (
            "⚠️ Дата закінчення LinkedIn-токена невідома. "
            "Переавторизуйся (python linkedin_auth.py --force), "
            "щоб почати відлік 60 днів."
        )

    if days > WARN_DAYS_BEFORE:
        return None

    if try_refresh():
        return None  # silently renewed — nothing to report

    if days < 0:
        return (
            "🔴 LinkedIn-токен ПРОТУХ. Пости не публікуються!\n"
            "Виконай: python linkedin_auth.py --force"
        )
    return (
        f"🟡 LinkedIn-токен закінчиться через {days} дн. "
        "(автооновлення LinkedIn не дає — потрібен браузер).\n"
        "Виконай: python linkedin_auth.py --force"
    )

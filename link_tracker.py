"""Click-tracked short links via short.io.

Why: LinkedIn does not forward a usable Referer header to link destinations
— confirmed empirically (a real published post's GitHub traffic showed only
"github.com" as referrer, never "linkedin.com"), so GitHub's own traffic
stats can never be attributed to a specific post. A short link WE create
per post/language is the only way to get an exact click count tied to one
specific post.

Setup (manual, one-time — this needs an external account I can't create
for you):
    1. Sign up at https://short.io (free tier: 1000 links/month + clicks)
    2. Verify a domain in your short.io dashboard — their free provided
       subdomain (something.short.gy) works fine, no custom domain needed
    3. Generate an API key: Settings -> Integrations & API
    4. Put both in .env: SHORTIO_API_KEY, SHORTIO_DOMAIN
    5. Verify the setup: python link_tracker.py

Everything here fails open: if short.io isn't configured or a call fails,
functions return None/{} and the caller falls back to the plain GitHub
link — a broken tracking integration must never block a post from going out.

State: short_links.json (gitignored) maps short.io link id -> {repo, lang,
date, short_url, destination, clicks_history}.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_KEY = os.getenv("SHORTIO_API_KEY")
DOMAIN = os.getenv("SHORTIO_DOMAIN")
STATE_FILE = Path(__file__).parent / "short_links.json"

CREATE_URL = "https://api.short.io/links"
STATS_URL = "https://api-v2.short.io/statistics/link/{link_id}"


def is_configured() -> bool:
    return bool(API_KEY and DOMAIN)


def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("short_links.json is corrupted, ignoring it")
    return {}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def create_tracked_link(destination_url: str, repo: str, lang: str) -> str | None:
    """Create a short.io link for one post's repo URL.

    Returns the short URL to embed in the post, or None (caller must fall
    back to the plain destination_url) if short.io isn't configured or the
    call fails.
    """
    if not is_configured():
        return None

    date = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "originalURL": destination_url,
        "domain": DOMAIN,
        "allowDuplicates": True,
        "title": f"{repo} — {lang} — {date}",
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": API_KEY,
    }
    try:
        r = requests.post(CREATE_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        logger.error(f"short.io link creation failed: {e}")
        return None

    link_id = data.get("idString") or data.get("id")
    short_url = data.get("shortURL")
    if not link_id or not short_url:
        logger.error(f"short.io response missing expected fields: {data}")
        return None

    state = _load()
    state[str(link_id)] = {
        "repo": repo,
        "lang": lang,
        "date": date,
        "short_url": short_url,
        "destination": destination_url,
        "clicks_history": [],
    }
    _save(state)
    logger.info(f"Tracked link created for {repo} ({lang}): {short_url}")
    return short_url


def refresh_click_counts() -> dict:
    """Fetch current click counts for every tracked link, append today's
    snapshot to each link's history. Returns {link_id: total_clicks}.
    Called by the daily traffic_snapshot.py job.
    """
    state = _load()
    if not state or not is_configured():
        return {}

    today = datetime.now().strftime("%Y-%m-%d")
    headers = {"accept": "application/json", "Authorization": API_KEY}
    results = {}

    for link_id, entry in state.items():
        try:
            r = requests.get(
                STATS_URL.format(link_id=link_id),
                params={"period": "total", "tzOffset": 0},
                headers=headers, timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            logger.warning(f"short.io stats fetch failed for {link_id}: {e}")
            continue

        clicks = data.get("totalClicks", 0)
        history = [h for h in entry.get("clicks_history", []) if h["date"] != today]
        history.append({"date": today, "clicks": clicks})
        entry["clicks_history"] = history
        results[link_id] = clicks

    _save(state)
    return results


def summary() -> list[dict]:
    """Per-link click totals for /stats — most-clicked first."""
    state = _load()
    rows = []
    for entry in state.values():
        history = entry.get("clicks_history", [])
        clicks = history[-1]["clicks"] if history else 0
        rows.append({
            "repo": entry["repo"], "lang": entry["lang"], "date": entry["date"],
            "short_url": entry["short_url"], "clicks": clicks,
        })
    return sorted(rows, key=lambda r: -r["clicks"])


def _self_test() -> None:
    if not is_configured():
        print("❌ SHORTIO_API_KEY / SHORTIO_DOMAIN не задані в .env — див. докстрінг цього файлу.")
        return
    url = create_tracked_link("https://github.com/octocat/Hello-World", "self-test", "test")
    if url:
        print(f"✅ short.io налаштовано правильно. Тестове посилання: {url}")
        print("   (можеш видалити його в дашборді short.io, на роботу бота це не впливає)")
    else:
        print("❌ Не вдалося створити посилання — перевір ключ/домен, деталі в логах вище.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _self_test()

"""Daily snapshot of GitHub repo traffic + short.io click counts.

GitHub's traffic API only keeps a rolling 14-day window — without a daily
snapshot, older numbers are gone for good. Run by traffic-snapshot.timer
(see systemd/), once a day.

Only repos that have ever been linked in a post (posted_history.json) are
worth tracking — traffic for the rest isn't tied to anything we did.
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import link_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "schemchuk")
HISTORY_FILE = Path(__file__).parent / "posted_history.json"
TRAFFIC_FILE = Path(__file__).parent / "traffic_history.json"


def _gh(args: list[str], timeout: int = 60) -> str | None:
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=timeout
        )
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"gh command failed ({' '.join(args)}): {e}")
        return None


def _posted_repos() -> list[str]:
    if not HISTORY_FILE.exists():
        return []
    try:
        entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return sorted({e["repo"] for e in entries})


def _load_traffic() -> dict:
    if TRAFFIC_FILE.exists():
        try:
            return json.loads(TRAFFIC_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("traffic_history.json is corrupted, starting fresh")
    return {}


def _today_entry(daily_array: list[dict], today: str) -> dict:
    """Pick today's row from GitHub's per-day breakdown, or the most recent
    available day if GitHub hasn't processed today's data yet."""
    by_day = {d["timestamp"][:10]: d for d in daily_array}
    if today in by_day:
        return by_day[today]
    if by_day:
        latest = max(by_day)
        return by_day[latest]
    return {"count": 0, "uniques": 0}


def snapshot() -> None:
    repos = _posted_repos()
    if not repos:
        logger.info("No repos have ever been linked in a post — nothing to snapshot")
        return

    traffic = _load_traffic()
    today = datetime.now().strftime("%Y-%m-%d")

    for repo in repos:
        views_raw = _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/traffic/views"])
        if views_raw is None:
            logger.warning(f"No traffic access for {repo} (needs push/admin access)")
            continue
        clones_raw = _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/traffic/clones"])
        referrers_raw = _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/traffic/popular/referrers"])

        views = json.loads(views_raw)
        clones = json.loads(clones_raw) if clones_raw else {"views": [], "clones": []}
        referrers = json.loads(referrers_raw) if referrers_raw else []

        today_views = _today_entry(views.get("views", []), today)
        today_clones = _today_entry(clones.get("clones", []), today)

        traffic.setdefault(repo, {})[today] = {
            "views": today_views["count"],
            "unique_views": today_views["uniques"],
            "clones": today_clones["count"],
            "unique_clones": today_clones["uniques"],
            "referrers": {r["referrer"]: r["count"] for r in referrers},
        }
        logger.info(
            f"{repo}: {today_views['count']} views, {today_clones['count']} clones today"
        )

    TRAFFIC_FILE.write_text(json.dumps(traffic, ensure_ascii=False, indent=2), encoding="utf-8")

    clicks = link_tracker.refresh_click_counts()
    logger.info(f"Refreshed click counts for {len(clicks)} tracked links")


if __name__ == "__main__":
    snapshot()

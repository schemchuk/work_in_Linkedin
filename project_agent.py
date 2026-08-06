"""Weekly LinkedIn agent: posts in German about my GitHub projects.

Pipeline (adapted from agtntLinSysadmin, using the versioned Posts API client):
    1. Collect GitHub activity for the last 7 days via gh CLI
    2. Claude picks one project and writes a German LinkedIn post
    3. Claude generates a DALL-E image prompt, DALL-E 3 renders the image
    4. Image is uploaded to LinkedIn, post is published
    5. Posted project is recorded in posted_history.json to avoid repeats
    6. On any failure an email notification is sent

Draft queue (works together with assistant_bot.py):
    - Wednesday timer runs `python project_agent.py --draft`: generates a
      draft, saves it to pending_post.json and sends it to Telegram, where
      it can be approved / edited / regenerated / skipped
    - Friday timer runs `python project_agent.py`: publishes the pending
      draft (as edited/approved), or generates fresh if no draft exists

Run manually:  python project_agent.py [--draft]
Scheduled:     systemd user timers (see systemd/ and README)
"""

import argparse
import json
import logging
import os
import re
import smtplib
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import series_manager
from agent_prompts import POST_SYSTEM_PROMPT, SERIES_SYSTEM_PROMPT, IMAGE_PROMPT_SYSTEM
from image_gen import generate_image, download_image
from linkedin_client import publish_post, upload_image_to_linkedin
from repo_safety import check_repo
from tg_notify import send_telegram, html_escape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "schemchuk")
HISTORY_FILE = Path(__file__).parent / "posted_history.json"
PENDING_POST_FILE = Path(__file__).parent / "pending_post.json"
ACTIVITY_DAYS = 7
RECENT_WEEKS_TO_AVOID = 4
README_MAX_LENGTH = 2000
MAX_COMMITS_PER_REPO = 30

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["post", "skip"]},
        "repo": {"type": "string"},
        "post_de": {"type": "string"},
        "post_uk": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "repo", "post_de", "post_uk", "reason"],
    "additionalProperties": False,
}

# Delay between publishing the German and the Ukrainian version
POST_GAP_MINUTES = int(os.getenv("POST_GAP_MINUTES", "30"))

anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def send_failure_email(reason: str, details: str = "") -> None:
    """Send an email notification when the agent fails to publish a post."""
    to_addr = os.getenv("NOTIFY_EMAIL_TO")
    from_addr = os.getenv("NOTIFY_EMAIL_FROM")
    password = os.getenv("NOTIFY_EMAIL_PASSWORD")

    if not all([to_addr, from_addr, password]):
        logger.warning("Email credentials not configured — skipping notification")
        return

    subject = f"[Project Agent] Post NOT published — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    body = f"Reason: {reason}\n\n{details}".strip()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        logger.info(f"Failure notification sent to {to_addr}")
    except Exception as e:
        logger.error(f"Could not send email notification: {e}")


def run_gh_command(args: list[str], timeout: int = 60) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def build_project_entry(name: str, description: str | None, language: str | None,
                         visibility: str | None, url: str) -> dict:
    """Shared metadata assembly for both the weekly picker and series continuations.

    The URL is only included when the repo passes every safety gate — a
    missing "url" field is the prompt's signal to omit links.
    """
    entry = {
        "name": name,
        "description": description or "",
        "language": language,
        "visibility": (visibility or "unknown").lower(),
        "readme_excerpt": fetch_readme(name),
    }
    check = check_repo(name)
    if check["safe"]:
        entry["url"] = url
    else:
        logger.info(f"No link for {name}: {'; '.join(check['reasons'])}")
    return entry


def fetch_weekly_activity() -> list[dict]:
    """Return repos with pushes in the last ACTIVITY_DAYS days, with commit messages."""
    since = datetime.now(timezone.utc) - timedelta(days=ACTIVITY_DAYS)
    logger.info(f"Fetching repos for {GITHUB_OWNER} pushed since {since.date()}...")

    stdout = run_gh_command([
        "repo", "list", GITHUB_OWNER,
        "--limit", "100",
        "--json", "name,description,url,visibility,primaryLanguage,pushedAt",
    ])
    repos = json.loads(stdout)

    active = []
    for repo in repos:
        pushed_at = repo.get("pushedAt")
        if not pushed_at:
            continue
        if datetime.fromisoformat(pushed_at.replace("Z", "+00:00")) < since:
            continue

        name = repo["name"]
        commits = fetch_recent_commits(name, since)
        if not commits:
            continue

        entry = build_project_entry(
            name, repo.get("description"),
            (repo.get("primaryLanguage") or {}).get("name"),
            repo.get("visibility"), repo["url"],
        )
        entry["commits"] = commits
        active.append(entry)

    logger.info(f"Found {len(active)} active repos this week")
    return active


def fetch_repo_meta(name: str) -> dict | None:
    """Fetch current metadata for one specific repo (used by series continuations,
    which target a repo directly instead of picking from the week's active ones).
    """
    try:
        stdout = run_gh_command([
            "repo", "view", f"{GITHUB_OWNER}/{name}",
            "--json", "description,url,visibility,primaryLanguage",
        ])
    except subprocess.CalledProcessError:
        return None
    data = json.loads(stdout)
    return build_project_entry(
        name, data.get("description"),
        (data.get("primaryLanguage") or {}).get("name"),
        data.get("visibility"), data.get("url", ""),
    )


def fetch_recent_commits(repo: str, since: datetime) -> list[str]:
    """Return commit messages for a repo since the given date."""
    try:
        stdout = run_gh_command([
            "api",
            f"repos/{GITHUB_OWNER}/{repo}/commits?since={since.isoformat()}"
            f"&per_page={MAX_COMMITS_PER_REPO}",
            "--jq", "[.[].commit.message]",
        ])
        return json.loads(stdout)
    except subprocess.CalledProcessError:
        logger.warning(f"Could not fetch commits for {repo}")
        return []


def fetch_readme(repo: str) -> str | None:
    """Return a README excerpt for a repo, or None."""
    import base64
    try:
        stdout = run_gh_command(["api", f"repos/{GITHUB_OWNER}/{repo}/readme"])
        data = json.loads(stdout)
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return content[:README_MAX_LENGTH]
    except subprocess.CalledProcessError:
        return None


def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history_entry(repo: str, post_id_de: str | None, post_id_uk: str | None) -> None:
    history = load_history()
    history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "repo": repo,
        "post_id": post_id_de,
        "post_id_uk": post_id_uk,
    })
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def recently_posted(history: list[dict]) -> list[dict]:
    cutoff = datetime.now() - timedelta(weeks=RECENT_WEEKS_TO_AVOID)
    return [
        {"repo": h["repo"], "date": h["date"]}
        for h in history
        if datetime.strptime(h["date"], "%Y-%m-%d") >= cutoff
    ]


def write_post(projects: list[dict], history: list[dict]) -> dict | None:
    """Ask Claude to pick a project and write the German post. Returns the parsed JSON."""
    payload = json.dumps({
        "week": datetime.now().strftime("%Y-%m-%d"),
        "projects": projects,
        "recently_posted": recently_posted(history),
    }, ensure_ascii=False)

    response = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=POST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        logger.error("Claude declined the post-writing request (refusal)")
        return None

    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def write_series_post(series: dict) -> dict | None:
    """Ask Claude to write the next installment of an active post series."""
    repo_name = series["repo"]
    meta = fetch_repo_meta(repo_name)
    if meta is None:
        logger.error(f"Series repo '{repo_name}' not found on GitHub — stopping series")
        return None

    last_date = series["history"][-1]["date"] if series.get("history") else series["started"]
    try:
        since_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        commits = fetch_recent_commits(repo_name, since_dt)
    except Exception:
        commits = []

    payload = json.dumps({
        "repo": meta,
        "part": series["part"],
        "total_parts": series.get("total_parts"),
        "recent_commits": commits,
        "previous_parts": [
            {"part": h["part"], "post_de": h["post_de"], "post_uk": h["post_uk"]}
            for h in series.get("history", [])
        ],
    }, ensure_ascii=False)

    response = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SERIES_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        logger.error("Claude declined the series continuation request (refusal)")
        return None

    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def generate_image_prompt(post_text: str) -> str:
    """Ask Claude for a one-line DALL-E prompt for the post."""
    response = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        system=IMAGE_PROMPT_SYSTEM,
        messages=[{"role": "user", "content": post_text}],
    )
    if response.stop_reason == "refusal":
        return "empty"
    return next(b.text for b in response.content if b.type == "text").strip()


_URL_RE = re.compile(r"https?://\S+|(?<![\w/@.])(?:www\.|github\.com/)\S+", re.IGNORECASE)


def strip_disallowed_links(text: str, allowed_repo: str | None) -> str:
    """Remove every URL except the approved repository link.

    Second line of defence: the prompt already forbids unapproved links, but a
    model can still assemble a plausible github.com/<owner>/<repo> address from
    the repo name. Publishing that would point readers at a repo that failed the
    safety gate (private, no README/LICENSE, or secrets in history).
    """
    allowed_url = None
    if allowed_repo:
        allowed_url = f"github.com/{GITHUB_OWNER}/{allowed_repo}".lower()

    def keep(match: re.Match) -> str:
        url = match.group(0)
        if allowed_url and allowed_url in url.lower():
            return url
        logger.warning(f"Stripped disallowed link from post: {url}")
        return ""

    cleaned = _URL_RE.sub(keep, text)
    # Tidy up the blank lines a removed trailing link leaves behind
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def load_pending_post() -> dict | None:
    if PENDING_POST_FILE.exists():
        try:
            return json.loads(PENDING_POST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("pending_post.json is corrupted, ignoring it")
    return None


def _generate_series_part(series: dict) -> dict | None:
    """Generate the next part of an active series. None means the series ended
    (Claude found nothing new to say, a refusal, or the repo vanished) — the
    caller falls back to a normal weekly pick for this run.
    """
    result = write_series_post(series)
    if result is None or result["decision"] == "skip":
        reason = result["reason"] if result else "refusal or empty output"
        logger.info(f"Series about '{series['repo']}' ended: {reason}")
        series_manager.stop()
        send_telegram(
            f"🏁 Серію про <b>{html_escape(series['repo'])}</b> завершено: "
            f"{html_escape(reason)}"
        )
        return None

    repo = series["repo"]
    link_allowed = check_repo(repo)["safe"]
    post_de = strip_disallowed_links(result["post_de"], repo if link_allowed else None)
    post_uk = strip_disallowed_links(result["post_uk"], repo if link_allowed else None)
    image_prompt = generate_image_prompt(post_de)

    return {
        "repo": repo,
        "post_de": post_de,
        "post_uk": post_uk,
        "reason": result["reason"],
        "image_prompt": image_prompt,
        "series_part": series["part"],
        "series_total": series.get("total_parts"),
    }


def _generate() -> dict | None:
    """Generate a bilingual post + image prompt: the next part of an active
    series if one is running, otherwise the normal weekly project pick.

    Returns {repo, post_de, post_uk, reason, image_prompt, [series_part, series_total]}.
    """
    series = series_manager.get_active()
    if series:
        result = _generate_series_part(series)
        if result is not None:
            return result
        # Series ended itself just now (skip/refusal/missing repo) — fall
        # through to a normal pick so this week isn't wasted.

    projects = fetch_weekly_activity()
    if not projects:
        logger.info("No GitHub activity this week — nothing to post")
        return None

    result = write_post(projects, load_history())
    if result is None or result["decision"] == "skip":
        reason = result["reason"] if result else "refusal or empty output"
        logger.info(f"No post this week: {reason}")
        return None

    repo = result["repo"]
    link_allowed = any(p["name"] == repo and "url" in p for p in projects)
    post_de = strip_disallowed_links(result["post_de"], repo if link_allowed else None)
    post_uk = strip_disallowed_links(result["post_uk"], repo if link_allowed else None)

    image_prompt = generate_image_prompt(post_de)
    return {
        "repo": repo,
        "post_de": post_de,
        "post_uk": post_uk,
        "reason": result["reason"],
        "image_prompt": image_prompt,
    }


def generate_draft() -> dict | None:
    """Generate a draft, save it to the pending queue and send it to Telegram.

    Called by the Wednesday timer (--draft) and by the bot's /draft command.
    """
    draft = _generate()
    if draft is None:
        return None

    draft.update({"status": "pending", "created": datetime.now().strftime("%Y-%m-%d %H:%M")})

    series_label = ""
    if draft.get("series_part"):
        total = draft.get("series_total")
        series_label = (
            f"🔥 Серія — частина {draft['series_part']}"
            + (f"/{total}" if total else " (без ліміту)") + "\n"
        )

    message_id = send_telegram(
        f"{series_label}📝 Чернетка поста про <b>{html_escape(draft['repo'])}</b>\n"
        f"<i>{html_escape(draft['reason'])}</i>\n\n"
        f"🇩🇪 <b>Німецька версія:</b>\n{html_escape(draft['post_de'])}\n\n"
        f"🇺🇦 <b>Українська версія:</b>\n{html_escape(draft['post_uk'])}\n\n"
        "✏️ Щоб відредагувати — надішли новий текст відповіддю (reply) на це "
        "повідомлення: текст кирилицею замінить українську версію, "
        "латиницею — німецьку.",
        buttons=[[
            {"text": "✅ Схвалити", "callback_data": "post_approve"},
            {"text": "🔁 Перегенерувати", "callback_data": "post_regen"},
            {"text": "❌ Пропустити", "callback_data": "post_skip"},
        ]],
    )
    if message_id:
        draft["tg_message_id"] = message_id

    PENDING_POST_FILE.write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Draft about '{draft['repo']}' saved and sent to Telegram")
    return draft


def _publish(post_de: str, post_uk: str, repo: str, image_prompt: str,
              series_part: int | None = None) -> None:
    """Publish the German post, wait POST_GAP_MINUTES, publish the Ukrainian one.

    The same DALL-E image is used for both posts (uploaded to LinkedIn once
    per post — asset URNs are single-use per publication).

    series_part, when set, means this publish was a series continuation:
    the part gets recorded into series_manager's history so the NEXT part
    knows what was already said. When it's None (a normal, one-off weekly
    post) and no series is currently running, the owner gets a "turn this
    into a series?" offer instead.
    """
    import time

    image_bytes = None
    if image_prompt and image_prompt != "empty":
        image_url = generate_image(image_prompt)
        if image_url:
            image_bytes = download_image(image_url)

    def upload() -> str | None:
        if not image_bytes:
            return None
        try:
            return upload_image_to_linkedin(image_bytes)
        except Exception as e:
            logger.error(f"Image upload failed, posting without image: {e}")
            return None

    asset_de = upload()
    published_de = publish_post(post_de, asset_de)
    post_id_de = published_de.get("id")
    logger.info(f"German post published! ID: {post_id_de}")
    send_telegram(
        f"🚀 🇩🇪 Німецький пост про <b>{html_escape(repo)}</b> опубліковано"
        + (" (без картинки)" if not asset_de else "")
        + f". Українська версія — через {POST_GAP_MINUTES} хв."
    )

    post_id_uk = None
    if post_uk:
        logger.info(f"Waiting {POST_GAP_MINUTES} min before the Ukrainian post...")
        time.sleep(POST_GAP_MINUTES * 60)
        asset_uk = upload()
        published_uk = publish_post(post_uk, asset_uk)
        post_id_uk = published_uk.get("id")
        logger.info(f"Ukrainian post published! ID: {post_id_uk}")
        send_telegram(
            f"🚀 🇺🇦 Український пост про <b>{html_escape(repo)}</b> опубліковано."
        )

    save_history_entry(repo, post_id_de, post_id_uk)

    if series_part is not None:
        updated = series_manager.record_part(repo, series_part, post_de, post_uk)
        if updated and updated.get("completed"):
            send_telegram(
                f"🏁 Серію про <b>{html_escape(repo)}</b> завершено "
                f"({series_part}/{series_part} частин)."
            )
        elif updated:
            total = updated.get("total_parts")
            label = f"{updated['part']}/{total}" if total else f"{updated['part']}"
            send_telegram(
                f"🔥 Серія про <b>{html_escape(repo)}</b> триває — "
                f"частина {label} прийде в середу."
            )
    elif series_manager.get_active() is None:
        send_telegram(
            f"Тема «{html_escape(repo)}» зайшла? Можеш перетворити її на серію постів.",
            buttons=[[{"text": "🔥 Зробити серію з цієї теми",
                       "callback_data": f"series_pick:{repo}"}]],
        )


def run_agent():
    logger.info(f"=== Project agent started at {datetime.now()} ===")

    try:
        pending = load_pending_post()

        if pending:
            PENDING_POST_FILE.unlink(missing_ok=True)
            if pending["status"] == "skipped":
                logger.info("Pending draft was skipped by the user — no post this week")
                send_telegram("ℹ️ Тижневий пост пропущено, як ти й просив.")
                return
            logger.info(
                f"Publishing pending draft about '{pending['repo']}' "
                f"(status: {pending['status']})"
            )
            _publish(
                pending["post_de"], pending.get("post_uk", ""),
                pending["repo"], pending.get("image_prompt", ""),
                series_part=pending.get("series_part"),
            )
            return

        # No draft in the queue — generate and publish immediately (old behavior)
        draft = _generate()
        if draft is None:
            send_failure_email("No post this week (no activity, skip, or refusal)")
            send_telegram("ℹ️ Цього тижня поста нема: недостатньо GitHub-активності.")
            return
        _publish(draft["post_de"], draft["post_uk"], draft["repo"], draft["image_prompt"],
                  series_part=draft.get("series_part"))

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Agent crashed: {e}\n{error_details}")
        send_failure_email(f"Agent exception: {e}", error_details)
        send_telegram(f"🔴 Агент впав: {html_escape(str(e))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly LinkedIn project post agent")
    parser.add_argument(
        "--draft", action="store_true",
        help="Generate a draft for Telegram approval instead of publishing",
    )
    args = parser.parse_args()

    if args.draft:
        if generate_draft() is None:
            send_telegram("ℹ️ Чернетки цього тижня нема: недостатньо GitHub-активності.")
    else:
        run_agent()

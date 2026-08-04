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
import smtplib
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from agent_prompts import POST_SYSTEM_PROMPT, IMAGE_PROMPT_SYSTEM
from image_gen import generate_image, download_image
from linkedin_client import publish_post, upload_image_to_linkedin
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
        "post": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "repo", "post", "reason"],
    "additionalProperties": False,
}

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

        active.append({
            "name": name,
            "description": repo.get("description") or "",
            "url": repo["url"],
            "language": (repo.get("primaryLanguage") or {}).get("name"),
            "visibility": (repo.get("visibility") or "unknown").lower(),
            "commits": commits,
            "readme_excerpt": fetch_readme(name),
        })

    logger.info(f"Found {len(active)} active repos this week")
    return active


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


def save_history_entry(repo: str, post_id: str | None) -> None:
    history = load_history()
    history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "repo": repo,
        "post_id": post_id,
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


def load_pending_post() -> dict | None:
    if PENDING_POST_FILE.exists():
        try:
            return json.loads(PENDING_POST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("pending_post.json is corrupted, ignoring it")
    return None


def _generate() -> dict | None:
    """Generate a post + image prompt. Returns {repo, post, reason, image_prompt}."""
    projects = fetch_weekly_activity()
    if not projects:
        logger.info("No GitHub activity this week — nothing to post")
        return None

    result = write_post(projects, load_history())
    if result is None or result["decision"] == "skip":
        reason = result["reason"] if result else "refusal or empty output"
        logger.info(f"No post this week: {reason}")
        return None

    image_prompt = generate_image_prompt(result["post"])
    return {
        "repo": result["repo"],
        "post": result["post"],
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

    message_id = send_telegram(
        f"📝 Чернетка поста про <b>{html_escape(draft['repo'])}</b>\n"
        f"<i>{html_escape(draft['reason'])}</i>\n\n"
        f"{html_escape(draft['post'])}\n\n"
        "✏️ Щоб відредагувати — надішли новий текст відповіддю (reply) "
        "на це повідомлення.",
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


def _publish(post_text: str, repo: str, image_prompt: str) -> None:
    """Render the image, publish the post, record history, confirm in Telegram."""
    asset_urn = None
    if image_prompt and image_prompt != "empty":
        image_url = generate_image(image_prompt)
        if image_url:
            image_bytes = download_image(image_url)
            if image_bytes:
                try:
                    asset_urn = upload_image_to_linkedin(image_bytes)
                except Exception as e:
                    logger.error(f"Image upload failed, posting without image: {e}")

    published = publish_post(post_text, asset_urn)
    post_id = published.get("id")
    save_history_entry(repo, post_id)
    logger.info(f"Published! ID: {post_id}")
    send_telegram(
        f"🚀 Пост про <b>{html_escape(repo)}</b> опубліковано"
        + (" (без картинки)" if not asset_urn else "")
        + "."
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
            _publish(pending["post"], pending["repo"], pending.get("image_prompt", ""))
            return

        # No draft in the queue — generate and publish immediately (old behavior)
        draft = _generate()
        if draft is None:
            send_failure_email("No post this week (no activity, skip, or refusal)")
            send_telegram("ℹ️ Цього тижня поста нема: недостатньо GitHub-активності.")
            return
        _publish(draft["post"], draft["repo"], draft["image_prompt"])

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

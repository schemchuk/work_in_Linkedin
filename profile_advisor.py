"""
AI Profile Advisor for LinkedIn.

Analyzes GitHub repositories and the current LinkedIn profile, then uses
Claude AI to generate concrete suggestions for profile improvements and
post drafts.

Usage:
    1. Set ANTHROPIC_API_KEY in .env
    2. Put your current LinkedIn profile text into profile.md (kept out of git)
    3. Run: python profile_advisor.py
    4. Review generated profile_suggestions.md
"""

import base64
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REPOS_LIMIT = 100
README_MAX_LENGTH = 2500
OUTPUT_FILE = "profile_suggestions.md"
# Current LinkedIn profile text lives outside the code (and outside git)
PROFILE_FILE = Path("profile.md")

# Repositories to always include with full README analysis
PRIORITY_REPOS = {
    "work_in_Linkedin",
    "agtntLinSysadmin",
    "agentLinkedin",
    "networkSecurity",
    "coachHelp",
    "jobPilot",
    "cybersecurity-90days",
    "HomeWorksForLinux",
    "MyObsidian",
}

def load_profile() -> str:
    """Read the current LinkedIn profile text from profile.md."""
    if not PROFILE_FILE.exists():
        raise FileNotFoundError(
            f"{PROFILE_FILE} not found. Create it with your current LinkedIn "
            "profile text (headline, summary, skills, experience, education)."
        )
    return PROFILE_FILE.read_text(encoding="utf-8")


def run_gh_command(args: list[str], timeout: int = 60) -> str:
    """Run a gh CLI command and return stdout."""
    logger.info(f"Running: gh {' '.join(args)}")
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def fetch_repositories(owner: str = "schemchuk") -> list[dict]:
    """Fetch all repositories for the authenticated GitHub user."""
    logger.info(f"Fetching repository list for {owner} via gh CLI...")
    stdout = run_gh_command([
        "repo", "list", owner,
        "--limit", str(REPOS_LIMIT),
        "--json", "name,description,url,visibility,primaryLanguage,pushedAt,isPrivate",
    ])
    repos = json.loads(stdout)
    logger.info(f"Found {len(repos)} repositories")
    return repos


def fetch_readme(owner: str, repo: str) -> str | None:
    """Fetch README content for a repository via gh API."""
    try:
        stdout = run_gh_command(["api", f"repos/{owner}/{repo}/readme"])
        data = json.loads(stdout)
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return content[:README_MAX_LENGTH]
    except subprocess.CalledProcessError:
        logger.warning(f"Could not fetch README for {repo}")
        return None
    except Exception as e:
        logger.warning(f"Error reading README for {repo}: {e}")
        return None


def collect_repo_data(owner: str = "schemchuk") -> list[dict]:
    """Collect repository metadata and README excerpts."""
    repos = fetch_repositories(owner)
    enriched = []

    for repo in repos:
        name = repo["name"]
        is_priority = name in PRIORITY_REPOS

        data = {
            "name": name,
            "description": repo.get("description") or "",
            "url": repo["url"],
            "visibility": repo.get("visibility", "unknown"),
            "language": (repo.get("primaryLanguage") or {}).get("name"),
            "last_pushed": repo.get("pushedAt", ""),
            "is_priority": is_priority,
        }

        if is_priority:
            readme = fetch_readme(owner, name)
            if readme:
                data["readme_excerpt"] = readme

        enriched.append(data)

    # Sort: priority first, then by last pushed date
    enriched.sort(
        key=lambda r: (
            not r["is_priority"],
            r.get("last_pushed", ""),
        ),
        reverse=True,
    )
    return enriched


def build_system_prompt() -> str:
    return """You are an experienced German-Ukrainian IT career coach and LinkedIn profile optimizer.
Your client positions himself as:

"IT-Systemadministrator | AI Integration & IT Automation"

The client's full current LinkedIn profile (name, summary, work history, education) and
GitHub repository data are provided in the user message. Key context: the client recently
retrained into IT in Germany after a long prior career in other fields — a career change
forced by circumstances beyond his control (relocation/integration in Germany) — and has
strong self-learning in Linux, cybersecurity, networking, Git, databases, APIs, and AI automation.

Tone: professional, honest, optimistic, forward-looking. Avoid victim language. Frame the career change as resilience, adaptability, and continuous learning.

Output must be in Markdown with exactly these sections:

## Executive Summary
Brief analysis of the gap between the current profile and the target specialization.

## Proposed Headline
One strong LinkedIn headline (max 120 characters).

## Proposed About / Summary
Full rewritten About section in English (3-5 short paragraphs). It must mention Linux, Git, cybersecurity, databases, APIs, and AI automation. Include the career transition narrative respectfully.

## Proposed Top Skills
A prioritized list of 10-15 skills relevant to "System Administrator + AI Integration & IT Automation".

## Featured Projects
Select 3-5 GitHub repos to feature. For each: repo name, URL, and one-sentence description.

## Experience Section Suggestions
For each past role, suggest a short rewritten bullet that highlights transferable IT skills.

## Post Drafts
Provide 3 post drafts:
- 1 in German (about AI automation / LinkedIn agent project)
- 1 in Ukrainian (about career change and learning IT in Germany)
- 1 in German or Ukrainian (about a cybersecurity/networking topic from his projects)
Each post should be 200-400 words, professional, personal but not oversharing.

## Action Items
Concrete next steps to update the LinkedIn profile.
"""


def build_user_prompt(repos: list[dict], profile: str) -> str:
    repo_json = json.dumps(repos, ensure_ascii=False, indent=2)
    return f"""Target specialization: IT-Systemadministrator | AI Integration & IT Automation

Current LinkedIn profile:
{profile}

GitHub repositories analysis data:
{repo_json}

Please generate the LinkedIn profile optimization suggestions in the requested format.
"""


def call_claude(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")

    client = Anthropic(api_key=api_key)
    logger.info("Sending request to Claude Opus 4.8...")

    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        response = stream.get_final_message()

    return next(block.text for block in response.content if block.type == "text")


def save_suggestions(content: str) -> None:
    Path(OUTPUT_FILE).write_text(content, encoding="utf-8")
    logger.info(f"Saved suggestions to {OUTPUT_FILE}")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY is not set in .env")
        print("   Get it from https://console.anthropic.com/")
        return

    try:
        profile = load_profile()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return

    repos = collect_repo_data()

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(repos, profile)

    suggestions = call_claude(system_prompt, user_prompt)
    save_suggestions(suggestions)

    print(f"\n✅ Done! Review {OUTPUT_FILE}")
    print(f"   Analyzed {len(repos)} repositories")


if __name__ == "__main__":
    main()

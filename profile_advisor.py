"""
AI Profile Advisor for LinkedIn.

Analyzes GitHub repositories and the current LinkedIn profile, then uses
Claude AI to generate concrete suggestions for profile improvements and
post drafts.

Usage:
    1. Set ANTHROPIC_API_KEY in .env
    2. Run: python profile_advisor.py
    3. Review generated profile_suggestions.md
"""

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

CURRENT_PROFILE = """
# Volodymyr Shemchuk
IT-Systemadministrator | AI Integration & IT Automation
Landshut, Bavaria, Germany

## Top Skills
- TypeScript
- Vercel
- WhatsApp API

## Languages
- German (Limited Working)
- English (Elementary)
- Ukrainian (Native or Bilingual)
- Russian (Native or Bilingual)

## Certifications
- Git for distributed software development
- Fundamenta
- Introduction to Cybersecurity
- Networking Devices and Initial Configuration
- Networking Basics

## Summary
I am a dedicated System Administrator and Data Security specialist with a strong foundation built through comprehensive training at Tel-Run school and continuous self-development. My technical expertise spans critical infrastructure management and cybersecurity domains.

Core Technical Skills:
- Linux System Administration — Proficient in Linux fundamentals, system configuration, and server management
- Database Management — MySQL administration, optimization, and security implementation
- Version Control Systems — Advanced Git workflow management for distributed software development
- API Testing & Quality Assurance — Postman expertise for comprehensive API testing and validation
- Cybersecurity & Penetration Testing — Including Metasploit framework and penetration testing methodologies
- Network Security — Data protection protocols and security infrastructure implementation

My diverse background in business management, marketing, and agronomy from Kamianets-Podilsky State University provides me with unique analytical skills and business acumen that enhance my technical approach to system administration. This combination allows me to understand both the technical requirements and business impact of IT infrastructure decisions.

Currently focused on advancing my expertise in system administration and data security through hands-on practice and continuous learning. I bring a methodical approach to problem-solving, strong attention to detail, and the ability to maintain secure, efficient IT environments.

Specializations: Linux Server Administration, Database Security, Network Infrastructure, API Testing, Cybersecurity Assessment

## Experience
Naturfarm — Computer Clerk (October 2021 - September 2022, Kyiv City, Ukraine)
Working as a computer operator, I was involved in personnel accounting and payroll. In addition, he withheld taxes and contributions to various social funds and also created forms for reporting on these items.

Sudwestbahn — Rail defect inspector (February 2010 - October 2021, Khmelnytsky, Ukraine)
Working as a flaw detectorist, I was responsible for the serviceability of tracks, detecting defects, determining their properties and degree of danger, monitoring their condition and development. Made decisions on replacing defective flights and turnouts.

State plant protection inspection — State Plant Protection Inspector (August 2007 - February 2010, Khmelnytsky, Ukraine)
Working as a plant protection inspector, I monitored the implementation of legislation in the field of plant protection by business entities. I kept records of the receipt and expenditure of pesticides and fertilizers in the area entrusted to me.

Studenycya — Agronom (April 1994 - July 2007, Khmelnytsky, Ukraine)
Working as an agronomist, I sought to introduce advanced agricultural technologies in the cultivation of agricultural plants. Made decisions about the start and end times of agricultural work and their rationality. Lots of teamwork. Personal responsibility for the progress and execution of work.

## Education
Tel-Ran.de GmbH (August 2023 - September 2024)
Kamianets-Podilsky State University — Specialist, Business, Management, Marketing
Kamianets-Podilsky State University — Specialist agronomist, Agronomy and Crop Science
"""


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
        import base64
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
Your client is Volodymyr Shemchuk, who positions himself as:

"IT-Systemadministrator | AI Integration & IT Automation"

His background includes:
- Recent retraining at Tel-Ran.de GmbH (2023-2024)
- Previous career in Ukraine (agronomist, state inspector, rail defect inspector, computer clerk)
- Forced career change due to circumstances beyond his control (relocation/integration in Germany)
- Strong self-learning in Linux, cybersecurity, networking, Git, databases, APIs, and AI automation

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


def build_user_prompt(repos: list[dict]) -> str:
    repo_json = json.dumps(repos, ensure_ascii=False, indent=2)
    return f"""Target specialization: IT-Systemadministrator | AI Integration & IT Automation

Current LinkedIn profile:
{CURRENT_PROFILE}

GitHub repositories analysis data:
{repo_json}

Please generate the LinkedIn profile optimization suggestions in the requested format.
"""


def call_claude(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")

    client = Anthropic(api_key=api_key)
    logger.info("Sending request to Claude 3.5 Sonnet...")

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


def save_suggestions(content: str) -> None:
    Path(OUTPUT_FILE).write_text(content, encoding="utf-8")
    logger.info(f"Saved suggestions to {OUTPUT_FILE}")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY is not set in .env")
        print("   Get it from https://console.anthropic.com/")
        return

    repos = collect_repo_data()

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(repos)

    suggestions = call_claude(system_prompt, user_prompt)
    save_suggestions(suggestions)

    print(f"\n✅ Done! Review {OUTPUT_FILE}")
    print(f"   Analyzed {len(repos)} repositories")


if __name__ == "__main__":
    main()

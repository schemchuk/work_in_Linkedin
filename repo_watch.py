"""Track new forks and stargazers on public repos, with real GitHub identities.

Polls cheaply: one bulk gh call gets fork/star COUNTS for every public repo;
the actual fork/stargazer LIST (the real usernames) is only fetched for a
repo whose count just went up, to keep API usage low on an hourly poll.

The first time a given repo is seen, its current forkers/stargazers are
recorded as the baseline WITHOUT emitting events — otherwise every repo's
entire pre-existing fork/star history would fire as "new" on the first run.

State: fork_star_state.json (gitignored) — {repo: {forks, stars,
known_forkers: [...], known_stargazers: [...]}}.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "schemchuk")
STATE_FILE = Path(__file__).parent / "fork_star_state.json"


def _gh(args: list[str], timeout: int = 60) -> str | None:
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=timeout
        )
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("fork_star_state.json is corrupted, ignoring it")
    return {}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_logins(repo: str, kind: str) -> list[str]:
    """kind: 'forks' or 'stargazers'."""
    out = _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/{kind}", "--paginate",
               "--jq", "[.[].owner.login]" if kind == "forks" else "[.[].login]"])
    if not out:
        return []
    # --paginate concatenates one JSON array per page; merge them
    logins: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            logins.extend(json.loads(line))
    return logins


def check_new_forks_stars() -> list[dict]:
    """Poll all public repos; return newly detected {kind, repo, actor} events."""
    out = _gh([
        "repo", "list", GITHUB_OWNER, "--limit", "100",
        "--json", "name,visibility,forkCount,stargazerCount",
        "--jq", '[.[] | select(.visibility=="PUBLIC")]',
    ])
    if not out:
        return []
    repos = json.loads(out)

    state = _load()
    events = []

    for repo in repos:
        name = repo["name"]
        is_new = name not in state
        entry = state.setdefault(name, {
            "forks": 0, "stars": 0, "known_forkers": [], "known_stargazers": [],
        })

        if repo["forkCount"] > entry["forks"]:
            forkers = _fetch_logins(name, "forks")
            if not is_new:
                for actor in forkers:
                    if actor not in entry["known_forkers"]:
                        events.append({"kind": "fork", "repo": name, "actor": actor})
            entry["known_forkers"] = forkers
        entry["forks"] = repo["forkCount"]

        if repo["stargazerCount"] > entry["stars"]:
            stargazers = _fetch_logins(name, "stargazers")
            if not is_new:
                for actor in stargazers:
                    if actor not in entry["known_stargazers"]:
                        events.append({"kind": "star", "repo": name, "actor": actor})
            entry["known_stargazers"] = stargazers
        entry["stars"] = repo["stargazerCount"]

    _save(state)
    if events:
        logger.info(f"New fork/star events: {events}")
    return events

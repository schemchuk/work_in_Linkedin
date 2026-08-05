"""Safety gate deciding whether a repository URL may appear in a public post.

A link is a permanent invitation for strangers (recruiters, bots, scrapers) to
read the repository, so the agent only publishes one when the repo passes every
check below:

    1. visibility is PUBLIC          — a private URL leaks nothing but is useless
                                       and signals sloppiness
    2. README exists                 — the link is a shop window; empty repos hurt
    3. LICENSE exists                — without it the code is "all rights reserved"
    4. no secrets in git history     — gitleaks scans every commit, not just HEAD;
                                       a key deleted in a later commit is still
                                       readable in the history

Results are cached in repo_safety.json keyed by the repo's HEAD commit, so an
unchanged repo is scanned once, not every week.

CLI:
    python repo_safety.py                # audit every public repo of the owner
    python repo_safety.py work_in_Linkedin ...   # audit specific repos
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "schemchuk")
CACHE_FILE = Path(__file__).parent / "repo_safety.json"
GITLEAKS = shutil.which("gitleaks") or str(Path.home() / ".local/bin/gitleaks")
CLONE_TIMEOUT = 300
SCAN_TIMEOUT = 600


def _gh(args: list[str], timeout: int = 60) -> str | None:
    """Run a gh CLI command, returning stdout or None when it fails."""
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=timeout
        )
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("repo_safety.json is corrupted, rebuilding")
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _head_sha(repo: str) -> str | None:
    """Current default-branch commit SHA — the cache key."""
    out = _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/commits?per_page=1", "--jq", ".[0].sha"])
    return out.strip() if out else None


def _has_file(repo: str, kind: str) -> bool:
    """Check for a README or LICENSE via the GitHub API."""
    return _gh(["api", f"repos/{GITHUB_OWNER}/{repo}/{kind}", "--silent"]) is not None


def scan_secrets(repo: str) -> tuple[bool, str]:
    """Scan the repo's entire git history for secrets.

    Returns (clean, detail). A missing gitleaks binary is treated as NOT clean —
    an unverifiable repo must not get a public link.
    """
    if not Path(GITLEAKS).exists():
        return False, "gitleaks не встановлений — перевірити історію неможливо"

    with tempfile.TemporaryDirectory() as tmp:
        clone_path = Path(tmp) / f"{repo}.git"
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--bare",
                 f"git@github.com:{GITHUB_OWNER}/{repo}.git", str(clone_path)],
                capture_output=True, check=True, timeout=CLONE_TIMEOUT,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return False, f"не вдалося клонувати для перевірки: {e}"

        try:
            result = subprocess.run(
                [GITLEAKS, "git", str(clone_path), "--redact", "--no-banner"],
                capture_output=True, text=True, timeout=SCAN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False, "сканування історії перевищило таймаут"

    if result.returncode == 0:
        return True, "історія чиста"
    findings = [
        line.strip() for line in (result.stdout + result.stderr).splitlines()
        if "RuleID:" in line or "File:" in line
    ][:6]
    return False, "знайдено секрети в історії: " + "; ".join(findings or ["деталі в gitleaks"])


def check_repo(repo: str, use_cache: bool = True) -> dict:
    """Run every gate for one repo.

    Returns {"repo", "safe", "reasons": [...], "checked", "head"}.
    """
    head = _head_sha(repo)
    cache = _load_cache()
    cached = cache.get(repo)
    if use_cache and cached and head and cached.get("head") == head:
        return cached

    reasons: list[str] = []

    meta = _gh(["repo", "view", f"{GITHUB_OWNER}/{repo}", "--json", "visibility"])
    visibility = json.loads(meta).get("visibility") if meta else None
    is_public = visibility == "PUBLIC"
    if not is_public:
        reasons.append(f"репозиторій не публічний (visibility={visibility})")

    if not _has_file(repo, "readme"):
        reasons.append("нема README")
    if not _has_file(repo, "license"):
        reasons.append("нема LICENSE")

    # Scanning is the slow part — only worth it for repos that could get a link
    if is_public:
        clean, detail = scan_secrets(repo)
        if not clean:
            reasons.append(detail)

    result = {
        "repo": repo,
        "safe": not reasons,
        "reasons": reasons,
        "checked": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "head": head,
    }
    cache[repo] = result
    _save_cache(cache)

    status = "SAFE" if result["safe"] else "BLOCKED"
    logger.info(f"Link check {repo}: {status}" + (f" ({'; '.join(reasons)})" if reasons else ""))
    return result


def is_link_allowed(repo: str) -> bool:
    """Convenience wrapper: may this repo's URL appear in a public post?"""
    return check_repo(repo)["safe"]


def main() -> None:
    """Audit repos from the command line."""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    repos = sys.argv[1:]
    if not repos:
        out = _gh([
            "repo", "list", GITHUB_OWNER, "--limit", "100",
            "--json", "name,visibility",
            "--jq", '.[] | select(.visibility=="PUBLIC") | .name',
        ])
        repos = out.split() if out else []

    print(f"Перевіряю {len(repos)} репозиторіїв (посилання в постах дозволене лише для SAFE)\n")
    safe, blocked = [], []
    for repo in repos:
        result = check_repo(repo, use_cache=False)
        if result["safe"]:
            safe.append(repo)
            print(f"✅ {repo}")
        else:
            blocked.append(repo)
            print(f"❌ {repo}")
            for reason in result["reasons"]:
                print(f"     {reason}")

    print(f"\nПідсумок: {len(safe)} дозволено, {len(blocked)} заблоковано")
    if blocked:
        print("Заблоковані репозиторії згадуються в постах без посилання.")


if __name__ == "__main__":
    main()

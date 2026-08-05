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


def scan_secrets(repo: str) -> dict:
    """Scan the repo's entire git history for secrets.

    Returns {"clean", "error", "summary"}. `summary` describes findings without
    ever carrying the secret values themselves (gitleaks runs with --redact):
    total count, affected files, rule ids, commit count and date range.

    A missing gitleaks binary or a failed clone yields clean=False with an
    `error` — an unverifiable repo must not get a public link, but it is not
    reported as a leak.
    """
    if not Path(GITLEAKS).exists():
        return {"clean": False, "error": "gitleaks не встановлений — історію не перевірено", "summary": None}

    with tempfile.TemporaryDirectory() as tmp:
        clone_path = Path(tmp) / f"{repo}.git"
        report_path = Path(tmp) / "report.json"
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--bare",
                 f"git@github.com:{GITHUB_OWNER}/{repo}.git", str(clone_path)],
                capture_output=True, check=True, timeout=CLONE_TIMEOUT,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return {"clean": False, "error": f"не вдалося клонувати для перевірки: {e}", "summary": None}

        try:
            result = subprocess.run(
                [GITLEAKS, "git", str(clone_path), "--redact", "--no-banner",
                 "--report-format", "json", "--report-path", str(report_path)],
                capture_output=True, text=True, timeout=SCAN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {"clean": False, "error": "сканування історії перевищило таймаут", "summary": None}

        if result.returncode == 0:
            return {"clean": True, "error": None, "summary": None}

        try:
            findings = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            findings = []

    if not findings:
        # Non-zero exit without a parsable report — treat as unverified, not as a leak
        return {"clean": False, "error": "gitleaks завершився з помилкою", "summary": None}

    files: dict[str, int] = {}
    rules: dict[str, int] = {}
    for f in findings:
        files[f.get("File", "?")] = files.get(f.get("File", "?"), 0) + 1
        rules[f.get("RuleID", "?")] = rules.get(f.get("RuleID", "?"), 0) + 1
    dates = sorted(f.get("Date", "") for f in findings if f.get("Date"))

    return {
        "clean": False,
        "error": None,
        "summary": {
            "total": len(findings),
            "files": dict(sorted(files.items(), key=lambda x: -x[1])[:8]),
            "rules": dict(sorted(rules.items(), key=lambda x: -x[1])[:5]),
            "commits": len({f.get("Commit", "") for f in findings}),
            "first_date": dates[0][:10] if dates else "",
            "last_date": dates[-1][:10] if dates else "",
        },
    }


def check_repo(repo: str, use_cache: bool = True, alert: bool = True) -> dict:
    """Run every gate for one repo.

    Returns {"repo", "safe", "leaks", "leak_summary", "reasons", "checked", "head"}.

    `leaks` is the hard stop: a repo with secrets anywhere in its history never
    gets a link, and the owner is alerted (once per HEAD, unless `alert` is
    False). Missing README/LICENSE also block the link but are cosmetic — no
    alert is raised for them.
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
    scan = {"clean": False, "error": None, "summary": None}
    if is_public:
        scan = scan_secrets(repo)
        if scan["summary"]:
            reasons.append(
                f"ВИТІК: {scan['summary']['total']} знахідок у "
                f"{scan['summary']['commits']} комітах"
            )
        elif scan["error"]:
            reasons.append(scan["error"])

    result = {
        "repo": repo,
        "safe": not reasons,
        "leaks": bool(scan["summary"]),
        "leak_summary": scan["summary"],
        "reasons": reasons,
        "checked": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "head": head,
        # Remember that this exact history was already reported, so the weekly
        # run doesn't repeat the same alert until something actually changes
        "alerted_head": (cached or {}).get("alerted_head"),
    }

    if result["leaks"] and alert and result["alerted_head"] != head:
        if alert_about_leaks([result]):
            result["alerted_head"] = head

    cache[repo] = result
    _save_cache(cache)

    status = "SAFE" if result["safe"] else ("LEAKS" if result["leaks"] else "BLOCKED")
    logger.info(f"Link check {repo}: {status}" + (f" ({'; '.join(reasons)})" if reasons else ""))
    return result


def is_link_allowed(repo: str) -> bool:
    """Convenience wrapper: may this repo's URL appear in a public post?"""
    return check_repo(repo)["safe"]


def format_leak_report(results: list[dict]) -> str:
    """Human-readable leak report (plain text, no secret values)."""
    lines = []
    for r in results:
        s = r["leak_summary"]
        lines.append(f"📛 {r['repo']}: {s['total']} знахідок у {s['commits']} комітах")
        if s["first_date"]:
            lines.append(f"   період: {s['first_date']} — {s['last_date']}")
        lines.append("   файли:")
        for path, count in s["files"].items():
            lines.append(f"     • {path} ({count})")
        lines.append("   типи: " + ", ".join(f"{k} ×{v}" for k, v in s["rules"].items()))
        lines.append(
            f"   репозиторій: github.com/{GITHUB_OWNER}/{r['repo']} "
            "(посилання в постах заблоковане)"
        )
        lines.append("")
    lines.append(
        "Видалити файл наступним комітом НЕ достатньо — секрет лишається "
        "в історії й читається через git log -p. Треба відкликати/перегенерувати "
        "самі облікові дані, а вже потім чистити історію (git filter-repo)."
    )
    lines.append(f"\nПеревірити самому: python repo_safety.py {' '.join(r['repo'] for r in results)}")
    return "\n".join(lines)


def alert_about_leaks(results: list[dict]) -> bool:
    """Notify the owner about repos with secrets in history (Telegram + email).

    Returns True if at least one channel accepted the alert.
    """
    leaky = [r for r in results if r.get("leak_summary")]
    if not leaky:
        return False

    report = format_leak_report(leaky)
    names = ", ".join(r["repo"] for r in leaky)
    logger.warning(f"Secrets found in git history: {names}")

    delivered = False

    try:
        from tg_notify import send_telegram, html_escape
        header = (
            "🔴 <b>Знайдено секрети в історії Git</b>\n"
            f"Репозиторії: <b>{html_escape(names)}</b>\n"
            "Посилання на них у постах заблоковані.\n\n"
        )
        delivered = bool(send_telegram(header + f"<pre>{html_escape(report)}</pre>"))
    except Exception as e:
        logger.error(f"Could not send Telegram leak alert: {e}")

    try:
        from project_agent import send_failure_email
        send_failure_email(f"Секрети в історії Git: {names}", report)
        delivered = True
    except Exception as e:
        logger.error(f"Could not send email leak alert: {e}")

    return delivered


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
    safe, blocked, leaky = [], [], []
    for repo in repos:
        result = check_repo(repo, use_cache=False, alert=False)
        if result["safe"]:
            safe.append(repo)
            print(f"✅ {repo}")
        elif result["leaks"]:
            leaky.append(result)
            print(f"📛 {repo} (ВИТІК СЕКРЕТІВ)")
        else:
            blocked.append(repo)
            print(f"❌ {repo}")
            for reason in result["reasons"]:
                print(f"     {reason}")

    print(f"\nПідсумок: {len(safe)} дозволено, {len(blocked)} заблоковано, {len(leaky)} з витоками секретів")
    if blocked or leaky:
        print("Заблоковані репозиторії згадуються в постах без посилання.")
    if leaky:
        print("\n" + format_leak_report(leaky))
        alert_about_leaks(leaky)


if __name__ == "__main__":
    main()

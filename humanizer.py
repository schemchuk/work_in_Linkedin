"""Humanizer gate: rewrite each post to remove AI tells and verify it before it is
queued for publishing. Fail-closed: a post that does not pass raises HumanizerError."""
import json
import logging
import os
import re

from anthropic import Anthropic

from agent_prompts import HUMANIZE_SYSTEM_PROMPT, AI_CHECK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
MAX_ROUNDS = 3
MAX_CHARS = 1300
MAX_AI_SCORE = 3
LANG_NAMES = {"de": "German", "uk": "Ukrainian"}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "ai_score": {"type": "integer"},
        "passed": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "unsupported_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ai_score", "passed", "issues", "unsupported_facts"],
    "additionalProperties": False,
}

_client: Anthropic | None = None


class HumanizerError(Exception):
    """Post still looks AI-generated or contains unsupported facts after all rounds."""


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _facts_text(facts: dict | None) -> str:
    return json.dumps(facts or {}, ensure_ascii=False)[:6000]


def _ask(system: str, user: str, schema: dict | None = None) -> str:
    kwargs = {}
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
        **kwargs,
    )
    if response.stop_reason != "end_turn":
        raise HumanizerError(f"Claude response ended unexpectedly (stop_reason={response.stop_reason})")
    return next(b.text for b in response.content if b.type == "text").strip()


def rule_check(original: str, text: str, lang: str) -> list[str]:
    """Deterministic checks: language, limits, links and hashtags kept."""
    issues = []
    if len(text) > MAX_CHARS:
        issues.append(f"Post is {len(text)} chars, limit is {MAX_CHARS}. Shorten it.")
    has_cyrillic = bool(re.search(r"[а-яА-ЯіїєґІЇЄҐ]{3}", text))
    if lang == "uk" and not has_cyrillic:
        issues.append("Post must be written in Ukrainian.")
    if lang == "de" and has_cyrillic:
        issues.append("Post must be written in German, it contains Cyrillic text.")
    if lang == "uk" and re.search(r"[ыэъЫЭЪ]", text):
        issues.append("Contains Russian-only letters (ы, э, ъ).")
    if "—" in text or "–" in text:
        issues.append("Contains em/en dashes. Replace with period, comma, colon or parentheses.")
    if "**" in text or re.search(r"^#{1,6}\s", text, re.M):
        issues.append("Contains markdown. Use plain text.")
    # Link safety: the set of URLs must be exactly what finalize_post_links produced
    url_re = r"(?:https?://|www\.)\S+"
    before, after = set(re.findall(url_re, original)), set(re.findall(url_re, text))
    for url in before - after:
        issues.append(f"URL missing: {url}")
    for url in after - before:
        issues.append(f"URL must not be added or changed: {url}")
    marker = re.match(r"\s*(Teil|Частина)\s+\d+", original)
    if marker and marker.group(0).strip() not in text:
        issues.append(f"Series marker missing: {marker.group(0).strip()}")
    hashtags = re.findall(r"(?<!\S)#\w+", text)
    if not 4 <= len(hashtags) <= 6:
        issues.append(f"Needs 4-6 hashtags, found {len(hashtags)}.")
    return issues


def ai_check(text: str, lang: str, facts: dict | None) -> dict:
    """LLM reviewer: AI tells plus unsupported facts."""
    raw = _ask(
        AI_CHECK_SYSTEM_PROMPT,
        f"LANGUAGE: {LANG_NAMES[lang]}\n\nPOST:\n{text}\n\nSOURCE FACTS:\n{_facts_text(facts)}",
        CHECK_SCHEMA,
    )
    try:
        verdict = json.loads(raw)
        score = int(verdict["ai_score"])
    except (ValueError, KeyError, TypeError) as e:
        raise HumanizerError(f"Unparseable reviewer response: {raw[:200]}") from e
    unsupported = [str(i) for i in verdict.get("unsupported_facts", [])]
    return {
        "ai_score": score,
        "passed": bool(verdict.get("passed")) and score <= MAX_AI_SCORE and not unsupported,
        "issues": [str(i) for i in verdict.get("issues", [])],
        "unsupported_facts": unsupported,
    }


def humanize_post(post_text: str, lang: str, facts: dict | None = None) -> str:
    """Rewrite and verify one post. Returns the approved text or raises HumanizerError."""
    if lang not in LANG_NAMES:
        raise ValueError(f"Unsupported language: {lang}")
    current = post_text
    notes: list[str] = []
    last_issues: list[str] = []

    for round_no in range(1, MAX_ROUNDS + 1):
        user = f"LANGUAGE: {LANG_NAMES[lang]}\n\nDRAFT:\n{current}\n\nSOURCE FACTS:\n{_facts_text(facts)}"
        if notes:
            user += "\n\nREVIEW NOTES:\n" + "\n".join(f"- {n}" for n in notes)
        candidate = _ask(HUMANIZE_SYSTEM_PROMPT, user)

        rule_issues = rule_check(post_text, candidate, lang)
        verdict = ai_check(candidate, lang, facts)
        logger.info(
            f"Humanizer [{lang}] round {round_no}: ai_score={verdict['ai_score']}, "
            f"rule_issues={len(rule_issues)}, unsupported={len(verdict['unsupported_facts'])}, "
            f"tells={verdict['issues']}"
        )

        if not rule_issues and verdict["passed"]:
            return candidate

        current = candidate
        notes = rule_issues + verdict["issues"] + [
            f"Unsupported by source facts, remove or soften: {f}"
            for f in verdict["unsupported_facts"]
        ]
        last_issues = notes

    raise HumanizerError(
        f"{LANG_NAMES[lang]} post failed the humanizer check after {MAX_ROUNDS} rounds. "
        "Remaining issues:\n" + "\n".join(f"- {i}" for i in last_issues)
        + f"\n\nLast draft:\n{current}"
    )


def humanize_pair(post_de: str, post_uk: str, facts: dict | None = None) -> tuple[str, str]:
    """Humanize the German and the Ukrainian post (the Ukrainian one may be empty)."""
    post_de = humanize_post(post_de, "de", facts)
    if post_uk:
        post_uk = humanize_post(post_uk, "uk", facts)
    return post_de, post_uk

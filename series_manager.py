"""State for an active LinkedIn post series.

A series lets the weekly agent keep writing about ONE project across several
weeks instead of switching topics every time — for when a post's engagement
signals the topic is worth continuing. Only one series can run at a time,
matching the one-post-per-week publishing slot.

State lives in series_state.json (gitignored): absent/None means no active
series. Each entry in "history" is a previously published part, kept so the
next installment's prompt can avoid repeating itself.
"""

import json
from datetime import datetime
from pathlib import Path

STATE_FILE = Path(__file__).parent / "series_state.json"


def load() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_active() -> dict | None:
    """Return the active series state, or None if no series is running."""
    return load()


def start(repo: str, total_parts: int | None) -> dict:
    """Start a new series about `repo`, overwriting any previous one.

    total_parts=None means unlimited — the series continues every week until
    stopped manually.
    """
    state = {
        "repo": repo,
        "part": 1,
        "total_parts": total_parts,
        "started": datetime.now().strftime("%Y-%m-%d"),
        "history": [],
    }
    save(state)
    return state


def stop() -> dict | None:
    """Stop the active series, if any. Returns the state that was stopped."""
    state = load()
    if state is not None:
        STATE_FILE.unlink(missing_ok=True)
    return state


def record_part(repo: str, part: int, post_de: str, post_uk: str) -> dict | None:
    """Record a just-published part and advance to the next one.

    Called only at publish time (not at draft time), so a discarded/
    regenerated draft never pollutes the continuity history. Returns the
    updated state, or {"completed": True, ...} once total_parts is reached
    (the series file is removed). Returns None if the series was stopped or
    moved on between draft and publish (repo/part no longer match) — in that
    case nothing is changed, since the mismatch means the user already acted.
    """
    state = load()
    if not state or state["repo"] != repo or state["part"] != part:
        return None

    state.setdefault("history", []).append({
        "part": part,
        "post_de": post_de,
        "post_uk": post_uk,
        "date": datetime.now().strftime("%Y-%m-%d"),
    })

    total = state.get("total_parts")
    if total and part >= total:
        STATE_FILE.unlink(missing_ok=True)
        return {"completed": True, **state}

    state["part"] = part + 1
    save(state)
    return state

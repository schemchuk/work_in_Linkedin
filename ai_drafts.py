"""AI drafting for the LinkedIn assistant bot (Claude Sonnet 5).

Two jobs:
    - analyze_notification(): parse a raw LinkedIn notification email and,
      if it's a comment, draft a German reply
    - draft_comment_variants(): write 2-3 German comment variants for
      someone else's post
"""

import json
import logging
import os

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

NOTIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["comment", "reaction", "connection", "mention", "other"],
        },
        "actor": {"type": "string"},
        "comment_text": {"type": "string"},
        "post_url": {"type": "string"},
        "post_hint": {"type": "string"},
        "reply_draft": {"type": "string"},
    },
    "required": ["kind", "actor", "comment_text", "post_url", "post_hint", "reply_draft"],
    "additionalProperties": False,
}

NOTIFICATION_PROMPT = """Ти — асистент LinkedIn-профілю ІТ-фахівця з Німеччини
("IT-Systemadministrator | AI Integration & IT Automation").

Тобі дають сирий текст email-сповіщення від LinkedIn. Розбери його:

- kind: "comment" (хтось прокоментував пост або відповів на коментар),
  "reaction" (лайк/реакція), "connection" (запит/прийняття контакту),
  "mention" (згадка), "other" (все інше: дайджести, вакансії, реклама)
- actor: ім'я людини, яка виконала дію ("" якщо невідомо)
- comment_text: текст коментаря, якщо kind=comment ("" інакше)
- post_url: посилання на пост/активність з листа, якщо є ("" інакше);
  обирай лінк виду linkedin.com/feed/update/... або comm/feed/...
- post_hint: 5-10 слів — про який пост ідеться, якщо видно з листа
- reply_draft: ЯКЩО kind=comment — чернетка відповіді ТІЄЮ Ж МОВОЮ,
  ЩО Й КОМЕНТАР (німецький коментар → німецькою, український →
  українською, англійський → англійською; якщо мову визначити важко —
  німецькою): 1-3 речення, тепло і по суті, подякуй і додай змістовну
  думку або зустрічне запитання; без хештегів, без формальностей типу
  "Sehr geehrte"; тон дзеркаль до тону коментаря (неформальний → "du"/"ти",
  формальний → "Sie"/"ви"). Якщо kind не comment — "".

ВАЖЛИВО для reply_draft: НЕ вигадуй технічних фактів про його проєкти
(які технології він використав, скільки часу щось зайняло, які в нього
плани). Якщо коментар містить конкретне технічне запитання, відповідь
на яке тобі невідома — залиш у чернетці плейсхолдер у квадратних дужках,
наприклад: "Danke! [твоя відповідь: webhooks чи polling?] Und wie ...".
Власник сам замінить плейсхолдер перед відправкою.

Не вигадуй: якщо чогось нема в листі — лиши порожній рядок."""

VARIANTS_SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {"type": "string"},
        },
        "note": {"type": "string"},
    },
    "required": ["variants", "note"],
    "additionalProperties": False,
}

VARIANTS_PROMPT = """Ти — асистент LinkedIn-профілю ІТ-фахівця з Німеччини
("IT-Systemadministrator | AI Integration & IT Automation", нещодавно
перекваліфікувався в ІТ, будує проєкти з automation/AI/Linux).

Тобі дають текст ЧУЖОГО LinkedIn-поста. Напиши 3 варіанти коментаря
від його імені ТІЄЮ Ж МОВОЮ, ЩО Й ПОСТ (німецький пост → німецькою,
український → українською, англійський → англійською):

- варіант 1: підтримка + власний досвід або спостереження (2-3 речення)
- варіант 2: змістовне уточнююче запитання до автора (1-2 речення)
- варіант 3: коротка теза, яка додає нову думку до теми (1-2 речення)

Правила:
- звучати як людина, не як маркетинг; без лестощів типу "Great post!"
- без хештегів і посилань
- можна 1 доречне емодзі, не більше
- якщо пост технічний і близький до його стеку (Linux, автоматизація,
  AI, безпека) — можна легко показати власну практику, без хвастощів
- note: одне речення українською — який варіант радиш і чому"""


def _create(system: str, user_content: str, schema: dict, effort: str = "low") -> dict | None:
    """One structured-output call to Claude; returns parsed JSON or None on refusal."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    if response.stop_reason == "refusal":
        logger.warning("Claude refused the drafting request")
        return None
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def analyze_notification(email_text: str) -> dict | None:
    """Parse a LinkedIn notification email; draft a reply if it's a comment."""
    return _create(NOTIFICATION_PROMPT, email_text[:6000], NOTIFICATION_SCHEMA)


def draft_comment_variants(post_text: str) -> dict | None:
    """Return {'variants': [...], 'note': ...} for someone else's post."""
    return _create(VARIANTS_PROMPT, post_text[:6000], VARIANTS_SCHEMA, effort="medium")


def redraft_reply(comment_text: str, previous_draft: str) -> str | None:
    """Generate an alternative German reply to the same comment."""
    result = _create(
        NOTIFICATION_PROMPT
        + "\n\nДОДАТКОВО: попередня чернетка відповіді користувачу не сподобалась. "
        "Напиши ІНШИЙ варіант відповіді — інший кут, інша структура.",
        f"Коментар під моїм постом:\n{comment_text}\n\n"
        f"Попередня чернетка (не використовуй її формулювання):\n{previous_draft}",
        NOTIFICATION_SCHEMA,
    )
    if result and result.get("reply_draft"):
        return result["reply_draft"]
    return None

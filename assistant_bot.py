"""LinkedIn assistant Telegram bot.

What it does:
    - polls Gmail (IMAP) for LinkedIn notification emails every 10 minutes;
      for each new comment sends the comment + an AI reply draft (German).
      The draft is in a tap-to-copy block; a button opens the post in LinkedIn
    - logs all engagement (comments/reactions/mentions) to engagement_log.json;
      /stats shows a summary
    - manages the weekly post draft queue (pending_post.json): approve /
      regenerate / skip buttons, and editing by replying to the draft message
    - checks LinkedIn token expiry daily and warns 7 days ahead
    - any plain text message you send is treated as SOMEONE ELSE'S post:
      the bot returns 3 German comment variants

Commands: /start /help /stats /token /post /draft

Setup:
    1. Create a bot via @BotFather, put the token in .env as TELEGRAM_BOT_TOKEN
    2. Run the bot, send /start — it prints your chat id
    3. Put the id in .env as TELEGRAM_CHAT_ID and restart
"""

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

import series_manager
from ai_drafts import analyze_notification, draft_comment_variants, redraft_reply
from email_inbox import fetch_new_notifications, load_state, save_state
from tg_notify import html_escape
from token_manager import check_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
PENDING_POST_FILE = BASE_DIR / "pending_post.json"
ENGAGEMENT_LOG = BASE_DIR / "engagement_log.json"

BOT_TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Kyiv"))
INBOX_POLL_MINUTES = int(os.getenv("INBOX_POLL_MINUTES", "10"))
MAX_STORED_NOTIFICATIONS = 50

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# --------------------------------------------------------------------------
# Small state helpers
# --------------------------------------------------------------------------

def _authorized(update: Update) -> bool:
    """Only react to the owner's chat once TELEGRAM_CHAT_ID is configured."""
    if not CHAT_ID:
        return True  # setup phase: allow /start to reveal the chat id
    return str(update.effective_chat.id) == str(CHAT_ID)


def log_engagement(kind: str, actor: str, post_hint: str, post_url: str) -> None:
    entries = []
    if ENGAGEMENT_LOG.exists():
        try:
            entries = json.loads(ENGAGEMENT_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    entries.append({
        "date": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kind": kind,
        "actor": actor,
        "post_hint": post_hint,
        "post_url": post_url,
    })
    ENGAGEMENT_LOG.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_pending_post() -> dict | None:
    if PENDING_POST_FILE.exists():
        try:
            return json.loads(PENDING_POST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return None


def save_pending_post(pending: dict) -> None:
    PENDING_POST_FILE.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def remember_notification(nid: str, data: dict) -> None:
    state = load_state()
    notes = state.setdefault("notifications", {})
    notes[nid] = data
    # Keep the dict bounded
    for old in sorted(notes, key=int)[:-MAX_STORED_NOTIFICATIONS]:
        del notes[old]
    save_state(state)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

async def poll_inbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHAT_ID:
        return
    notifications = await asyncio.to_thread(fetch_new_notifications)
    for note in notifications:
        result = await asyncio.to_thread(analyze_notification, note["text"])
        if not result:
            continue

        kind = result["kind"]
        if kind == "other":
            continue
        log_engagement(kind, result["actor"], result["post_hint"], result["post_url"])

        if kind == "comment":
            nid = str(note["uid"])
            remember_notification(nid, {
                "comment_text": result["comment_text"],
                "reply_draft": result["reply_draft"],
                "actor": result["actor"],
            })
            text = (
                f"💬 <b>{html_escape(result['actor'] or 'Хтось')}</b> "
                f"прокоментував пост"
                + (f" «{html_escape(result['post_hint'])}»" if result["post_hint"] else "")
                + f":\n\n<i>{html_escape(result['comment_text'])}</i>\n\n"
                f"✍️ Чернетка відповіді (тапни, щоб скопіювати):\n"
                f"<code>{html_escape(result['reply_draft'])}</code>"
            )
            rows = []
            if result["post_url"].startswith("http"):
                rows.append([InlineKeyboardButton("🔗 Відкрити пост", url=result["post_url"])])
            rows.append([InlineKeyboardButton("🔁 Інший варіант", callback_data=f"regen:{nid}")])
            await context.bot.send_message(
                CHAT_ID, text, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(rows),
                disable_web_page_preview=True,
            )
        elif kind in ("connection", "mention"):
            emoji = "🤝" if kind == "connection" else "📣"
            await context.bot.send_message(
                CHAT_ID,
                f"{emoji} {html_escape(result['actor'])}: {html_escape(result['post_hint'] or kind)}",
                parse_mode=ParseMode.HTML,
            )
        # reactions are logged silently — see /stats


async def daily_token_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHAT_ID:
        return
    warning = await asyncio.to_thread(check_token)
    if warning:
        await context.bot.send_message(CHAT_ID, warning)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not CHAT_ID:
        await update.message.reply_text(
            f"Привіт! Твій chat id: {chat_id}\n\n"
            f"Додай у .env рядок:\nTELEGRAM_CHAT_ID={chat_id}\n"
            "і перезапусти бота."
        )
        return
    if not _authorized(update):
        return
    await update.message.reply_text("Бот працює. Команди: /help")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Що я вмію:\n\n"
        "💬 Сам повідомляю про нові коментарі під твоїми постами "
        "і даю чернетку відповіді німецькою.\n\n"
        "📝 Надішли мені текст ЧУЖОГО поста — поверну 3 варіанти "
        "коментаря твоїм голосом.\n\n"
        "📅 У середу приходить чернетка тижневого поста: можна схвалити, "
        "перегенерувати, пропустити або відредагувати (відповідай reply'єм "
        "на повідомлення з чернеткою — твій текст замінить пост).\n\n"
        "🔥 Якщо якась тема добре зайшла — постав її на серію: наступні "
        "тижні бот писатиме продовження замість нового проєкту, поки не "
        "закінчаться обрані частини або ти не зупиниш вручну.\n\n"
        "Команди:\n"
        "/post — показати поточну чернетку тижневого поста\n"
        "/draft — згенерувати чернетку зараз (не чекаючи середи)\n"
        "/series — почати/подивитись/зупинити серію постів\n"
        "/stats — статистика реакцій і коментарів\n"
        "/token — стан LinkedIn-токена"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ENGAGEMENT_LOG.exists():
        await update.message.reply_text("Поки нема даних — лог порожній.")
        return
    entries = json.loads(ENGAGEMENT_LOG.read_text(encoding="utf-8"))
    cutoff = dt.datetime.now() - dt.timedelta(days=30)
    recent = [
        e for e in entries
        if dt.datetime.strptime(e["date"], "%Y-%m-%d %H:%M") >= cutoff
    ]
    by_kind: dict[str, int] = {}
    by_post: dict[str, int] = {}
    for e in recent:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        if e["post_hint"]:
            by_post[e["post_hint"]] = by_post.get(e["post_hint"], 0) + 1

    kind_names = {"comment": "💬 коментарі", "reaction": "👍 реакції",
                  "mention": "📣 згадки", "connection": "🤝 контакти"}
    lines = [f"📊 За останні 30 днів ({len(recent)} подій):\n"]
    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        lines.append(f"{kind_names.get(kind, kind)}: {count}")
    if by_post:
        lines.append("\nТоп постів за активністю:")
        for hint, count in sorted(by_post.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {count} × {hint}")
    await update.message.reply_text("\n".join(lines))


async def cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    from token_manager import days_until_expiry
    warning = await asyncio.to_thread(check_token)
    if warning:
        await update.message.reply_text(warning)
    else:
        days = days_until_expiry()
        await update.message.reply_text(f"🟢 Токен LinkedIn у нормі, лишилось {days} дн.")


def _pending_post_text(pending: dict) -> str:
    status_names = {"pending": "⏳ чекає рішення", "approved": "✅ схвалено",
                    "skipped": "❌ пропущено"}
    series_label = ""
    if pending.get("series_part"):
        total = pending.get("series_total")
        series_label = (
            f"🔥 Серія — частина {pending['series_part']}"
            + (f"/{total}" if total else " (без ліміту)") + "\n"
        )
    return (
        f"{series_label}📝 Чернетка поста про <b>{html_escape(pending['repo'])}</b> "
        f"({status_names.get(pending['status'], pending['status'])})\n"
        f"<i>{html_escape(pending.get('reason', ''))}</i>\n\n"
        f"🇩🇪 <b>Німецька версія:</b>\n{html_escape(pending['post_de'])}\n\n"
        f"🇺🇦 <b>Українська версія:</b>\n{html_escape(pending.get('post_uk', ''))}\n\n"
        "✏️ Щоб відредагувати — надішли новий текст відповіддю (reply) на це "
        "повідомлення: текст кирилицею замінить українську версію, "
        "латиницею — німецьку."
    )


def _pending_post_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Схвалити", callback_data="post_approve"),
        InlineKeyboardButton("🔁 Перегенерувати", callback_data="post_regen"),
        InlineKeyboardButton("❌ Пропустити", callback_data="post_skip"),
    ]])


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    pending = load_pending_post()
    if not pending:
        await update.message.reply_text(
            "Чернетки нема. Вона з'являється щосереди о 18:00, "
            "або згенеруй зараз: /draft"
        )
        return
    msg = await update.message.reply_text(
        _pending_post_text(pending), parse_mode=ParseMode.HTML,
        reply_markup=_pending_post_buttons(),
    )
    pending["tg_message_id"] = msg.message_id
    save_pending_post(pending)


async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text("Генерую чернетку, це займе до хвилини…")
    ok = await asyncio.to_thread(_generate_draft)
    if not ok:
        await update.message.reply_text(
            "Не вийшло: нема GitHub-активності за тиждень або Claude "
            "вирішив пропустити. Деталі в логах."
        )
        return
    await _send_pending_draft(context)


def _generate_draft() -> bool:
    from project_agent import generate_draft
    return generate_draft() is not None


async def _send_pending_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = load_pending_post()
    if not pending:
        return
    msg = await context.bot.send_message(
        CHAT_ID, _pending_post_text(pending), parse_mode=ParseMode.HTML,
        reply_markup=_pending_post_buttons(),
    )
    pending["tg_message_id"] = msg.message_id
    save_pending_post(pending)


def _series_count_buttons(repo: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(str(n), callback_data=f"series_count:{repo}:{n}")
        for n in (2, 3, 4, 5)
    ] + [InlineKeyboardButton("♾️", callback_data=f"series_count:{repo}:0")]])


def _recent_series_candidates(limit: int = 6) -> list[str]:
    """Distinct repos from the most recent posts, newest first — series proposals."""
    history_file = BASE_DIR / "posted_history.json"
    if not history_file.exists():
        return []
    try:
        entries = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    seen, repos = set(), []
    for e in reversed(entries):
        if e["repo"] not in seen:
            seen.add(e["repo"])
            repos.append(e["repo"])
        if len(repos) >= limit:
            break
    return repos


async def cmd_series(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    active = series_manager.get_active()
    if active:
        total = active.get("total_parts")
        done = active["part"] - 1
        progress = f"{done}/{total}" if total else f"{done} опубліковано (без ліміту)"
        await update.message.reply_text(
            f"🔥 Активна серія: <b>{html_escape(active['repo'])}</b>\n"
            f"Прогрес: {progress}\n"
            f"Наступна частина (№{active['part']}) прийде в середу.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⏹ Зупинити серію", callback_data="series_stop")]]
            ),
        )
        return

    candidates = _recent_series_candidates()
    if not candidates:
        await update.message.reply_text(
            "Активної серії нема, і нема з чого запропонувати — спершу "
            "має вийти хоч один тижневий пост."
        )
        return
    await update.message.reply_text(
        "Активної серії нема. Про який проєкт продовжити писати?",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"🔥 {repo}", callback_data=f"series_pick:{repo}")]
             for repo in candidates]
        ),
    )


# --------------------------------------------------------------------------
# Callbacks and free-text messages
# --------------------------------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    query = update.callback_query
    data = query.data

    if data.startswith("series_pick:"):
        await query.answer()
        repo = data.split(":", 1)[1]
        if series_manager.get_active():
            await query.message.reply_text(
                "Уже є активна серія — спершу зупини її через /series."
            )
            return
        await query.message.reply_text(
            f"Скільки частин серії про <b>{html_escape(repo)}</b>? "
            "(♾️ — без ліміту, зупиниш вручну командою /series)",
            parse_mode=ParseMode.HTML,
            reply_markup=_series_count_buttons(repo),
        )
        return

    if data.startswith("series_count:"):
        await query.answer("Починаю серію…")
        _, repo, n = data.split(":", 2)
        total = int(n) or None
        series_manager.start(repo, total)
        label = f"{total} частин" if total else "без ліміту (зупиниш вручну через /series)"
        await query.message.reply_text(
            f"🔥 Серію про <b>{html_escape(repo)}</b> розпочато ({label}). "
            "Наступна частина прийде в середу замість звичайного вибору проєкту.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "series_stop":
        await query.answer("Зупинено")
        stopped = series_manager.stop()
        if stopped:
            await query.message.reply_text(
                f"⏹ Серію про <b>{html_escape(stopped['repo'])}</b> зупинено "
                f"(опубліковано частин: {len(stopped.get('history', []))}).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.message.reply_text("Активної серії й так не було.")
        return

    if data.startswith("regen:"):
        await query.answer("Пишу інший варіант…")
        nid = data.split(":", 1)[1]
        note = load_state().get("notifications", {}).get(nid)
        if not note:
            await query.message.reply_text("Не знайшов контекст цього коментаря.")
            return
        new_draft = await asyncio.to_thread(
            redraft_reply, note["comment_text"], note["reply_draft"]
        )
        if not new_draft:
            await query.message.reply_text("Не вдалося перегенерувати, спробуй ще раз.")
            return
        remember_notification(nid, {**note, "reply_draft": new_draft})
        await query.message.reply_text(
            f"✍️ Інший варіант відповіді для <b>{html_escape(note['actor'])}</b>:\n"
            f"<code>{html_escape(new_draft)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    pending = load_pending_post()
    if not pending:
        await query.answer("Чернетки вже нема")
        return

    if data == "post_approve":
        pending["status"] = "approved"
        save_pending_post(pending)
        await query.answer("Схвалено")
        await query.message.reply_text("✅ Схвалено. Опублікується у п'ятницю о 18:00.")
    elif data == "post_skip":
        pending["status"] = "skipped"
        save_pending_post(pending)
        await query.answer("Пропущено")
        await query.message.reply_text("❌ Цього тижня поста не буде.")
    elif data == "post_regen":
        await query.answer("Генерую наново…")
        ok = await asyncio.to_thread(_generate_draft)
        if ok:
            await _send_pending_draft(context)
        else:
            await query.message.reply_text("Перегенерація не вдалася, деталі в логах.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    text = update.message.text.strip()

    # Reply to the draft message = edit the pending post.
    # Cyrillic text replaces the Ukrainian version, Latin text the German one.
    pending = load_pending_post()
    reply_to = update.message.reply_to_message
    if pending and reply_to and reply_to.message_id == pending.get("tg_message_id"):
        is_cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ") > len(text) * 0.3
        lang_key, lang_name = ("post_uk", "українську") if is_cyrillic else ("post_de", "німецьку")
        pending[lang_key] = text
        pending["status"] = "approved"
        save_pending_post(pending)
        await update.message.reply_text(
            f"✏️ Замінено {lang_name} версію поста, чернетку схвалено. "
            "Опублікується у п'ятницю о 18:00."
        )
        return

    # Otherwise: it's someone else's post — draft comment variants
    if len(text) < 30:
        await update.message.reply_text(
            "Надішли текст чужого LinkedIn-поста (від ~30 символів) — "
            "запропоную варіанти коментаря. Або /help."
        )
        return

    await update.message.reply_text("Думаю над коментарями…")
    result = await asyncio.to_thread(draft_comment_variants, text)
    if not result or not result.get("variants"):
        await update.message.reply_text("Не вдалося згенерувати, спробуй ще раз.")
        return

    parts = ["💡 Варіанти коментаря (тапни, щоб скопіювати):"]
    for i, variant in enumerate(result["variants"], 1):
        parts.append(f"\n{i}. <code>{html_escape(variant)}</code>")
    if result.get("note"):
        parts.append(f"\n\n{html_escape(result['note'])}")
    await update.message.reply_text(
        "\n".join(parts), parse_mode=ParseMode.HTML
    )


# --------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN не заданий у .env")
        print("   Створи бота через @BotFather і додай токен.")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("series", cmd_series))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.job_queue.run_repeating(
        poll_inbox, interval=INBOX_POLL_MINUTES * 60, first=20
    )
    app.job_queue.run_daily(
        daily_token_check, time=dt.time(hour=9, minute=30, tzinfo=BOT_TZ)
    )

    logger.info("Assistant bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

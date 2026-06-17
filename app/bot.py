from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

from telegram import InputMediaPhoto, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.image_meta import build_ai_image, get_exif_datetime
from app.openai_client import AIAnalysisError, validate_checkpoint_photo
from app.settings import load_settings
from app.shift_config import load_shift_plan
from app.storage import build_photo_path, build_report_path, save_report
from app.timezone import get_timezone


logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MODE_MAP = {
    "Відкриття зміни": "opening",
    "Фото протягом дня": "day",
    "Закриття зміни": "closing",
}

KEYBOARD = ReplyKeyboardMarkup(
    [["Відкриття зміни", "Закриття зміни"], ["Фото протягом дня"]],
    resize_keyboard=True,
)

STATUS_ICONS = {
    "Добре": "🟢",
    "Увага": "🟡",
    "Критично": "🔴",
}

NO_PROBLEM_MARKERS = {
    "",
    "явних проблем не видно",
    "явних проблем не видно.",
    "проблем не видно",
    "проблем не виявлено",
}


def _help_text() -> str:
    return (
        "Я працюю як фото-чекліст зміни.\n\n"
        "1. Оберіть режим кнопкою.\n"
        "2. Я надішлю список обов'язкових фото.\n"
        "3. Надсилайте фото по черзі.\n"
        "4. Я перевірю, чи це правильна зона і чи фото схоже на свіже.\n"
        "5. Після всіх фото відправлю підсумок у службовий чат.\n\n"
        "Команди:\n"
        "/start - почати\n"
        "/help - підказка\n"
        "/status - поточний режим і прогрес\n"
        "/cancel - скасувати поточну перевірку"
    )


def _get_mode_config(application: Application, mode_code: str) -> dict:
    return application.bot_data["shift_plan"][mode_code]


def _build_steps_text(mode_config: dict) -> str:
    lines = [f"{index}. {step['title']}" for index, step in enumerate(mode_config["steps"], start=1)]
    return "\n".join(lines)


def _get_current_step(chat_data: dict, context: ContextTypes.DEFAULT_TYPE) -> dict:
    mode_code = chat_data["session"]["mode_code"]
    step_index = chat_data["session"]["step_index"]
    mode_config = _get_mode_config(context.application, mode_code)
    return mode_config["steps"][step_index]


async def _start_mode_session(application: Application, chat_id: int, mode_code: str) -> None:
    mode_config = _get_mode_config(application, mode_code)
    application.chat_data[chat_id]["session"] = {
        "mode_code": mode_code,
        "step_index": 0,
        "results": [],
        "started_at": datetime.now(get_timezone(application.bot_data["settings"].timezone)).isoformat(),
    }

    checklist_text = _build_steps_text(mode_config)
    first_step = mode_config["steps"][0]
    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{mode_config['label']}\n\n"
            f"Потрібно надіслати фото по черзі:\n{checklist_text}\n\n"
            f"Зараз чекаю фото: {first_step['title']}"
        ),
        reply_markup=KEYBOARD,
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Оберіть режим перевірки.", reply_markup=KEYBOARD)
    await update.message.reply_text(_help_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_help_text(), reply_markup=KEYBOARD)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.chat_data.get("session")
    if not session:
        await update.message.reply_text("Активної перевірки зараз немає.")
        return
    mode_config = _get_mode_config(context.application, session["mode_code"])
    current_step = mode_config["steps"][session["step_index"]]
    done_count = len(session["results"])
    total_count = len(mode_config["steps"])
    await update.message.reply_text(
        f"Зараз режим: {mode_config['label']}\n"
        f"Прогрес: {done_count}/{total_count}\n"
        f"Чекаю фото: {current_step['title']}"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.chat_data.pop("session", None)
    if not session:
        await update.message.reply_text("Активної перевірки зараз немає.", reply_markup=KEYBOARD)
        return

    mode_config = _get_mode_config(context.application, session["mode_code"])
    await update.message.reply_text(
        f"Перевірку скасовано: {mode_config['label']}. Можна обрати новий режим.",
        reply_markup=KEYBOARD,
    )


async def select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    mode_code = MODE_MAP.get(text)
    if not mode_code:
        await update.message.reply_text(
            "Не впізнав команду. Оберіть режим кнопкою або напишіть /help.",
            reply_markup=KEYBOARD,
        )
        return

    if context.chat_data.get("session"):
        await update.message.reply_text(
            "Починаю нову перевірку і замінюю попередню незавершену сесію."
        )
    await _start_mode_session(context.application, update.effective_chat.id, mode_code)
    if mode_code in {"opening", "day"}:
        context.application.bot_data["active_day_targets"].add(update.effective_chat.id)


def _format_photo_result(step: dict, result: dict) -> str:
    return (
        f"{step['title']}\n"
        f"Статус: {result.get('status', 'Увага')}\n"
        f"Проблема: {result.get('problem', 'Явних проблем не видно.')}\n"
        f"Дія: {result.get('action', 'Продовжувати по чеклісту.')}"
    )


def _format_summary(mode_label: str, results: list[dict]) -> str:
    lines = [f"{mode_label} завершено"]
    for item in results:
        lines.append(f"- {item['title']}: {item['status']} | {item['problem']}")
    return "\n".join(lines)


def _status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, "⚪")


def _has_actionable_issue(item: dict) -> bool:
    problem = item.get("problem", "").strip().lower()
    return item.get("status") != "Добре" or problem not in NO_PROBLEM_MARKERS


def _format_manager_report(
    *,
    mode_label: str,
    user_full_name: str,
    results: list[dict],
) -> str:
    issues = [item for item in results if _has_actionable_issue(item)]
    good_count = sum(1 for item in results if item.get("status") == "Добре")
    attention_count = sum(1 for item in results if item.get("status") == "Увага")
    critical_count = sum(1 for item in results if item.get("status") == "Критично")

    lines = [
        f"📸 {mode_label}",
        f"👤 {user_full_name}",
        f"🟢 {good_count}  🟡 {attention_count}  🔴 {critical_count}",
        "",
    ]

    if not issues:
        lines.append("✅ Зауважень немає. Усі фото прийняті.")
        return "\n".join(lines)

    lines.append("⚠️ Зауваження:")
    for item in issues:
        lines.append(
            f"{_status_icon(item.get('status', ''))} {item['title']}: {item['problem']}"
        )
        action = item.get("action", "").strip()
        if action and action != "Продовжувати по чеклісту.":
            lines.append(f"   ↳ {action}")
    return "\n".join(lines)


def _format_detailed_report(
    *,
    mode_config: dict,
    user_full_name: str,
    user_id: int,
    started_at: str,
    finished_at: datetime,
    results: list[dict],
) -> str:
    lines = [
        "Звіт зміни",
        f"Тип: {mode_config['label']}",
        f"Від: {user_full_name}",
        f"User ID: {user_id}",
        f"Початок: {started_at}",
        f"Завершено: {finished_at.isoformat()}",
        f"Фото прийнято: {len(results)}/{len(mode_config['steps'])}",
        "",
        "Підсумок:",
    ]
    for index, item in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. {item['title']}",
                f"   Статус: {item['status']}",
                f"   Добре: {item['good'] or 'Без окремого коментаря.'}",
                f"   Проблема: {item['problem']}",
                f"   Дія: {item['action']}",
                f"   Свіжість: {item['freshness']}",
                f"   Файл: {item['photo_path']}",
            ]
        )
    return "\n".join(lines)


def _build_random_day_marks(application: Application, today: str) -> list[str]:
    day_config = application.bot_data["shift_plan"]["day"].get("random_reminders", {})
    if not day_config.get("enabled"):
        return []

    count = int(day_config.get("count", 3))
    window_start = day_config.get("window_start", "13:00")
    window_end = day_config.get("window_end", "19:00")

    start_hour, start_minute = [int(part) for part in window_start.split(":")]
    end_hour, end_minute = [int(part) for part in window_end.split(":")]
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total <= start_total:
        return []

    total_slots = list(range(start_total, end_total + 1))
    sample_count = min(count, len(total_slots))
    rng = random.Random(today)
    chosen = sorted(rng.sample(total_slots, sample_count))
    return [f"{today}:{minute // 60:02d}:{minute % 60:02d}" for minute in chosen]


async def _send_report_album(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    report_text: str,
    results: list[dict],
) -> None:
    photo_results = [item for item in results if item.get("photo_path")]
    if not photo_results:
        await context.bot.send_message(chat_id=chat_id, text=report_text[:4000])
        return

    media_items = []
    file_handles = []
    try:
        caption_in_album = len(report_text) <= 1024
        for index, item in enumerate(photo_results[:10]):
            caption = None
            if index == 0 and caption_in_album:
                caption = report_text
            file_handle = Path(item["photo_path"]).open("rb")
            file_handles.append(file_handle)
            media_items.append(
                InputMediaPhoto(
                    media=file_handle,
                    caption=caption,
                )
            )
        await context.bot.send_media_group(chat_id=chat_id, media=media_items)
        if not caption_in_album:
            await context.bot.send_message(chat_id=chat_id, text=report_text[:4000])
    finally:
        for file_handle in file_handles:
            file_handle.close()


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.chat_data.get("session")
    if not session:
        await update.message.reply_text("Спочатку оберіть режим кнопкою.", reply_markup=KEYBOARD)
        return

    message = update.message
    user = update.effective_user
    mode_config = _get_mode_config(context.application, session["mode_code"])
    step = _get_current_step(context.chat_data, context)

    await message.reply_text(f"Отримав фото для пункту: {step['title']}. Перевіряю.")

    photo = message.photo[-1]
    file = await photo.get_file()
    photo_path = build_photo_path(
        user_id=user.id,
        inspection_code=session["mode_code"],
        step_code=step["code"],
        original_name=f"{photo.file_unique_id}.jpg",
    )
    await file.download_to_drive(custom_path=str(photo_path))
    ai_photo_path = build_ai_image(photo_path)

    settings = context.application.bot_data["settings"]
    try:
        result = validate_checkpoint_photo(
            settings=settings,
            image_path=ai_photo_path,
            mode_label=mode_config["label"],
            checkpoint=step,
            received_at=message.date.astimezone(get_timezone(settings.timezone)),
            exif_datetime=get_exif_datetime(photo_path),
        )
    except AIAnalysisError as exc:
        await message.reply_text(str(exc))
        return

    if not result.get("matches_expected", False):
        await message.reply_text(
            f"Це не схоже на потрібну зону: {step['title']}.\nПричина: {result.get('expected_reason', 'Зона не підтверджена.')}\nНадішліть інше фото."
        )
        return

    if result.get("freshness") == "stale" or result.get("needs_retake"):
        await message.reply_text(
            f"Фото для пункту {step['title']} виглядає несвіжим або непридатним.\nПричина: {result.get('freshness_reason', 'Потрібне нове фото.')}\nБудь ласка, зробіть нове фото зараз."
        )
        return

    session["results"].append(
        {
            "title": step["title"],
            "status": result.get("status", "Увага"),
            "good": result.get("good", ""),
            "problem": result.get("problem", "Явних проблем не видно."),
            "action": result.get("action", "Продовжувати по чеклісту."),
            "freshness": result.get("freshness", "unclear"),
            "photo_path": str(photo_path),
        }
    )

    await message.reply_text(_format_photo_result(step, result))

    session["step_index"] += 1
    if session["step_index"] >= len(mode_config["steps"]):
        finished_at = datetime.now(get_timezone(settings.timezone))
        manager_report = _format_manager_report(
            mode_label=mode_config["label"],
            user_full_name=user.full_name,
            results=session["results"],
        )
        detailed_report = _format_detailed_report(
            mode_config=mode_config,
            user_full_name=user.full_name,
            user_id=user.id,
            started_at=session["started_at"],
            finished_at=finished_at,
            results=session["results"],
        )
        report_path = build_report_path(user.id, session["mode_code"])
        save_report(report_path, detailed_report)

        await message.reply_text(f"{mode_config['label']} завершено.")
        report_chat_id = settings.telegram_report_chat_id
        if report_chat_id:
            await _send_report_album(
                context=context,
                chat_id=report_chat_id,
                report_text=manager_report,
                results=session["results"],
            )
        context.chat_data.pop("session", None)
        return

    next_step = _get_current_step(context.chat_data, context)
    await message.reply_text(f"Фото прийнято. Наступний пункт: {next_step['title']}")


async def reminder_loop(application: Application) -> None:
    settings = application.bot_data["settings"]
    timezone = get_timezone(settings.timezone)
    sent_marks: set[str] = set()
    planned_marks: dict[str, list[str]] = {}
    while True:
        now = datetime.now(timezone)
        today = now.strftime("%Y-%m-%d")
        if today not in planned_marks:
            planned_marks = {today: _build_random_day_marks(application, today)}
        for mark in planned_marks.get(today, []):
            if mark in sent_marks:
                continue
            _, hour_text, minute_text = mark.split(":")
            if now.strftime("%H:%M") >= f"{hour_text}:{minute_text}":
                sent_marks.add(mark)
                for chat_id in list(application.bot_data["active_day_targets"]):
                    if application.chat_data[chat_id].get("session"):
                        continue
                    await _start_mode_session(application, chat_id, "day")
        sent_marks = {item for item in sent_marks if item.startswith(today)}
        await asyncio.sleep(30)


async def post_init(application: Application) -> None:
    application.bot_data["reminder_task"] = asyncio.create_task(reminder_loop(application))


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.get("reminder_task")
    if task:
        task.cancel()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Сталася помилка під час обробки. Спробуйте ще раз."
        )


def main() -> None:
    settings = load_settings()

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["shift_plan"] = load_shift_plan()
    application.bot_data["active_day_targets"] = set()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, select_mode))
    application.add_error_handler(error_handler)
    application.run_polling()

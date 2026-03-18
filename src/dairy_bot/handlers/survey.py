import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dairy_bot.config import Settings
from dairy_bot.services.git_sync import GitService, GitSyncError
from dairy_bot.services.language_store import get_language
from dairy_bot.services.sheets_service import SheetsService
from dairy_bot.services.storage import (
    count_deep_answers_for_day,
    get_survey_data,
    is_evening_survey_filled,
    is_morning_survey_filled,
    save_survey_data,
)
from dairy_bot.services.toc_service import reconcile_toc
from dairy_bot.services.weather_service import (
    get_city_weather,
    get_vienna_weather,
)
from dairy_bot.texts import messages

router = Router()
logger = logging.getLogger(__name__)
_survey_lock: asyncio.Lock | None = None

# Habit keys for evening survey
HABITS = [
    ("steps_8k", "8000+ шагов"),
    ("zero_spending", "0 евро трат"),
    ("english_words", "Занятия английским"),
    ("supplements", "БАДы"),
    ("tea_time", "Чаепитие"),
    ("no_junk_food", "Без сладкого/фаст-фуда"),
    ("no_eating_out", "Без еды вне дома"),
]


class EveningSurveyStates(StatesGroup):
    mood = State()
    energy = State()
    anxiety = State()
    focus = State()
    cravings = State()
    sport = State()
    habits = State()


class MorningSurveyStates(StatesGroup):
    location = State()
    location_city = State()
    mood = State()
    sleep_duration = State()
    sleep_score = State()
    bedtime = State()
    wake_time = State()
    reading = State()


# Callback prefixes
EVENING_START = "evening_start"
EVENING_REFILL = "evening_refill"
MORNING_START = "morning_start"
MORNING_REFILL = "morning_refill"
SKIP_CALLBACK = "skip"

# Evening survey callbacks
MOOD_EVENING_PREFIX = "mood_e_"
ENERGY_PREFIX = "energy_"
ANXIETY_PREFIX = "anxiety_"
FOCUS_PREFIX = "focus_"
CRAVINGS_PREFIX = "cravings_"
SPORT_PREFIX = "sport_"
HABIT_PREFIX = "habit_"
HABITS_DONE = "habits_done"

# Morning survey callbacks
MOOD_MORNING_PREFIX = "mood_m_"
READING_PREFIX = "reading_"
LOCATION_VIENNA_PREFIX = "loc_vienna_"


def _get_survey_lock() -> asyncio.Lock:
    global _survey_lock
    if _survey_lock is None:
        _survey_lock = asyncio.Lock()
    return _survey_lock


def _user_lang(user_id: int | None) -> str:
    return get_language(user_id or 0)


async def _safe_respond(action: str, op) -> None:
    """Send a Telegram response but don't crash on transient network errors."""
    try:
        await op()
    except TelegramNetworkError:
        logger.warning("Telegram request failed during %s", action, exc_info=True)
    except Exception:
        logger.exception("Unexpected error during %s", action)


async def _save_with_sync(
    journal_dir,
    updates: dict[str, Any],
    settings: Settings,
    git_service: GitService,
    sheets_service: SheetsService | None = None,
    moment: datetime | None = None,
) -> str:
    """Save survey data with git sync and optional Google Sheets sync."""
    async with _get_survey_lock():
        try:
            await asyncio.to_thread(git_service.prepare_for_write)
        except GitSyncError:
            logger.warning("Git sync blocked survey write", exc_info=True)
            return "blocked"
        note_path = await save_survey_data(
            journal_dir, updates, moment=moment, timezone=settings.timezone
        )
        toc_paths = await reconcile_toc(
            journal_dir, settings, target_paths=[note_path]
        )
        try:
            result = await asyncio.to_thread(
                git_service.commit_and_push, [note_path] + toc_paths
            )
        except GitSyncError:
            logger.warning("Git push failed after survey write", exc_info=True)
            result = None

        if sheets_service and sheets_service.enabled:
            full_data = await get_survey_data(
                journal_dir, moment=moment, timezone=settings.timezone
            )
            deep_answers_count = await count_deep_answers_for_day(
                journal_dir, moment=moment, timezone=settings.timezone
            )
            await asyncio.to_thread(
                sheets_service.sync_survey_data,
                full_data,
                moment,
                deep_answers_count,
            )

        if result is None:
            return "local_only"
        return "synced" if result.pushed else "local_only"


async def _sync_for_read(git_service: GitService, action: str) -> None:
    try:
        await asyncio.to_thread(git_service.sync_from_remote, allow_dirty=True)
    except GitSyncError:
        logger.warning("Git sync failed before %s; using local view", action, exc_info=True)


def _save_status_key(save_state: str) -> str:
    if save_state == "blocked":
        return "repo_sync_blocked"
    if save_state == "synced":
        return "survey_saved_synced"
    return "survey_saved_local"


def _combine_save_states(*states: str) -> str:
    if any(state == "blocked" for state in states):
        return "blocked"
    if all(state == "synced" for state in states):
        return "synced"
    return "local_only"


def build_rating_keyboard(prefix: str, min_val: int, max_val: int, lang: str) -> InlineKeyboardBuilder:
    """Build inline keyboard for rating questions (1-5 or 0-3)."""
    kb = InlineKeyboardBuilder()
    for i in range(min_val, max_val + 1):
        kb.button(text=str(i), callback_data=f"{prefix}{i}")
    kb.button(text=messages.t("btn_skip", lang), callback_data=SKIP_CALLBACK)
    kb.adjust(max_val - min_val + 1, 1)
    return kb


def build_boolean_keyboard(prefix: str, lang: str) -> InlineKeyboardBuilder:
    """Build inline keyboard for boolean questions."""
    kb = InlineKeyboardBuilder()
    kb.button(text=messages.t("btn_yes", lang), callback_data=f"{prefix}yes")
    kb.button(text=messages.t("btn_no", lang), callback_data=f"{prefix}no")
    kb.button(text=messages.t("btn_skip", lang), callback_data=SKIP_CALLBACK)
    kb.adjust(2, 1)
    return kb


def build_habits_keyboard(selected: dict[str, bool], lang: str) -> InlineKeyboardBuilder:
    """Build inline keyboard for habits with checkboxes."""
    kb = InlineKeyboardBuilder()
    for key, label in HABITS:
        check = "✅ " if selected.get(key) else ""
        kb.button(text=f"{check}{label}", callback_data=f"{HABIT_PREFIX}{key}")
    kb.button(text=messages.t("btn_done", lang), callback_data=HABITS_DONE)
    kb.button(text=messages.t("btn_skip", lang), callback_data=SKIP_CALLBACK)
    kb.adjust(1)
    return kb


def build_start_keyboard(callback_data: str, lang: str) -> InlineKeyboardBuilder:
    """Build keyboard with 'Start' button for survey invitation."""
    kb = InlineKeyboardBuilder()
    kb.button(text=messages.t("btn_start_survey", lang), callback_data=callback_data)
    return kb


def build_refill_keyboard(callback_data: str, lang: str) -> InlineKeyboardBuilder:
    """Build keyboard with 'Refill' button for already filled survey."""
    kb = InlineKeyboardBuilder()
    kb.button(text=messages.t("btn_refill_survey", lang), callback_data=callback_data)
    return kb


def build_vienna_keyboard(lang: str) -> InlineKeyboardBuilder:
    """Build inline keyboard for Vienna location question."""
    kb = InlineKeyboardBuilder()
    kb.button(text=messages.t("btn_yes", lang), callback_data=f"{LOCATION_VIENNA_PREFIX}yes")
    kb.button(text=messages.t("btn_no", lang), callback_data=f"{LOCATION_VIENNA_PREFIX}no")
    kb.button(text=messages.t("btn_skip", lang), callback_data=SKIP_CALLBACK)
    kb.adjust(2, 1)
    return kb


def parse_sleep_duration(text: str) -> int | None:
    """Parse sleep duration from free text to minutes.

    Supports formats like:
    - "9 часов 20 минут"
    - "9h 20m"
    - "9 20"
    - "9:20"
    - "9.5" (hours)
    """
    text = text.strip().lower()

    # Try "Xh Ym" or "X h Y m" format
    match = re.match(r"(\d+)\s*h(?:ours?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2)) if match.group(2) else 0
        return hours * 60 + minutes

    # Try "X часов Y минут" format
    match = re.match(r"(\d+)\s*(?:час(?:а|ов)?)\s*(?:(\d+)\s*(?:мин(?:ут(?:а|ы)?)?)?)?", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2)) if match.group(2) else 0
        return hours * 60 + minutes

    # Try "X:Y" or "X Y" format (hours:minutes or hours minutes)
    match = re.match(r"(\d+)[:\s](\d+)", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        return hours * 60 + minutes

    # Try single number (hours) or decimal
    match = re.match(r"(\d+(?:\.\d+)?)", text)
    if match:
        hours = float(match.group(1))
        return int(hours * 60)

    return None


def parse_time(text: str) -> str | None:
    """Parse time from text to HH:MM format."""
    text = text.strip()

    # Try HH:MM format
    match = re.match(r"(\d{1,2})[:\.](\d{2})", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return f"{hours:02d}:{minutes:02d}"

    # Try HHMM format
    match = re.match(r"(\d{4})", text)
    if match:
        hours = int(text[:2])
        minutes = int(text[2:])
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return f"{hours:02d}:{minutes:02d}"

    return None


def _format_bar(value: int, max_val: int, filled: str = "●", empty: str = "○") -> str:
    """Create a visual bar like ●●●○○ for ratings."""
    return filled * value + empty * (max_val - value)


def format_survey_results(data: dict[str, Any], survey_type: str, lang: str) -> str:
    """Format survey results for display."""
    lines = []

    if survey_type == "evening":
        lines.append(f"<b>{messages.t('evening_results_title', lang)}</b>")
        lines.append("")

        if data.get("mood_evening") is not None:
            bar = _format_bar(data["mood_evening"], 5)
            lines.append(f"😊 Настроение:  {bar}")
        if data.get("energy") is not None:
            bar = _format_bar(data["energy"], 5)
            lines.append(f"⚡ Энергия:     {bar}")
        if data.get("anxiety") is not None:
            bar = _format_bar(data["anxiety"], 5)
            lines.append(f"😰 Тревожность: {bar}")
        if data.get("focus") is not None:
            focus_labels = ["💤", "📉", "📈", "🚀"]
            label = focus_labels[min(data["focus"], 3)]
            lines.append(f"🎯 Фокус:       {label}")
        if data.get("cravings") is not None:
            bar = _format_bar(data["cravings"], 5)
            lines.append(f"🍬 Cravings:    {bar}")
        if data.get("sport") is not None:
            sport_icon = "✅" if data["sport"] else "—"
            lines.append(f"🏃 Спорт:       {sport_icon}")

        habits = data.get("habits", {})
        completed = [label for key, label in HABITS if habits.get(key)]
        if completed:
            lines.append("")
            lines.append("🏆 <b>Привычки:</b>")
            for h in completed:
                lines.append(f"    ✅ {h}")

    elif survey_type == "morning":
        lines.append(f"<b>{messages.t('morning_results_title', lang)}</b>")
        lines.append("")

        # Weather info
        weather = data.get("weather", {})
        if weather.get("city"):
            lines.append(f"📍 {weather['city']}")
            weather_parts = []
            if weather.get("temperature_max") is not None:
                weather_parts.append(f"{weather['temperature_max']}°C")
            if weather.get("pressure") is not None:
                weather_parts.append(f"{round(weather['pressure'])} hPa")
            if weather.get("cloud_cover") is not None:
                weather_parts.append(f"☁️ {weather['cloud_cover']}%")
            if weather.get("uv_index") is not None:
                weather_parts.append(f"UV {weather['uv_index']}")
            if weather_parts:
                lines.append(f"🌤 {' · '.join(weather_parts)}")
            lines.append("")

        if data.get("mood_morning") is not None:
            bar = _format_bar(data["mood_morning"], 5)
            lines.append(f"😊 Настрой:     {bar}")
        if data.get("sleep_duration") is not None:
            hours = data["sleep_duration"] // 60
            mins = data["sleep_duration"] % 60
            time_str = f"{hours}ч {mins}м" if mins else f"{hours}ч"
            lines.append(f"😴 Сон:         {time_str}")
        if data.get("sleep_score") is not None:
            lines.append(f"📊 Sleep Score: {data['sleep_score']}")
        if data.get("wake_time") is not None:
            lines.append(f"⏰ Подъём:      {data['wake_time']}")

    return "\n".join(lines) if lines else messages.t("no_data", lang)


# ============== Evening Survey Handlers ==============

@router.message(Command("evening"))
async def cmd_evening(
    message: Message, state: FSMContext, settings: Settings, git_service: GitService
) -> None:
    """Handle /evening command."""
    lang = _user_lang(message.from_user.id if message.from_user else None)

    async with _get_survey_lock():
        await _sync_for_read(git_service, "/evening")
        data = await get_survey_data(settings.journal_dir, timezone=settings.timezone)

    if is_evening_survey_filled(data):
        # Show existing results with option to refill
        results = format_survey_results(data, "evening", lang)
        kb = build_refill_keyboard(EVENING_REFILL, lang)
        await _safe_respond(
            "evening results",
            lambda: message.answer(results, reply_markup=kb.as_markup())
        )
    else:
        # Show invitation to start
        kb = build_start_keyboard(EVENING_START, lang)
        await _safe_respond(
            "evening invite",
            lambda: message.answer(
                messages.t("evening_invite", lang),
                reply_markup=kb.as_markup()
            )
        )


async def send_evening_invite(bot, user_id: int, settings: Settings) -> None:
    """Send evening survey invitation (called by scheduler)."""
    lang = get_language(user_id)
    kb = build_start_keyboard(EVENING_START, lang)
    await bot.send_message(
        chat_id=user_id,
        text=messages.t("evening_invite", lang),
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.in_({EVENING_START, EVENING_REFILL}))
async def start_evening_survey(callback: CallbackQuery, state: FSMContext) -> None:
    """Start evening survey."""
    await state.clear()
    await state.update_data(survey_data={})
    lang = _user_lang(callback.from_user.id)

    await _safe_respond("evening start ack", callback.answer)
    if callback.message:
        await _safe_respond("evening start delete", callback.message.delete)

    kb = build_rating_keyboard(MOOD_EVENING_PREFIX, 1, 5, lang)
    await _safe_respond(
        "evening mood question",
        lambda: callback.message.answer(
            messages.t("q_mood_evening", lang),
            reply_markup=kb.as_markup()
        )
    )
    await state.set_state(EveningSurveyStates.mood)


@router.callback_query(F.data.startswith(MOOD_EVENING_PREFIX), StateFilter(EveningSurveyStates.mood))
async def evening_mood_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle mood rating for evening survey."""
    value = int(callback.data.replace(MOOD_EVENING_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["mood_evening"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("mood ack", callback.answer)
    if callback.message:
        await _safe_respond("mood delete", callback.message.delete)

    kb = build_rating_keyboard(ENERGY_PREFIX, 1, 5, lang)
    await _safe_respond(
        "energy question",
        lambda: callback.message.answer(messages.t("q_energy", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.energy)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.mood))
async def evening_mood_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip mood question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_rating_keyboard(ENERGY_PREFIX, 1, 5, lang)
    await _safe_respond(
        "energy question",
        lambda: callback.message.answer(messages.t("q_energy", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.energy)


@router.callback_query(F.data.startswith(ENERGY_PREFIX), StateFilter(EveningSurveyStates.energy))
async def evening_energy_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle energy rating."""
    value = int(callback.data.replace(ENERGY_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["energy"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("energy ack", callback.answer)
    if callback.message:
        await _safe_respond("energy delete", callback.message.delete)

    kb = build_rating_keyboard(ANXIETY_PREFIX, 1, 5, lang)
    await _safe_respond(
        "anxiety question",
        lambda: callback.message.answer(messages.t("q_anxiety", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.anxiety)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.energy))
async def evening_energy_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip energy question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_rating_keyboard(ANXIETY_PREFIX, 1, 5, lang)
    await _safe_respond(
        "anxiety question",
        lambda: callback.message.answer(messages.t("q_anxiety", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.anxiety)


@router.callback_query(F.data.startswith(ANXIETY_PREFIX), StateFilter(EveningSurveyStates.anxiety))
async def evening_anxiety_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle anxiety rating."""
    value = int(callback.data.replace(ANXIETY_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["anxiety"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("anxiety ack", callback.answer)
    if callback.message:
        await _safe_respond("anxiety delete", callback.message.delete)

    kb = build_rating_keyboard(FOCUS_PREFIX, 0, 3, lang)
    await _safe_respond(
        "focus question",
        lambda: callback.message.answer(messages.t("q_focus", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.focus)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.anxiety))
async def evening_anxiety_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip anxiety question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_rating_keyboard(FOCUS_PREFIX, 0, 3, lang)
    await _safe_respond(
        "focus question",
        lambda: callback.message.answer(messages.t("q_focus", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.focus)


@router.callback_query(F.data.startswith(FOCUS_PREFIX), StateFilter(EveningSurveyStates.focus))
async def evening_focus_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle focus rating."""
    value = int(callback.data.replace(FOCUS_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["focus"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("focus ack", callback.answer)
    if callback.message:
        await _safe_respond("focus delete", callback.message.delete)

    kb = build_rating_keyboard(CRAVINGS_PREFIX, 1, 5, lang)
    await _safe_respond(
        "cravings question",
        lambda: callback.message.answer(messages.t("q_cravings", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.cravings)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.focus))
async def evening_focus_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip focus question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_rating_keyboard(CRAVINGS_PREFIX, 1, 5, lang)
    await _safe_respond(
        "cravings question",
        lambda: callback.message.answer(messages.t("q_cravings", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.cravings)


@router.callback_query(F.data.startswith(CRAVINGS_PREFIX), StateFilter(EveningSurveyStates.cravings))
async def evening_cravings_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle cravings rating."""
    value = int(callback.data.replace(CRAVINGS_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["cravings"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("cravings ack", callback.answer)
    if callback.message:
        await _safe_respond("cravings delete", callback.message.delete)

    kb = build_boolean_keyboard(SPORT_PREFIX, lang)
    await _safe_respond(
        "sport question",
        lambda: callback.message.answer(messages.t("q_sport", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.sport)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.cravings))
async def evening_cravings_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip cravings question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_boolean_keyboard(SPORT_PREFIX, lang)
    await _safe_respond(
        "sport question",
        lambda: callback.message.answer(messages.t("q_sport", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.sport)


@router.callback_query(F.data.startswith(SPORT_PREFIX), StateFilter(EveningSurveyStates.sport))
async def evening_sport_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle sport boolean."""
    value = callback.data.replace(SPORT_PREFIX, "") == "yes"
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["sport"] = value
    await state.update_data(survey_data=survey_data, selected_habits={})

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("sport ack", callback.answer)
    if callback.message:
        await _safe_respond("sport delete", callback.message.delete)

    kb = build_habits_keyboard({}, lang)
    await _safe_respond(
        "habits question",
        lambda: callback.message.answer(messages.t("q_habits", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.habits)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.sport))
async def evening_sport_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip sport question."""
    lang = _user_lang(callback.from_user.id)
    await state.update_data(selected_habits={})
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    kb = build_habits_keyboard({}, lang)
    await _safe_respond(
        "habits question",
        lambda: callback.message.answer(messages.t("q_habits", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(EveningSurveyStates.habits)


@router.callback_query(F.data.startswith(HABIT_PREFIX), StateFilter(EveningSurveyStates.habits))
async def evening_habit_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    """Toggle a habit checkbox."""
    habit_key = callback.data.replace(HABIT_PREFIX, "")
    data = await state.get_data()
    selected = data.get("selected_habits", {})
    selected[habit_key] = not selected.get(habit_key, False)
    await state.update_data(selected_habits=selected)

    lang = _user_lang(callback.from_user.id)
    kb = build_habits_keyboard(selected, lang)
    await _safe_respond("habit toggle ack", callback.answer)
    if callback.message:
        await _safe_respond(
            "habits update",
            lambda: callback.message.edit_reply_markup(reply_markup=kb.as_markup())
        )


@router.callback_query(F.data == HABITS_DONE, StateFilter(EveningSurveyStates.habits))
async def evening_habits_done(
    callback: CallbackQuery, state: FSMContext, settings: Settings,
    git_service: GitService, sheets_service: SheetsService,
) -> None:
    """Finish evening survey with habits."""
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    selected = data.get("selected_habits", {})

    # Build habits dict with all habits set to False by default
    habits = {key: selected.get(key, False) for key, _ in HABITS}
    survey_data["habits"] = habits

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("habits done ack", callback.answer)
    if callback.message:
        await _safe_respond("habits done delete", callback.message.delete)

    # Save to storage
    save_state = await _save_with_sync(
        settings.journal_dir, survey_data, settings, git_service, sheets_service
    )
    status_key = _save_status_key(save_state)

    await _safe_respond(
        "evening done",
        lambda: callback.message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(EveningSurveyStates.habits))
async def evening_habits_skip(
    callback: CallbackQuery, state: FSMContext, settings: Settings,
    git_service: GitService, sheets_service: SheetsService,
) -> None:
    """Skip habits and finish evening survey."""
    data = await state.get_data()
    survey_data = data.get("survey_data", {})

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    # Save to storage
    save_state = await _save_with_sync(
        settings.journal_dir, survey_data, settings, git_service, sheets_service
    )
    status_key = _save_status_key(save_state)

    await _safe_respond(
        "evening done",
        lambda: callback.message.answer(messages.t(status_key, lang))
    )
    await state.clear()


# ============== Morning Survey Handlers ==============

@router.message(Command("morning"))
async def cmd_morning(
    message: Message, state: FSMContext, settings: Settings, git_service: GitService
) -> None:
    """Handle /morning command."""
    lang = _user_lang(message.from_user.id if message.from_user else None)

    async with _get_survey_lock():
        await _sync_for_read(git_service, "/morning")
        data = await get_survey_data(settings.journal_dir, timezone=settings.timezone)

    if is_morning_survey_filled(data):
        # Show existing results with option to refill
        results = format_survey_results(data, "morning", lang)
        kb = build_refill_keyboard(MORNING_REFILL, lang)
        await _safe_respond(
            "morning results",
            lambda: message.answer(results, reply_markup=kb.as_markup())
        )
    else:
        # Show invitation to start
        kb = build_start_keyboard(MORNING_START, lang)
        await _safe_respond(
            "morning invite",
            lambda: message.answer(
                messages.t("morning_invite", lang),
                reply_markup=kb.as_markup()
            )
        )


async def send_morning_invite(bot, user_id: int, settings: Settings) -> None:
    """Send morning survey invitation (called by scheduler)."""
    lang = get_language(user_id)
    kb = build_start_keyboard(MORNING_START, lang)
    await bot.send_message(
        chat_id=user_id,
        text=messages.t("morning_invite", lang),
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.in_({MORNING_START, MORNING_REFILL}))
async def start_morning_survey(callback: CallbackQuery, state: FSMContext) -> None:
    """Start morning survey with location question."""
    await state.clear()
    await state.update_data(survey_data={}, yesterday_data={})
    lang = _user_lang(callback.from_user.id)

    await _safe_respond("morning start ack", callback.answer)
    if callback.message:
        await _safe_respond("morning start delete", callback.message.delete)

    # Ask about location first
    kb = build_vienna_keyboard(lang)
    await _safe_respond(
        "location vienna question",
        lambda: callback.message.answer(
            messages.t("q_location_vienna", lang),
            reply_markup=kb.as_markup()
        )
    )
    await state.set_state(MorningSurveyStates.location)


async def _proceed_to_mood_question(callback: CallbackQuery, state: FSMContext) -> None:
    """Helper to proceed to mood question after location handling."""
    lang = _user_lang(callback.from_user.id)
    kb = build_rating_keyboard(MOOD_MORNING_PREFIX, 1, 5, lang)
    await _safe_respond(
        "morning mood question",
        lambda: callback.message.answer(
            messages.t("q_mood_morning", lang),
            reply_markup=kb.as_markup()
        )
    )
    await state.set_state(MorningSurveyStates.mood)


@router.callback_query(F.data.startswith(LOCATION_VIENNA_PREFIX), StateFilter(MorningSurveyStates.location))
async def morning_location_vienna_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle Vienna location answer."""
    is_vienna = callback.data.replace(LOCATION_VIENNA_PREFIX, "") == "yes"
    lang = _user_lang(callback.from_user.id)

    await _safe_respond("location ack", callback.answer)
    if callback.message:
        await _safe_respond("location delete", callback.message.delete)

    if is_vienna:
        # Fetch Vienna weather
        weather = await get_vienna_weather()
        if weather:
            data = await state.get_data()
            survey_data = data.get("survey_data", {})
            survey_data["weather"] = weather.to_dict()
            await state.update_data(survey_data=survey_data)

            # Notify user about weather
            await _safe_respond(
                "weather info",
                lambda: callback.message.answer(
                    messages.t("weather_fetched", lang).format(
                        city=weather.city,
                        temp=round(weather.temperature_max, 1),
                        uv=round(weather.uv_index, 1)
                    )
                )
            )
        else:
            await _safe_respond(
                "weather failed",
                lambda: callback.message.answer(messages.t("weather_fetch_failed", lang))
            )

        await _proceed_to_mood_question(callback, state)
    else:
        # Ask for city name
        await _safe_respond(
            "location city question",
            lambda: callback.message.answer(messages.t("q_location_city", lang))
        )
        await state.set_state(MorningSurveyStates.location_city)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(MorningSurveyStates.location))
async def morning_location_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip location question."""
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    await _proceed_to_mood_question(callback, state)


@router.message(F.text, StateFilter(MorningSurveyStates.location_city))
async def morning_location_city_answer(message: Message, state: FSMContext) -> None:
    """Handle city name input with validation."""
    lang = _user_lang(message.from_user.id if message.from_user else None)
    city_name = message.text.strip()

    # Try to fetch weather for the city (this validates the city exists)
    weather = await get_city_weather(city_name)

    if weather is None:
        # City not found - ask to retry
        await _safe_respond(
            "city not found",
            lambda: message.answer(messages.t("location_not_found", lang))
        )
        return  # Stay in location_city state, wait for valid input

    # City found - save weather data
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["weather"] = weather.to_dict()
    await state.update_data(survey_data=survey_data)

    # Notify user about weather
    await _safe_respond(
        "weather info",
        lambda: message.answer(
            messages.t("weather_fetched", lang).format(
                city=weather.city,
                temp=round(weather.temperature_max, 1),
                uv=round(weather.uv_index, 1)
            )
        )
    )

    # Proceed to mood question
    kb = build_rating_keyboard(MOOD_MORNING_PREFIX, 1, 5, lang)
    await _safe_respond(
        "morning mood question",
        lambda: message.answer(
            messages.t("q_mood_morning", lang),
            reply_markup=kb.as_markup()
        )
    )
    await state.set_state(MorningSurveyStates.mood)


@router.callback_query(F.data.startswith(MOOD_MORNING_PREFIX), StateFilter(MorningSurveyStates.mood))
async def morning_mood_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle mood rating for morning survey."""
    value = int(callback.data.replace(MOOD_MORNING_PREFIX, ""))
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["mood_morning"] = value
    await state.update_data(survey_data=survey_data)

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("mood ack", callback.answer)
    if callback.message:
        await _safe_respond("mood delete", callback.message.delete)

    await _safe_respond(
        "sleep duration question",
        lambda: callback.message.answer(messages.t("q_sleep_duration", lang))
    )
    await state.set_state(MorningSurveyStates.sleep_duration)


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(MorningSurveyStates.mood))
async def morning_mood_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip mood question."""
    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    await _safe_respond(
        "sleep duration question",
        lambda: callback.message.answer(messages.t("q_sleep_duration", lang))
    )
    await state.set_state(MorningSurveyStates.sleep_duration)


@router.message(F.text, StateFilter(MorningSurveyStates.sleep_duration))
async def morning_sleep_duration_answer(message: Message, state: FSMContext) -> None:
    """Handle sleep duration text input."""
    lang = _user_lang(message.from_user.id if message.from_user else None)
    duration = parse_sleep_duration(message.text)

    if duration is None:
        await _safe_respond(
            "invalid duration",
            lambda: message.answer(messages.t("invalid_sleep_duration", lang))
        )
        return

    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["sleep_duration"] = duration
    await state.update_data(survey_data=survey_data)

    await _safe_respond(
        "sleep score question",
        lambda: message.answer(messages.t("q_sleep_score", lang))
    )
    await state.set_state(MorningSurveyStates.sleep_score)


@router.message(F.text, StateFilter(MorningSurveyStates.sleep_score))
async def morning_sleep_score_answer(message: Message, state: FSMContext) -> None:
    """Handle sleep score text input."""
    lang = _user_lang(message.from_user.id if message.from_user else None)

    try:
        score = int(message.text.strip())
        if not 0 <= score <= 100:
            raise ValueError("Out of range")
    except ValueError:
        await _safe_respond(
            "invalid score",
            lambda: message.answer(messages.t("invalid_sleep_score", lang))
        )
        return

    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["sleep_score"] = score
    await state.update_data(survey_data=survey_data)

    await _safe_respond(
        "bedtime question",
        lambda: message.answer(messages.t("q_bedtime", lang))
    )
    await state.set_state(MorningSurveyStates.bedtime)


@router.message(F.text, StateFilter(MorningSurveyStates.bedtime))
async def morning_bedtime_answer(message: Message, state: FSMContext) -> None:
    """Handle bedtime text input (goes to yesterday's note)."""
    lang = _user_lang(message.from_user.id if message.from_user else None)
    time_str = parse_time(message.text)

    if time_str is None:
        await _safe_respond(
            "invalid time",
            lambda: message.answer(messages.t("invalid_time", lang))
        )
        return

    data = await state.get_data()
    yesterday_data = data.get("yesterday_data", {})
    yesterday_data["bedtime"] = time_str
    await state.update_data(yesterday_data=yesterday_data)

    await _safe_respond(
        "wake time question",
        lambda: message.answer(messages.t("q_wake_time", lang))
    )
    await state.set_state(MorningSurveyStates.wake_time)


@router.message(F.text, StateFilter(MorningSurveyStates.wake_time))
async def morning_wake_time_answer(message: Message, state: FSMContext) -> None:
    """Handle wake time text input."""
    lang = _user_lang(message.from_user.id if message.from_user else None)
    time_str = parse_time(message.text)

    if time_str is None:
        await _safe_respond(
            "invalid time",
            lambda: message.answer(messages.t("invalid_time", lang))
        )
        return

    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    survey_data["wake_time"] = time_str
    await state.update_data(survey_data=survey_data)

    kb = build_boolean_keyboard(READING_PREFIX, lang)
    await _safe_respond(
        "reading question",
        lambda: message.answer(messages.t("q_reading", lang), reply_markup=kb.as_markup())
    )
    await state.set_state(MorningSurveyStates.reading)


@router.callback_query(F.data.startswith(READING_PREFIX), StateFilter(MorningSurveyStates.reading))
async def morning_reading_answer(
    callback: CallbackQuery, state: FSMContext, settings: Settings,
    git_service: GitService, sheets_service: SheetsService,
) -> None:
    """Handle reading boolean (goes to yesterday's note) and finish survey."""
    value = callback.data.replace(READING_PREFIX, "") == "yes"
    data = await state.get_data()
    yesterday_data = data.get("yesterday_data", {})
    yesterday_data["habits"] = {"reading": value}

    survey_data = data.get("survey_data", {})

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("reading ack", callback.answer)
    if callback.message:
        await _safe_respond("reading delete", callback.message.delete)

    # Calculate yesterday's date
    now = datetime.now(settings.timezone)
    yesterday = now - timedelta(days=1)

    # Save today's data
    saved_today = await _save_with_sync(
        settings.journal_dir, survey_data, settings, git_service, sheets_service
    )

    # Save yesterday's data (bedtime + reading)
    saved_yesterday = await _save_with_sync(
        settings.journal_dir, yesterday_data, settings, git_service, sheets_service, moment=yesterday
    )

    save_state = _combine_save_states(saved_today, saved_yesterday)
    status_key = _save_status_key(save_state)

    await _safe_respond(
        "morning done",
        lambda: callback.message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.callback_query(F.data == SKIP_CALLBACK, StateFilter(MorningSurveyStates.reading))
async def morning_reading_skip(
    callback: CallbackQuery, state: FSMContext, settings: Settings,
    git_service: GitService, sheets_service: SheetsService,
) -> None:
    """Skip reading and finish morning survey."""
    data = await state.get_data()
    survey_data = data.get("survey_data", {})
    yesterday_data = data.get("yesterday_data", {})

    lang = _user_lang(callback.from_user.id)
    await _safe_respond("skip ack", callback.answer)
    if callback.message:
        await _safe_respond("skip delete", callback.message.delete)

    # Calculate yesterday's date
    now = datetime.now(settings.timezone)
    yesterday = now - timedelta(days=1)

    # Save today's data
    saved_today = await _save_with_sync(
        settings.journal_dir, survey_data, settings, git_service, sheets_service
    )

    # Save yesterday's data if any
    saved_yesterday = "synced"
    if yesterday_data:
        saved_yesterday = await _save_with_sync(
            settings.journal_dir, yesterday_data, settings, git_service, sheets_service, moment=yesterday
        )

    save_state = _combine_save_states(saved_today, saved_yesterday)
    status_key = _save_status_key(save_state)

    await _safe_respond(
        "morning done",
        lambda: callback.message.answer(messages.t(status_key, lang))
    )
    await state.clear()

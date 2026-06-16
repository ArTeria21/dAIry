import asyncio
import logging
import re
import tempfile
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Awaitable, Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dairy_bot.config import Settings
from dairy_bot.services.ai_service import transcribe_audio
from dairy_bot.services.git_sync import GitService, GitSyncError
from dairy_bot.services.language_store import get_language, set_language
from dairy_bot.services.storage import append_entry, read_daily_note_entries
from dairy_bot.services.toc_service import reconcile_toc
from dairy_bot.texts import LANG_BUTTONS, messages

router = Router()
logger = logging.getLogger(__name__)
MAX_TG_MESSAGE_LEN = 4000
_journal_lock: asyncio.Lock | None = None


class VoiceStates(StatesGroup):
    waiting_decision = State()
    waiting_edit = State()


CONFIRM_CALLBACK = "voice_confirm"
EDIT_CALLBACK = "voice_edit"
CANCEL_CALLBACK = "voice_cancel"
LANG_EN_CALLBACK = "lang_en"
LANG_RU_CALLBACK = "lang_ru"
LANG_CALLBACKS = {LANG_EN_CALLBACK, LANG_RU_CALLBACK}
ENTRY_TARGET_DATE_KEY = "entry_target_date"
DAY_COMMAND_RE = re.compile(r"^/day\s+(\d{2}-\d{2}-\d{4})\s*$")


async def _safe_respond(action: str, op: Callable[[], Awaitable[object]]) -> None:
    """Send a Telegram response without failing on temporary network errors."""
    try:
        await op()
    except TelegramNetworkError:
        logger.warning("Telegram request failed during %s", action, exc_info=True)
    except Exception:  # pragma: no cover - defensive boundary
        logger.exception("Unexpected error during %s", action)


def _user_lang(user_id: int | None) -> str:
    return get_language(user_id or 0)


def _split_long_line(line: str, max_len: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for char in line:
        char_len = len(escape(char))
        if current_len + char_len > max_len and current:
            parts.append("".join(current))
            current = [char]
            current_len = char_len
        else:
            current.append(char)
            current_len += char_len
    if current:
        parts.append("".join(current))
    return parts


def _split_text_for_html(text: str, max_len: int) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        line_len = len(escape(line))
        if line_len > max_len:
            if current:
                chunks.append("".join(current).rstrip("\n"))
                current = []
                current_len = 0
            chunks.extend(part.rstrip("\n") for part in _split_long_line(line, max_len))
            continue
        if current_len + line_len > max_len and current:
            chunks.append("".join(current).rstrip("\n"))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("".join(current).rstrip("\n"))
    return [chunk for chunk in chunks if chunk]


def get_journal_lock() -> asyncio.Lock:
    global _journal_lock
    if _journal_lock is None:
        _journal_lock = asyncio.Lock()
    return _journal_lock


def _save_status_key(save_state: str) -> str:
    if save_state == "empty":
        return "nothing_to_save"
    if save_state == "blocked":
        return "repo_sync_blocked"
    if save_state == "synced":
        return "save_synced"
    return "save_local_only"


async def _set_entry_target_date(state: FSMContext, target_date: date) -> None:
    await state.update_data(**{ENTRY_TARGET_DATE_KEY: target_date.isoformat()})


async def _get_entry_target_date(state: FSMContext) -> date | None:
    data = await state.get_data()
    value = data.get(ENTRY_TARGET_DATE_KEY)
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def _clear_entry_target_date(state: FSMContext) -> None:
    await state.update_data(**{ENTRY_TARGET_DATE_KEY: None})


def _parse_day_command(text: str | None) -> date | None:
    if not text:
        return None
    match = DAY_COMMAND_RE.match(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d-%m-%Y").date()
    except ValueError:
        return None


def _format_display_date(value: date) -> str:
    return value.strftime("%d-%m-%Y")


async def _save_entry_with_sync(
    content: str,
    settings: Settings,
    git_service: GitService,
    target_date: date | None = None,
    moment: datetime | None = None,
) -> str:
    if not content.strip():
        return "empty"

    async with get_journal_lock():
        try:
            await asyncio.to_thread(git_service.prepare_for_write)
        except GitSyncError:
            logger.warning("Git sync blocked journal write", exc_info=True)
            return "blocked"
        note_path = await append_entry(
            settings.journal_dir,
            content,
            moment=moment,
            timezone=settings.timezone,
            target_date=target_date,
        )
        toc_paths = await reconcile_toc(
            settings.journal_dir, settings, target_paths=[note_path]
        )
        try:
            result = await asyncio.to_thread(
                git_service.commit_and_push, [note_path] + toc_paths
            )
        except GitSyncError:
            logger.warning("Git push failed after journal write", exc_info=True)
            return "local_only"
        return "synced" if result.pushed else "local_only"


async def _sync_for_read(git_service: GitService, action: str) -> None:
    try:
        await asyncio.to_thread(git_service.sync_from_remote, allow_dirty=True)
    except GitSyncError:
        logger.warning("Git sync failed before %s; using local view", action, exc_info=True)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = _user_lang(message.from_user.id if message.from_user else None)

    keyboard = InlineKeyboardBuilder()
    for code, label in LANG_BUTTONS:
        keyboard.button(text=label, callback_data=f"lang_{code}")
    keyboard.adjust(2)

    await _safe_respond(
        "start prompt",
        lambda: message.answer(
            messages.t("start_prompt", lang), reply_markup=keyboard.as_markup()
        ),
    )


@router.message(Command("today"))
async def handle_today(
    message: Message, settings: Settings, git_service: GitService
) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)

    async with get_journal_lock():
        await _sync_for_read(git_service, "/today")
        content = await read_daily_note_entries(
            settings.journal_dir, timezone=settings.timezone
        )
    if not content.strip():
        await _safe_respond(
            "today empty note", lambda: message.answer(messages.t("today_empty", lang))
        )
        return

    date_label = _format_display_date(datetime.now(settings.timezone).date())
    reply_text = messages.format_today_note(date_label, content, lang)
    if len(reply_text) <= MAX_TG_MESSAGE_LEN:
        await _safe_respond("today note", lambda: message.answer(reply_text))
        return

    title = messages.t("today_header", lang).format(date=escape(date_label))
    await _safe_respond("today note header", lambda: message.answer(title))
    for index, chunk in enumerate(
        _split_text_for_html(content.strip(), MAX_TG_MESSAGE_LEN), start=1
    ):
        escaped_chunk = escape(chunk)
        await _safe_respond(
            f"today note chunk {index}",
            lambda chunk=escaped_chunk: message.answer(chunk),
        )


@router.message(Command("yesterday"))
async def handle_yesterday(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    target_date = datetime.now(settings.timezone).date() - timedelta(days=1)
    await _set_entry_target_date(state, target_date)
    await _safe_respond(
        "yesterday target set",
        lambda: message.answer(
            messages.format_date_override_set(_format_display_date(target_date), lang)
        ),
    )


@router.message(Command("day"))
async def handle_day(message: Message, state: FSMContext, settings: Settings) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    target_date = _parse_day_command(message.text)
    if target_date is None:
        await _safe_respond(
            "day target invalid",
            lambda: message.answer(messages.t("date_override_invalid", lang)),
        )
        return
    if target_date > datetime.now(settings.timezone).date():
        await _safe_respond(
            "day target future",
            lambda: message.answer(messages.t("date_override_future", lang)),
        )
        return

    await _set_entry_target_date(state, target_date)
    await _safe_respond(
        "day target set",
        lambda: message.answer(
            messages.format_date_override_set(_format_display_date(target_date), lang)
        ),
    )


@router.message(Command("back"))
async def handle_back(message: Message, state: FSMContext) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    await _clear_entry_target_date(state)
    await _safe_respond(
        "date target cancelled",
        lambda: message.answer(messages.t("date_override_cancelled", lang)),
    )


@router.callback_query(F.data.in_(LANG_CALLBACKS))
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = messages.LANG_EN if callback.data == LANG_EN_CALLBACK else messages.LANG_RU
    set_language(callback.from_user.id, lang)

    await _safe_respond(
        "language set callback answer",
        lambda: callback.answer(messages.t("start_set_language", lang)),
    )
    if callback.message:
        await _safe_respond(
            "language set remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )
        await _safe_respond(
            "language set confirmation",
            lambda: callback.message.answer(messages.t("start_set_language", lang)),
        )


@router.message(StateFilter(VoiceStates.waiting_decision))
async def handle_pending_decision(message: Message, state: FSMContext) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    await _safe_respond(
        "voice pending decision",
        lambda: message.answer(messages.t("voice_pending_decision", lang)),
    )


@router.message(F.text, StateFilter(VoiceStates.waiting_edit))
async def handle_edit(
    message: Message, state: FSMContext, settings: Settings, git_service: GitService
) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    target_date = await _get_entry_target_date(state)
    save_state = await _save_entry_with_sync(
        message.text, settings, git_service, target_date=target_date
    )
    status_key = _save_status_key(save_state)
    await _safe_respond(
        "edit save confirmation", lambda: message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.message(F.text, StateFilter(None))
async def handle_text(
    message: Message, state: FSMContext, settings: Settings, git_service: GitService
) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    target_date = await _get_entry_target_date(state)
    save_state = await _save_entry_with_sync(
        message.text, settings, git_service, target_date=target_date
    )
    status_key = _save_status_key(save_state)
    await _safe_respond(
        "text save confirmation", lambda: message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.message(F.voice, StateFilter(None))
async def handle_voice(message: Message, state: FSMContext, settings: Settings) -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".oga") as temp_oga:
        temp_oga_path = Path(temp_oga.name)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav:
        temp_wav_path = Path(temp_wav.name)

    try:
        await message.bot.download(message.voice, destination=temp_oga_path)
        # Telegram sends OGG Opus, while the model accepts WAV.
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i", str(temp_oga_path),
            "-ar", "16000",
            "-ac", "1",
            str(temp_wav_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError("FFmpeg conversion failed")
        transcription = await transcribe_audio(temp_wav_path, settings)
    except Exception:
        lang = _user_lang(message.from_user.id if message.from_user else None)
        await _safe_respond(
            "transcription error notice",
            lambda: message.answer(messages.t("transcription_error", lang)),
        )
        return
    finally:
        temp_oga_path.unlink(missing_ok=True)
        temp_wav_path.unlink(missing_ok=True)

    if not transcription:
        lang = _user_lang(message.from_user.id if message.from_user else None)
        await _safe_respond(
            "transcription empty notice",
            lambda: message.answer(messages.t("transcription_empty", lang)),
        )
        return

    lang = _user_lang(message.from_user.id if message.from_user else None)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text=messages.t("btn_save", lang), callback_data=CONFIRM_CALLBACK)
    keyboard.button(text=messages.t("btn_edit", lang), callback_data=EDIT_CALLBACK)
    keyboard.button(text=messages.t("btn_cancel", lang), callback_data=CANCEL_CALLBACK)
    keyboard.adjust(3)

    preview = messages.format_transcription_preview(transcription, lang)
    await _safe_respond(
        "transcription preview",
        lambda: message.answer(preview, reply_markup=keyboard.as_markup()),
    )
    await state.set_state(VoiceStates.waiting_decision)
    await state.update_data(transcription=transcription)


@router.callback_query(
    F.data == CONFIRM_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def confirm_voice(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    git_service: GitService,
) -> None:
    data = await state.get_data()
    transcription = data.get("transcription", "")
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    if not transcription:
        await _safe_respond(
            "nothing to save alert",
            lambda: callback.answer(
                messages.t("nothing_to_save", lang), show_alert=True
            ),
        )
        await state.clear()
        return

    target_date = await _get_entry_target_date(state)
    save_state = await _save_entry_with_sync(
        transcription, settings, git_service, target_date=target_date
    )
    status_key = _save_status_key(save_state)
    await _safe_respond(
        "voice confirm callback answer",
        lambda: callback.answer(messages.t(status_key, lang)),
    )
    if callback.message:
        await _safe_respond(
            "voice confirm remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )
        await _safe_respond(
            "voice confirm status message",
            lambda: callback.message.answer(messages.t(status_key, lang)),
        )
    await state.clear()


@router.callback_query(
    F.data == EDIT_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def edit_voice(callback: CallbackQuery, state: FSMContext) -> None:
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    await _safe_respond("edit voice callback answer", callback.answer)
    if callback.message:
        await _safe_respond(
            "edit voice remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )
        await _safe_respond(
            "edit voice prompt",
            lambda: callback.message.answer(messages.t("voice_prompt_edit", lang)),
        )
    await state.set_state(VoiceStates.waiting_edit)


@router.callback_query(
    F.data == CANCEL_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def cancel_voice(callback: CallbackQuery, state: FSMContext) -> None:
    await _safe_respond("cancel voice callback answer", callback.answer)
    if callback.message:
        await _safe_respond("cancel voice delete message", callback.message.delete)
    await state.clear()

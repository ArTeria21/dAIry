import asyncio
import logging
import tempfile
from datetime import datetime
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
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.language_store import get_language, set_language
from dairy_bot.services.storage import append_entry, read_daily_note
from dairy_bot.texts import LANG_BUTTONS, messages

router = Router()
logger = logging.getLogger(__name__)


class VoiceStates(StatesGroup):
    waiting_decision = State()
    waiting_edit = State()


CONFIRM_CALLBACK = "voice_confirm"
EDIT_CALLBACK = "voice_edit"
CANCEL_CALLBACK = "voice_cancel"
LANG_EN_CALLBACK = "lang_en"
LANG_RU_CALLBACK = "lang_ru"
LANG_CALLBACKS = {LANG_EN_CALLBACK, LANG_RU_CALLBACK}


async def _safe_respond(action: str, op: Callable[[], Awaitable[object]]) -> None:
    """Send a Telegram response but don't crash on transient network errors."""
    try:
        await op()
    except TelegramNetworkError:
        logger.warning("Telegram request failed during %s", action, exc_info=True)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Unexpected error during %s", action)


def _user_lang(user_id: int | None) -> str:
    return get_language(user_id or 0)


async def _save_entry_with_sync(
    content: str, settings: Settings, git_service: GitService
) -> bool:
    pulled = await asyncio.to_thread(git_service.pull_changes)
    note_path = await append_entry(
        settings.journal_dir, content, timezone=settings.timezone
    )
    pushed = await asyncio.to_thread(git_service.commit_and_push, note_path)
    return pulled and pushed


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

    pulled = await asyncio.to_thread(git_service.pull_changes)
    if not pulled:
        logger.warning("Git pull failed before responding to /today")

    content = await read_daily_note(settings.journal_dir, timezone=settings.timezone)
    if not content.strip():
        await _safe_respond(
            "today empty note", lambda: message.answer(messages.t("today_empty", lang))
        )
        return

    date_label = datetime.now(settings.timezone).strftime("%Y-%m-%d")
    reply_text = messages.format_today_note(date_label, content, lang)
    await _safe_respond("today note", lambda: message.answer(reply_text))


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
    synced = await _save_entry_with_sync(message.text, settings, git_service)
    status_key = "save_synced" if synced else "save_local_only"
    await _safe_respond(
        "edit save confirmation", lambda: message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.message(F.text, StateFilter(None))
async def handle_text(
    message: Message, state: FSMContext, settings: Settings, git_service: GitService
) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    synced = await _save_entry_with_sync(message.text, settings, git_service)
    status_key = "save_synced" if synced else "save_local_only"
    await _safe_respond(
        "text save confirmation", lambda: message.answer(messages.t(status_key, lang))
    )
    await state.clear()


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, settings: Settings) -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".oga") as temp_file:
        temp_path = Path(temp_file.name)

    try:
        await message.bot.download(message.voice, destination=temp_path)
        transcription = await transcribe_audio(temp_path, settings)
    except Exception:
        lang = _user_lang(message.from_user.id if message.from_user else None)
        await _safe_respond(
            "transcription error notice",
            lambda: message.answer(messages.t("transcription_error", lang)),
        )
        return
    finally:
        temp_path.unlink(missing_ok=True)

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

    synced = await _save_entry_with_sync(transcription, settings, git_service)
    status_key = "save_synced" if synced else "save_local_only"
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

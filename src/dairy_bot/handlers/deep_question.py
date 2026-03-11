import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dairy_bot.config import Settings
from dairy_bot.services.ai_service import transcribe_audio
from dairy_bot.services.deep_question_service import generate_deep_question
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.language_store import get_language
from dairy_bot.services.sheets_service import SheetsService
from dairy_bot.services.storage import (
    append_deep_answer,
    append_deep_question,
    count_deep_answers_for_day,
    get_survey_data,
    list_recent_deep_questions,
    pick_random_substantive_note,
)
from dairy_bot.texts import messages

router = Router()
logger = logging.getLogger(__name__)
_deep_lock: asyncio.Lock | None = None

ANSWER_PREFIX = "dq_answer"
OTHER_PREFIX = "dq_other"
VOICE_CONFIRM = "dq_voice_confirm"
VOICE_EDIT = "dq_voice_edit"
VOICE_CANCEL = "dq_voice_cancel"
SOURCE_DAILY = "daily"
SOURCE_MANUAL = "manual"


class DeepQuestionStates(StatesGroup):
    waiting_answer = State()
    waiting_voice_decision = State()
    waiting_voice_edit = State()


def _get_deep_lock() -> asyncio.Lock:
    global _deep_lock
    if _deep_lock is None:
        _deep_lock = asyncio.Lock()
    return _deep_lock


def _user_lang(user_id: int | None) -> str:
    return get_language(user_id or 0)


def _parse_question_callback(data: str | None, prefix: str) -> str | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 2:
        return None
    action, source = parts[0], parts[1]
    if action != prefix:
        return None
    if source not in {SOURCE_DAILY, SOURCE_MANUAL}:
        return None
    return source


def _build_question_keyboard(source: str, lang: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=messages.t("btn_deep_answer", lang),
        callback_data=f"{ANSWER_PREFIX}:{source}",
    )
    kb.button(
        text=messages.t("btn_deep_other", lang),
        callback_data=f"{OTHER_PREFIX}:{source}",
    )
    kb.adjust(2)
    return kb


def _build_voice_keyboard(lang: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text=messages.t("btn_save", lang), callback_data=VOICE_CONFIRM)
    kb.button(text=messages.t("btn_edit", lang), callback_data=VOICE_EDIT)
    kb.button(text=messages.t("btn_cancel", lang), callback_data=VOICE_CANCEL)
    kb.adjust(3)
    return kb


def _format_question(question: str, source: str, lang: str) -> str:
    if source == SOURCE_DAILY:
        title = messages.t("deep_question_daily_title", lang)
    else:
        title = messages.t("deep_question_manual_title", lang)
    return f"{title}\n\n{question.strip()}"


def _extract_question_text(message_text: str | None) -> str:
    if not message_text:
        return ""
    parts = message_text.split("\n\n", maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip()
    return message_text.strip()


async def _safe_respond(action: str, op) -> None:
    try:
        await op()
    except TelegramNetworkError:
        logger.warning("Telegram request failed during %s", action, exc_info=True)
    except Exception:
        logger.exception("Unexpected error during %s", action)


async def _generate_and_store_question(
    settings: Settings,
    git_service: GitService,
    source: str,
) -> str:
    async with _get_deep_lock():
        await asyncio.to_thread(git_service.pull_changes)
        recent_questions = await list_recent_deep_questions(settings.journal_dir, limit=15)
        random_note = await pick_random_substantive_note(
            settings.journal_dir, timezone=settings.timezone
        )
        question = await generate_deep_question(
            settings=settings,
            recent_questions=recent_questions,
            random_note_text=random_note,
        )
        note_path = await append_deep_question(
            journal_dir=settings.journal_dir,
            question=question,
            source=source,
            timezone=settings.timezone,
        )
        await asyncio.to_thread(git_service.commit_and_push, note_path)
    return question


async def _save_answer(
    answer: str,
    question_text: str,
    settings: Settings,
    git_service: GitService,
    sheets_service: SheetsService | None = None,
) -> None:
    async with _get_deep_lock():
        await asyncio.to_thread(git_service.pull_changes)
        note_path = await append_deep_answer(
            journal_dir=settings.journal_dir,
            answer=answer,
            question_text=question_text,
            timezone=settings.timezone,
        )
        await asyncio.to_thread(git_service.commit_and_push, note_path)
        if sheets_service and sheets_service.enabled:
            full_data = await get_survey_data(
                settings.journal_dir, timezone=settings.timezone
            )
            deep_answers_count = await count_deep_answers_for_day(
                settings.journal_dir, timezone=settings.timezone
            )
            await asyncio.to_thread(
                sheets_service.sync_survey_data,
                full_data,
                None,
                deep_answers_count,
            )


@router.message(Command("deep_question"))
async def cmd_deep_question(
    message: Message,
    settings: Settings,
    git_service: GitService,
    state: FSMContext,
) -> None:
    await state.clear()
    lang = _user_lang(message.from_user.id if message.from_user else None)
    progress_message = None
    try:
        progress_message = await message.answer(messages.t("deep_question_generating", lang))
    except Exception:
        progress_message = None

    try:
        question = await _generate_and_store_question(
            settings=settings,
            git_service=git_service,
            source=SOURCE_MANUAL,
        )
    except RuntimeError:
        if progress_message:
            await _safe_respond(
                "deep question generation failed edit",
                lambda: progress_message.edit_text(
                    messages.t("deep_question_generation_failed", lang)
                ),
            )
        else:
            await _safe_respond(
                "deep question generation failed",
                lambda: message.answer(messages.t("deep_question_generation_failed", lang)),
            )
        return

    text = _format_question(question, SOURCE_MANUAL, lang)
    kb = _build_question_keyboard(SOURCE_MANUAL, lang)
    if progress_message:
        await _safe_respond(
            "deep question generated edit",
            lambda: progress_message.edit_text(text, reply_markup=kb.as_markup()),
        )
    else:
        await _safe_respond(
            "deep question generated",
            lambda: message.answer(text, reply_markup=kb.as_markup()),
        )


@router.callback_query(F.data.startswith(f"{OTHER_PREFIX}:"))
async def regenerate_question(
    callback: CallbackQuery,
    settings: Settings,
    git_service: GitService,
    state: FSMContext,
) -> None:
    parsed = _parse_question_callback(callback.data, OTHER_PREFIX)
    if not parsed:
        await _safe_respond("invalid deep regenerate callback", callback.answer)
        return
    source = parsed
    # Acknowledge callback immediately to avoid Telegram timeout while LLM/Git work runs.
    await _safe_respond("deep regenerate callback answer", callback.answer)
    await state.clear()
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    progress_set = False
    if callback.message:
        await _safe_respond(
            "deep regenerate progress",
            lambda: callback.message.edit_text(messages.t("deep_question_generating", lang)),
        )
        progress_set = True

    try:
        question = await _generate_and_store_question(
            settings=settings,
            git_service=git_service,
            source=source,
        )
    except RuntimeError:
        if callback.message:
            if progress_set:
                await _safe_respond(
                    "deep regenerate failed edit",
                    lambda: callback.message.edit_text(
                        messages.t("deep_question_generation_failed", lang)
                    ),
                )
            else:
                await _safe_respond(
                    "deep regenerate failed message",
                    lambda: callback.message.answer(
                        messages.t("deep_question_generation_failed", lang)
                    ),
                )
        return

    kb = _build_question_keyboard(source, lang)
    new_text = _format_question(question, source, lang)
    if callback.message:
        await _safe_respond(
            "deep regenerate message",
            lambda: callback.message.edit_text(new_text, reply_markup=kb.as_markup()),
        )


@router.callback_query(F.data.startswith(f"{ANSWER_PREFIX}:"))
async def answer_question(callback: CallbackQuery, state: FSMContext) -> None:
    parsed = _parse_question_callback(callback.data, ANSWER_PREFIX)
    if not parsed:
        await _safe_respond("invalid deep answer callback", callback.answer)
        return
    source = parsed
    await _safe_respond("deep answer callback answer", callback.answer)
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    question_text = _extract_question_text(callback.message.text if callback.message else "")
    if not question_text:
        await _safe_respond(
            "deep answer missing text",
            lambda: callback.message.answer(messages.t("deep_question_answer_missing", lang))
            if callback.message
            else callback.answer(),
        )
        return
    await state.set_state(DeepQuestionStates.waiting_answer)
    await state.update_data(question_text=question_text, source=source)
    if callback.message:
        await _safe_respond(
            "deep answer prompt",
            lambda: callback.message.answer(messages.t("deep_question_answer_prompt", lang)),
        )


@router.message(F.text, StateFilter(DeepQuestionStates.waiting_answer))
async def save_text_answer(
    message: Message,
    state: FSMContext,
    settings: Settings,
    git_service: GitService,
    sheets_service: SheetsService | None = None,
) -> None:
    data = await state.get_data()
    question_text = str(data.get("question_text", "")).strip()
    lang = _user_lang(message.from_user.id if message.from_user else None)
    if not question_text:
        await state.clear()
        await _safe_respond(
            "deep answer missing id",
            lambda: message.answer(messages.t("deep_question_answer_missing", lang)),
        )
        return
    await _save_answer(
        message.text or "",
        question_text,
        settings,
        git_service,
        sheets_service=sheets_service,
    )
    await state.clear()
    await _safe_respond(
        "deep answer saved",
        lambda: message.answer(messages.t("deep_question_answer_saved", lang)),
    )


@router.message(F.voice, StateFilter(DeepQuestionStates.waiting_answer))
async def save_voice_answer(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    data = await state.get_data()
    question_text = str(data.get("question_text", "")).strip()
    lang = _user_lang(message.from_user.id if message.from_user else None)
    if not question_text:
        await state.clear()
        await _safe_respond(
            "deep voice answer missing id",
            lambda: message.answer(messages.t("deep_question_answer_missing", lang)),
        )
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=".oga") as temp_oga:
        temp_oga_path = Path(temp_oga.name)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav:
        temp_wav_path = Path(temp_wav.name)

    try:
        await message.bot.download(message.voice, destination=temp_oga_path)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(temp_oga_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(temp_wav_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError("FFmpeg conversion failed")
        transcription = await transcribe_audio(temp_wav_path, settings)
    except Exception:
        await _safe_respond(
            "deep voice transcription failed",
            lambda: message.answer(messages.t("transcription_error", lang)),
        )
        return
    finally:
        temp_oga_path.unlink(missing_ok=True)
        temp_wav_path.unlink(missing_ok=True)

    if not transcription:
        await _safe_respond(
            "deep voice transcription empty",
            lambda: message.answer(messages.t("transcription_empty", lang)),
        )
        return

    await state.set_state(DeepQuestionStates.waiting_voice_decision)
    await state.update_data(transcription=transcription, question_text=question_text)
    kb = _build_voice_keyboard(lang)
    preview = messages.format_transcription_preview(transcription, lang)
    await _safe_respond(
        "deep voice preview",
        lambda: message.answer(preview, reply_markup=kb.as_markup()),
    )


@router.callback_query(
    F.data == VOICE_CONFIRM, StateFilter(DeepQuestionStates.waiting_voice_decision)
)
async def confirm_voice_answer(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    git_service: GitService,
    sheets_service: SheetsService | None = None,
) -> None:
    data = await state.get_data()
    transcription = str(data.get("transcription", "")).strip()
    question_text = str(data.get("question_text", "")).strip()
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    if not transcription or not question_text:
        await state.clear()
        await _safe_respond(
            "deep voice nothing to save",
            lambda: callback.answer(messages.t("nothing_to_save", lang), show_alert=True),
        )
        return
    await _save_answer(
        transcription,
        question_text,
        settings,
        git_service,
        sheets_service=sheets_service,
    )
    await state.clear()
    await _safe_respond(
        "deep voice save callback answer",
        lambda: callback.answer(messages.t("deep_question_answer_saved", lang)),
    )
    if callback.message:
        await _safe_respond(
            "deep voice save remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )
        await _safe_respond(
            "deep voice save message",
            lambda: callback.message.answer(messages.t("deep_question_answer_saved", lang)),
        )


@router.callback_query(
    F.data == VOICE_EDIT, StateFilter(DeepQuestionStates.waiting_voice_decision)
)
async def edit_voice_answer(callback: CallbackQuery, state: FSMContext) -> None:
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    await state.set_state(DeepQuestionStates.waiting_voice_edit)
    await _safe_respond("deep voice edit callback answer", callback.answer)
    if callback.message:
        await _safe_respond(
            "deep voice edit remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )
        await _safe_respond(
            "deep voice edit prompt",
            lambda: callback.message.answer(messages.t("voice_prompt_edit", lang)),
        )


@router.message(F.text, StateFilter(DeepQuestionStates.waiting_voice_edit))
async def save_edited_voice_answer(
    message: Message,
    state: FSMContext,
    settings: Settings,
    git_service: GitService,
    sheets_service: SheetsService | None = None,
) -> None:
    data = await state.get_data()
    question_text = str(data.get("question_text", "")).strip()
    lang = _user_lang(message.from_user.id if message.from_user else None)
    if not question_text:
        await state.clear()
        await _safe_respond(
            "deep edited voice missing id",
            lambda: message.answer(messages.t("deep_question_answer_missing", lang)),
        )
        return
    await _save_answer(
        message.text or "",
        question_text,
        settings,
        git_service,
        sheets_service=sheets_service,
    )
    await state.clear()
    await _safe_respond(
        "deep edited voice saved",
        lambda: message.answer(messages.t("deep_question_answer_saved", lang)),
    )


@router.callback_query(
    F.data == VOICE_CANCEL, StateFilter(DeepQuestionStates.waiting_voice_decision)
)
async def cancel_voice_answer(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_respond("deep voice cancel callback answer", callback.answer)
    if callback.message:
        await _safe_respond(
            "deep voice cancel remove markup",
            lambda: callback.message.edit_reply_markup(reply_markup=None),
        )


async def send_daily_deep_question(
    bot,
    user_id: int,
    settings: Settings,
    git_service: GitService,
) -> bool:
    """Generate and deliver daily deep question with inline actions."""
    lang = _user_lang(user_id)
    try:
        question = await _generate_and_store_question(
            settings=settings,
            git_service=git_service,
            source=SOURCE_DAILY,
        )
    except RuntimeError:
        logger.exception("Failed to generate daily deep question")
        return False

    kb = _build_question_keyboard(SOURCE_DAILY, lang)
    await _safe_respond(
        "daily deep question send",
        lambda: bot.send_message(
            chat_id=user_id,
            text=_format_question(question, SOURCE_DAILY, lang),
            reply_markup=kb.as_markup(),
        ),
    )
    return True

import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dairy_bot.config import Settings
from dairy_bot.services.ai_service import transcribe_audio
from dairy_bot.services.language_store import get_language, set_language
from dairy_bot.services.storage import append_entry
from dairy_bot.texts import LANG_BUTTONS, messages

router = Router()


class VoiceStates(StatesGroup):
    waiting_decision = State()
    waiting_edit = State()


CONFIRM_CALLBACK = "voice_confirm"
EDIT_CALLBACK = "voice_edit"
CANCEL_CALLBACK = "voice_cancel"
LANG_EN_CALLBACK = "lang_en"
LANG_RU_CALLBACK = "lang_ru"
LANG_CALLBACKS = {LANG_EN_CALLBACK, LANG_RU_CALLBACK}


def _user_lang(user_id: int | None) -> str:
    return get_language(user_id or 0)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = _user_lang(message.from_user.id if message.from_user else None)

    keyboard = InlineKeyboardBuilder()
    for code, label in LANG_BUTTONS:
        keyboard.button(text=label, callback_data=f"lang_{code}")
    keyboard.adjust(2)

    await message.answer(
        messages.t("start_prompt", lang), reply_markup=keyboard.as_markup()
    )


@router.callback_query(F.data.in_(LANG_CALLBACKS))
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = messages.LANG_EN if callback.data == LANG_EN_CALLBACK else messages.LANG_RU
    set_language(callback.from_user.id, lang)

    await callback.answer(messages.t("start_set_language", lang))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(messages.t("start_set_language", lang))


@router.message(StateFilter(VoiceStates.waiting_decision))
async def handle_pending_decision(message: Message, state: FSMContext) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    await message.answer(messages.t("voice_pending_decision", lang))


@router.message(F.text, StateFilter(VoiceStates.waiting_edit))
async def handle_edit(message: Message, state: FSMContext, settings: Settings) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    await append_entry(settings.journal_path, message.text)
    await message.answer(messages.t("save_confirmed", lang))
    await state.clear()


@router.message(F.text, StateFilter(None))
async def handle_text(message: Message, state: FSMContext, settings: Settings) -> None:
    lang = _user_lang(message.from_user.id if message.from_user else None)
    await append_entry(settings.journal_path, message.text)
    await message.answer(messages.t("save_done", lang))
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
        await message.answer(messages.t("transcription_error", lang))
        return
    finally:
        temp_path.unlink(missing_ok=True)

    if not transcription:
        lang = _user_lang(message.from_user.id if message.from_user else None)
        await message.answer(messages.t("transcription_empty", lang))
        return

    lang = _user_lang(message.from_user.id if message.from_user else None)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text=messages.t("btn_save", lang), callback_data=CONFIRM_CALLBACK)
    keyboard.button(text=messages.t("btn_edit", lang), callback_data=EDIT_CALLBACK)
    keyboard.button(text=messages.t("btn_cancel", lang), callback_data=CANCEL_CALLBACK)
    keyboard.adjust(3)

    preview = messages.format_transcription_preview(transcription, lang)
    await message.answer(preview, reply_markup=keyboard.as_markup())
    await state.set_state(VoiceStates.waiting_decision)
    await state.update_data(transcription=transcription)


@router.callback_query(
    F.data == CONFIRM_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def confirm_voice(
    callback: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    data = await state.get_data()
    transcription = data.get("transcription", "")
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    if not transcription:
        await callback.answer(messages.t("nothing_to_save", lang), show_alert=True)
        await state.clear()
        return

    await append_entry(settings.journal_path, transcription)
    await callback.answer(messages.t("save_confirmed", lang))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(messages.t("save_confirmed", lang))
    await state.clear()


@router.callback_query(
    F.data == EDIT_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def edit_voice(callback: CallbackQuery, state: FSMContext) -> None:
    lang = _user_lang(callback.from_user.id if callback.from_user else None)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(messages.t("voice_prompt_edit", lang))
    await state.set_state(VoiceStates.waiting_edit)


@router.callback_query(
    F.data == CANCEL_CALLBACK, StateFilter(VoiceStates.waiting_decision)
)
async def cancel_voice(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.delete()
    await state.clear()

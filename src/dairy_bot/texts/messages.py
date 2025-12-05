from html import escape

LANG_EN = "en"
LANG_RU = "ru"
SUPPORTED_LANGS = {LANG_EN, LANG_RU}
DEFAULT_LANG = LANG_EN

LANG_BUTTONS = (
    (LANG_EN, "🇬🇧 English"),
    (LANG_RU, "🇷🇺 Русский"),
)

MESSAGES = {
    "save_done": {
        LANG_EN: "✅ Added to today's journal.",
        LANG_RU: "✅ Добавлено в журнал за сегодня.",
    },
    "save_confirmed": {
        LANG_EN: "✅ Voice note saved to today's page.",
        LANG_RU: "✅ Голосовая заметка сохранена.",
    },
    "save_synced": {
        LANG_EN: "✅ Saved and synced.",
        LANG_RU: "✅ Сохранено и синхронизировано.",
    },
    "save_local_only": {
        LANG_EN: "✅ Saved locally, but sync failed.",
        LANG_RU: "✅ Сохранено локально, но синхронизация не удалась.",
    },
    "voice_pending_decision": {
        LANG_EN: "You still have a voice note waiting. Confirm or edit it first.",
        LANG_RU: "У вас есть неподтверждённая голосовая заметка. Сначала подтвердите или исправьте её.",
    },
    "voice_prompt_edit": {
        LANG_EN: "✏️ Send the updated text and I'll save it.",
        LANG_RU: "✏️ Отправьте исправленный текст, я сохраню его.",
    },
    "voice_prompt_resend": {
        LANG_EN: "Got it. I'll save whatever you send next.",
        LANG_RU: "Хорошо, сохраню следующий текст.",
    },
    "transcription_error": {
        LANG_EN: "⚠️ I couldn't transcribe that one. Please retry or type your note.",
        LANG_RU: "⚠️ Не удалось расшифровать. Попробуйте ещё раз или введите текст.",
    },
    "transcription_empty": {
        LANG_EN: "I couldn't hear anything in that recording. Want to try again?",
        LANG_RU: "В записи не было звука. Попробуем ещё раз?",
    },
    "unauthorized": {
        LANG_EN: "🔒 This bot is private. Access is restricted.",
        LANG_RU: "🔒 Это приватный бот. Доступ ограничен.",
    },
    "reminder_message": {
        LANG_EN: "⏰ Evening nudge: today's page is still empty. Want to jot something down?",
        LANG_RU: "⏰ Вечернее напоминание: страница за сегодня пустая. Что-то добавить?",
    },
    "start_prompt": {
        LANG_EN: "Welcome! Choose your language to begin.",
        LANG_RU: "Добро пожаловать! Выберите язык, чтобы продолжить.",
    },
    "start_set_language": {
        LANG_EN: "Language set to English. Let's journal!",
        LANG_RU: "Язык переключён на русский. Готов принимать заметки!",
    },
    "btn_save": {
        LANG_EN: "✅ Save",
        LANG_RU: "✅ Сохранить",
    },
    "btn_edit": {
        LANG_EN: "✏️ Edit",
        LANG_RU: "✏️ Исправить",
    },
    "btn_cancel": {
        LANG_EN: "❌ Cancel",
        LANG_RU: "❌ Отмена",
    },
    "voice_preview_title": {
        LANG_EN: "Voice note preview",
        LANG_RU: "Предпросмотр голосовой заметки",
    },
    "voice_preview_question": {
        LANG_EN: "Save this to today's journal?",
        LANG_RU: "Сохранить в сегодняшнем журнале?",
    },
    "nothing_to_save": {
        LANG_EN: "Nothing to save.",
        LANG_RU: "Нет данных для сохранения.",
    },
    "today_empty": {
        LANG_EN: "No entries for today yet.",
        LANG_RU: "За сегодня пока нет записей.",
    },
    "today_header": {
        LANG_EN: "📓 Today's note ({date})",
        LANG_RU: "📓 Заметки за сегодня ({date})",
    },
}


def _normalize_lang(lang: str | None) -> str:
    if lang in SUPPORTED_LANGS:
        return lang
    return DEFAULT_LANG


def t(key: str, lang: str | None = None) -> str:
    lang_code = _normalize_lang(lang)
    values = MESSAGES.get(key, {})
    return values.get(lang_code, values.get(DEFAULT_LANG, ""))


def format_transcription_preview(transcription: str, lang: str | None = None) -> str:
    """Render a clean, escaped preview of the transcription."""
    safe_text = escape(transcription.strip()) or "…"
    title = t("voice_preview_title", lang)
    question = t("voice_preview_question", lang)
    return f"<b>{title}</b>\n<blockquote>{safe_text}</blockquote>\n{question}"


def format_today_note(date_label: str, content: str, lang: str | None = None) -> str:
    """Render today's note with a localized heading."""
    title = t("today_header", lang).format(date=escape(date_label))
    safe_body = escape(content.strip())
    if not safe_body:
        return title
    return f"{title}\n\n{safe_body}"

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
    "save_synced": {
        LANG_EN: "✅ Saved and synced.",
        LANG_RU: "✅ Сохранено и синхронизировано.",
    },
    "save_started": {
        LANG_EN: "Saving...",
        LANG_RU: "Сохраняю...",
    },
    "save_local_only": {
        LANG_EN: "✅ Saved locally, but sync failed.",
        LANG_RU: "✅ Сохранено локально, но синхронизация не удалась.",
    },
    "progress_writing_note": {
        LANG_EN: "⏳ Writing note to file...",
        LANG_RU: "⏳ Записываю заметку в файл...",
    },
    "progress_note_written": {
        LANG_EN: "✅ Note written to file",
        LANG_RU: "✅ Заметка записана в файл",
    },
    "progress_note_processing": {
        LANG_EN: "⏳ Processing note with LLM...",
        LANG_RU: "⏳ Обрабатываю заметку через LLM...",
    },
    "progress_note_processed": {
        LANG_EN: "✅ LLM processed note. {summary}",
        LANG_RU: "✅ Заметка обработана LLM. {summary}",
    },
    "progress_note_failed": {
        LANG_EN: "⚠️ LLM processing failed; I will retry in the background",
        LANG_RU: "⚠️ LLM-обработка не удалась; повторю в фоне",
    },
    "progress_git_syncing": {
        LANG_EN: "⏳ Syncing with git...",
        LANG_RU: "⏳ Синхронизирую с git...",
    },
    "progress_git_synced": {
        LANG_EN: "✅ Synced with git",
        LANG_RU: "✅ Синхронизировано с git",
    },
    "progress_git_local_only": {
        LANG_EN: "✅ Saved locally, but git sync failed",
        LANG_RU: "✅ Сохранено локально, но синхронизация с git не удалась",
    },
    "progress_note_summary": {
        LANG_EN: "Mood: {mood} ({confidence:.2f}), topics: {topics}",
        LANG_RU: "Настроение: {mood} ({confidence:.2f}), темы: {topics}",
    },
    "repo_sync_blocked": {
        LANG_EN: "⚠️ I couldn't sync the journal repo safely. Commit, stash, or revert local changes first, then try again.",
        LANG_RU: "⚠️ Я не смог безопасно синхронизировать репозиторий журнала. Сначала закоммить, убери в stash или откати локальные изменения, потом попробуй ещё раз.",
    },
    "voice_pending_decision": {
        LANG_EN: "You still have a voice note waiting. Confirm or edit it first.",
        LANG_RU: "У вас есть неподтверждённая голосовая заметка. Сначала подтвердите или исправьте её.",
    },
    "voice_prompt_edit": {
        LANG_EN: "✏️ Send the updated text and I'll save it.",
        LANG_RU: "✏️ Отправьте исправленный текст, я сохраню его.",
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
    "start_prompt": {
        LANG_EN: "Welcome! Choose your language to begin.",
        LANG_RU: "Добро пожаловать! Выберите язык, чтобы продолжить.",
    },
    "start_set_language": {
        LANG_EN: "Language set to English. Let's journal!",
        LANG_RU: "Язык переключён на русский. Готов принимать заметки!",
    },
    "btn_save": {
        LANG_EN: "💾 Save",
        LANG_RU: "💾 Сохранить",
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
    "date_override_set": {
        LANG_EN: "Next entry will be saved to {date}. If the note does not exist, I will create it.",
        LANG_RU: "Следующая запись сохранится в заметку за {date}. Если заметки ещё нет, я создам её.",
    },
    "date_override_cancelled": {
        LANG_EN: "Date override cancelled. The next entry will be saved to today.",
        LANG_RU: "Выбор другой даты отменён. Следующая запись сохранится в сегодняшний день.",
    },
    "date_override_invalid": {
        LANG_EN: "Use /day dd-mm-yyyy, for example /day 13-06-2026.",
        LANG_RU: "Используйте /day dd-mm-yyyy, например /day 13-06-2026.",
    },
    "date_override_future": {
        LANG_EN: "Future dates are not available. Choose today or a past date.",
        LANG_RU: "Нельзя выбрать дату в будущем. Выберите сегодняшний или прошедший день.",
    },
    "enrichment_disabled": {
        LANG_EN: "LLM enrichment is disabled.",
        LANG_RU: "LLM-обогащение выключено.",
    },
    "enrichment_done": {
        LANG_EN: "✅ Enrichment updated.",
        LANG_RU: "✅ Обогащение обновлено.",
    },
    "enrichment_failed": {
        LANG_EN: "⚠️ Enrichment failed. I will retry during the next background run.",
        LANG_RU: "⚠️ Обогащение не удалось. Повторю во время следующего фонового запуска.",
    },
}

MOOD_LABELS = {
    LANG_RU: {
        "joy": "радость",
        "calm": "спокойствие",
        "sadness": "грусть",
        "anger": "злость",
        "fear": "тревога",
        "neutral": "нейтральное",
        "mixed": "смешанное",
    }
}

TOPIC_LABELS = {
    LANG_RU: {
        "work": "работа",
        "learning": "обучение",
        "money": "деньги",
        "health": "здоровье",
        "fitness": "спорт",
        "nutrition": "питание",
        "relationships": "отношения",
        "travel": "путешествия",
        "creativity": "творчество",
        "identity": "идентичность",
        "spirituality": "духовность",
        "decision_making": "решения",
        "gratitude": "благодарность",
        "technology": "технологии",
        "entertainment": "развлечения",
        "therapy": "терапия",
        "planning": "планирование",
        "productivity": "продуктивность",
        "nature": "природа",
        "language": "язык",
        "living_situation": "быт",
        "bureaucracy": "бюрократия",
        "reflection": "рефлексия",
    }
}


def _normalize_lang(lang: str | None) -> str:
    if lang in SUPPORTED_LANGS:
        return lang
    return DEFAULT_LANG


def t(key: str, lang: str | None = None) -> str:
    lang_code = _normalize_lang(lang)
    values = MESSAGES.get(key, {})
    return values.get(lang_code, values.get(DEFAULT_LANG, ""))


def mood_label(value: object, lang: str | None = None) -> str:
    raw = getattr(value, "value", value)
    text = str(raw)
    return MOOD_LABELS.get(_normalize_lang(lang), {}).get(text, text)


def topic_label(value: object, lang: str | None = None) -> str:
    raw = getattr(value, "value", value)
    text = str(raw)
    return TOPIC_LABELS.get(_normalize_lang(lang), {}).get(text, text)


def format_transcription_preview(transcription: str, lang: str | None = None) -> str:
    """Show a safe HTML preview of the transcription."""
    safe_text = escape(transcription.strip()) or "…"
    title = t("voice_preview_title", lang)
    question = t("voice_preview_question", lang)
    return f"<b>{title}</b>\n<blockquote>{safe_text}</blockquote>\n{question}"


def format_today_note(date_label: str, content: str, lang: str | None = None) -> str:
    """Show today's entries with a localized heading."""
    title = t("today_header", lang).format(date=escape(date_label))
    safe_body = escape(content.strip())
    if not safe_body:
        return title
    return f"{title}\n\n{safe_body}"


def format_date_override_set(date_label: str, lang: str | None = None) -> str:
    return t("date_override_set", lang).format(date=escape(date_label))

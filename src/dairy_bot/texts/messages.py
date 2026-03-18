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
    "btn_deep_answer": {
        LANG_EN: "✍️ Answer",
        LANG_RU: "✍️ Ответить",
    },
    "btn_deep_other": {
        LANG_EN: "🔄 Another question",
        LANG_RU: "🔄 Другой вопрос",
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
    "deep_question_daily_title": {
        LANG_EN: "🧠 Question of the day",
        LANG_RU: "🧠 Вопрос дня",
    },
    "deep_question_manual_title": {
        LANG_EN: "🧠 Deep reflection question",
        LANG_RU: "🧠 Глубокий вопрос для рефлексии",
    },
    "deep_question_answer_prompt": {
        LANG_EN: "Send your answer now as text or voice, and I'll save it to today's note.",
        LANG_RU: "Отправь ответ текстом или голосом, и я сохраню его в заметку за сегодня.",
    },
    "deep_question_answer_saved": {
        LANG_EN: "✅ Deep answer saved.",
        LANG_RU: "✅ Ответ на глубокий вопрос сохранён.",
    },
    "deep_question_answer_missing": {
        LANG_EN: "⚠️ I lost question context. Generate a new question first.",
        LANG_RU: "⚠️ Не удалось определить вопрос. Сначала сгенерируй новый.",
    },
    "deep_question_generation_failed": {
        LANG_EN: "⚠️ Couldn't generate a deep question now. Please try again.",
        LANG_RU: "⚠️ Не удалось сгенерировать глубокий вопрос. Попробуй ещё раз.",
    },
    "deep_question_generating": {
        LANG_EN: "⌛ Generating a deep question...",
        LANG_RU: "⌛ Генерирую глубокий вопрос...",
    },
    # Survey buttons
    "btn_skip": {
        LANG_EN: "⏭️ Skip",
        LANG_RU: "⏭️ Пропустить",
    },
    "btn_yes": {
        LANG_EN: "✅ Yes",
        LANG_RU: "✅ Да",
    },
    "btn_no": {
        LANG_EN: "❌ No",
        LANG_RU: "❌ Нет",
    },
    "btn_done": {
        LANG_EN: "🏁 Done",
        LANG_RU: "🏁 Готово",
    },
    "btn_start_survey": {
        LANG_EN: "📝 Start",
        LANG_RU: "📝 Начать",
    },
    "btn_refill_survey": {
        LANG_EN: "🔄 Fill again",
        LANG_RU: "🔄 Заполнить заново",
    },
    # Survey invitations
    "evening_invite": {
        LANG_EN: "🌙 Good evening! Time for a quick check-in. How was your day?",
        LANG_RU: "🌙 Добрый вечер! Время для небольшого опроса. Как прошёл твой день?",
    },
    "morning_invite": {
        LANG_EN: "☀️ Good morning! Let's start the day with a quick check-in.",
        LANG_RU: "☀️ Доброе утро! Начнём день с небольшого опроса.",
    },
    # Evening survey questions
    "q_mood_evening": {
        LANG_EN: "😊 Rate your mood this evening (1-5, higher is better)",
        LANG_RU: "😊 Оцени своё настроение сегодня вечером (1-5, больше — лучше)",
    },
    "q_energy": {
        LANG_EN: "⚡ Rate your energy level today (1-5, higher is better)",
        LANG_RU: "⚡ Оцени уровень энергии сегодня (1-5, больше — лучше)",
    },
    "q_anxiety": {
        LANG_EN: "😰 Rate your anxiety level today (1-5, lower is better)",
        LANG_RU: "😰 Оцени уровень тревожности сегодня (1-5, меньше — лучше)",
    },
    "q_focus": {
        LANG_EN: "🎯 Rate your focus/engagement today:\n0 — couldn't concentrate\n1 — focused in bursts\n2 — good focus / engaged\n3 — God Mode (flow state)",
        LANG_RU: "🎯 Оцени свой фокус/включенность сегодня:\n0 — не мог собраться\n1 — фокус урывками\n2 — хорошая вовлечённость\n3 — God Mode (поток)",
    },
    "q_cravings": {
        LANG_EN: "🍬 How strong were your cravings for sweets/junk food? (1-5, lower is better)",
        LANG_RU: "🍬 Насколько сильно хотелось сладкого/джанк-фуда? (1-5, меньше — лучше)",
    },
    "q_sport": {
        LANG_EN: "🏃 Did you exercise today?",
        LANG_RU: "🏃 Был ли сегодня спорт?",
    },
    "q_habits": {
        LANG_EN: "🏆 Your achievements today (tap to toggle):",
        LANG_RU: "🏆 Твои ачивки за сегодня (нажми, чтобы отметить):",
    },
    # Morning survey questions
    "q_mood_morning": {
        LANG_EN: "😊 What's your mood for today? (1-5, higher is better)",
        LANG_RU: "😊 Какой настрой на сегодняшний день? (1-5, больше — лучше)",
    },
    "q_sleep_duration": {
        LANG_EN: "😴 How long did you sleep? (e.g., \"8h 30m\" or \"8 30\")",
        LANG_RU: "😴 Сколько ты спал сегодня? (например, \"8 часов 30 минут\" или \"8 30\")",
    },
    "q_sleep_score": {
        LANG_EN: "📊 What's your Sleep Score today? (0-100 from your sleep tracker)",
        LANG_RU: "📊 Какой у тебя Sleep Score сегодня? (0-100 из трекера сна)",
    },
    "q_bedtime": {
        LANG_EN: "🛏️ What time did you go to bed yesterday? (e.g., \"23:45\")",
        LANG_RU: "🛏️ Во сколько времени ты вчера лёг спать? (например, \"23:45\")",
    },
    "q_wake_time": {
        LANG_EN: "⏰ What time did you get out of bed today? (e.g., \"10:05\")",
        LANG_RU: "⏰ Во сколько времени ты сегодня встал из кровати? (например, \"10:05\")",
    },
    "q_reading": {
        LANG_EN: "📚 Did you read a book before sleep yesterday?",
        LANG_RU: "📚 Читал ли ты вчера перед сном книгу?",
    },
    # Validation errors
    "invalid_sleep_duration": {
        LANG_EN: "⚠️ I couldn't understand the format. Try \"8h 30m\" or \"8 30\".",
        LANG_RU: "⚠️ Не понял формат. Попробуй \"8 часов 30 минут\" или \"8 30\".",
    },
    "invalid_sleep_score": {
        LANG_EN: "⚠️ Please enter a number between 0 and 100.",
        LANG_RU: "⚠️ Введи число от 0 до 100.",
    },
    "invalid_time": {
        LANG_EN: "⚠️ I couldn't understand the time. Try \"23:45\" format.",
        LANG_RU: "⚠️ Не понял время. Попробуй формат \"23:45\".",
    },
    # Survey completion
    "survey_saved_synced": {
        LANG_EN: "✅ Survey saved and synced!",
        LANG_RU: "✅ Опрос сохранён и синхронизирован!",
    },
    "survey_saved_local": {
        LANG_EN: "✅ Survey saved locally, but sync failed.",
        LANG_RU: "✅ Опрос сохранён локально, но синхронизация не удалась.",
    },
    # Results display
    "evening_results_title": {
        LANG_EN: "🌙 Evening Survey Results",
        LANG_RU: "🌙 Результаты вечернего опроса",
    },
    "morning_results_title": {
        LANG_EN: "☀️ Morning Survey Results",
        LANG_RU: "☀️ Результаты утреннего опроса",
    },
    "habits_completed": {
        LANG_EN: "Habits completed",
        LANG_RU: "Выполненные привычки",
    },
    "no_data": {
        LANG_EN: "No data yet.",
        LANG_RU: "Данных пока нет.",
    },
    # Location questions
    "q_location_vienna": {
        LANG_EN: "📍 Are you in Vienna today?",
        LANG_RU: "📍 Ты сегодня в Вене?",
    },
    "q_location_city": {
        LANG_EN: "🌍 What city are you in? (e.g., \"Berlin\" or \"Prague\")",
        LANG_RU: "🌍 В каком городе ты находишься? (например, \"Берлин\" или \"Прага\")",
    },
    "location_not_found": {
        LANG_EN: "⚠️ Couldn't find that city. Please try again or skip.",
        LANG_RU: "⚠️ Не удалось найти этот город. Попробуй ещё раз или пропусти.",
    },
    "weather_fetched": {
        LANG_EN: "🌤 Got weather for {city}: {temp}°C, UV {uv}",
        LANG_RU: "🌤 Погода в {city}: {temp}°C, UV {uv}",
    },
    "weather_fetch_failed": {
        LANG_EN: "⚠️ Couldn't fetch weather data. Continuing without it.",
        LANG_RU: "⚠️ Не удалось получить данные о погоде. Продолжаем без них.",
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

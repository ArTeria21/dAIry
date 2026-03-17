<div align="center">

# 🥛 dAIry

**Your Personal AI Journaling & State Tracking Companion**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2ca5e0.svg)](https://aiogram.dev/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ed.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](#-english) | [Русский](#-русский)

</div>

---

<div id="english"></div>

## 🇬🇧 English

**dAIry** is a smart Telegram bot designed to help you document your life effortlessly. It serves as a bridge between your daily thoughts and a structured digital second brain (like Obsidian), with built-in state tracking surveys.

### 💡 Philosophy: Own Your Data, Empowered by AI

In an era of closed platforms, **dAIry** focuses on data sovereignty.

1.  **Markdown First:** All your entries are saved as clean, universal Markdown files with YAML frontmatter. You are not locked into a proprietary database.
2.  **LLM Ready:** By maintaining a chronological, text-based journal with structured metadata, you create a perfect dataset for Large Language Models (LLMs). You can easily feed your journal into an AI to analyze patterns, summarize weeks, or chat with your past self.
3.  **Git Sync:** Your journal is a Git repository. Every entry is automatically committed and pushed, ensuring you have version history and cloud backup (GitHub/GitLab) that syncs across devices.

### ✨ Key Features

- **📝 Text & Voice Journaling:** Send text messages or voice notes.
- **🎙️ AI Transcription:** Voice messages are automatically transcribed using state-of-the-art models (via OpenRouter/VoxTral) before saving.
- **🧠 Deep Questions:** AI-generated daily philosophical/reflection questions to prompt your journaling, avoiding repetitive topics.
- **📊 Daily Surveys:** Morning (10:00) and evening (20:00) check-ins to track your mood, energy, sleep, habits, and more.
- **🔄 Auto-Git Sync:** Automatically pulls changes before writing and pushes updates after saving.
- **🔒 Privacy Focused:** Single-user architecture. The bot only talks to _you_.
- **📂 Obsidian Compatible:** Files are organized by date (`YYYY-MM-DD.md`) with YAML frontmatter for metadata.
- **📊 Google Sheets Export:** Optionally sync survey data to Google Sheets for easy visualization and analysis.

### 📋 Survey System

#### Evening Survey (20:00 or `/evening`)
- 😊 Mood (1-5)
- ⚡ Energy level (1-5)
- 😰 Anxiety level (1-5)
- 🎯 Focus/productivity (0-3)
- 🍬 Cravings (1-5)
- 🏃 Sport (yes/no)
- 🏆 Habits checklist (steps, spending, learning, supplements, etc.)

#### Morning Survey (10:00 or `/morning`)
- 📍 Location check (Vienna or custom city)
- 🌤 Weather data (auto-fetched via Open-Meteo API)
- 😊 Mood for the day (1-5)
- 😴 Sleep duration
- 📊 Sleep score (0-100)
- 🛏️ Bedtime (saved to previous day)
- ⏰ Wake time
- 📚 Reading before sleep (saved to previous day)

#### 🌤 Weather Tracking

The morning survey asks about your location. If you're in Vienna (default), weather is fetched automatically. Otherwise, you can enter any city name.

Weather data saved:
- **Temperature** (°C) — daily maximum
- **Pressure** (hPa) — atmospheric pressure at sea level
- **Cloud cover** (%) — sky coverage
- **UV Index** — ultraviolet radiation level

This allows you to correlate your mood, energy, and sleep with weather conditions over time.

All survey data is stored in YAML frontmatter of daily notes, grouped thematically:
- **Mood & Mental:** mood_morning, mood_evening, energy, anxiety, focus
- **Sleep:** sleep_duration, sleep_score, bedtime, wake_time
- **Food:** cravings, no_junk_food, no_eating_out
- **Physical:** sport, steps_8k
- **Weather:** city, temperature_max, pressure, cloud_cover, uv_index
- **Habits:** supplements, tea_time, english_words, zero_spending, reading

### 🧠 Deep Questions

The bot will automatically ask you one deep, thought-provoking question per day at a random time (between configured daytime hours) to encourage journaling even on "routine" days.

- **AI-Powered:** Generates unique questions inspired by CBT, Stoicism, and Mindfulness.
- **Context-Aware:** Sometimes (40% chance) uses a random past journal entry as context to ask a highly personalized question.
- **No Repetition:** Analyzes the last 15 questions to ensure it doesn't repeat topics.
- **Inline Answers:** You can answer the question right in Telegram via text or voice, and it will be embedded into the daily Markdown note under the question block.
- **On-Demand:** Use the `/deep_question` command at any time to generate a new question manually.

### 📊 Google Sheets Integration (Optional)

You can enable automatic export of survey data to Google Sheets:

1. Create a Google Cloud project and enable Sheets API
2. Create a service account and download JSON credentials
3. Create a new Google Sheet and share it with the service account email
4. Add to `.env`:
   ```bash
   GOOGLE_SHEETS_ENABLED=true
   GOOGLE_SHEETS_ID=your_spreadsheet_id
   GOOGLE_CREDS_FILE=gcp_creds.json
   ```

Each row in the spreadsheet represents one day, with columns grouped thematically matching the YAML structure.

### 📑 Table of Contents Indexing (Optional)

The bot can automatically maintain a `table_of_contents.md` file in the root of your vault. This file gives LLM agents (and humans) a quick overview of every substantive note — with a 1-2 sentence English summary and a set of controlled tags.

**How it works:**

- After every bot write (journal entry, survey, deep question answer), the TOC is updated for the changed file.
- A periodic background scan (configurable interval, default 10 min) detects manual edits to older notes and re-indexes them.
- Empty or template-only daily notes (only frontmatter + date header) are excluded until real content appears.
- Summaries and tags are generated by an LLM via OpenRouter.

**Controlled tag vocabulary:** Tags are chosen from a fixed English vocabulary (~29 tags covering emotions, health, work, relationships, sleep, therapy, etc.). The LLM is instructed to pick only from this set, ensuring stable taxonomy for search and filtering.

**Enable in `.env`:**

```bash
TOC_ENABLED=true
# Extra directories to index (comma-separated, relative to JOURNAL_DIR)
TOC_EXTRA_INCLUDE_DIRS=EXTRA
# Scan interval for manual edits (minutes)
TOC_SCAN_INTERVAL_MINUTES=10
# Optional: override LLM model (defaults to QUESTION_MODEL_NAME)
# TOC_MODEL_NAME=openai/gpt-4.1-mini
```

**Entry format** (one line per note):

```
- [[2026/03/2026-03-15|2026-03-15]] :: Discussed anxiety about a work deadline and reflected on stoic practices. :: tags: [anxiety, work, reflection]
```

### 🛠 Tech Stack

- **Core:** Python 3.12+, `aiogram` 3.x (Async Telegram API)
- **Data:** Local Filesystem (Markdown + YAML), `GitPython` for version control
- **AI:** `openai` library (compatible with OpenRouter) for transcription
- **Weather:** `httpx` for Open-Meteo API requests
- **Scheduling:** `APScheduler` for survey triggers
- **Config:** `pydantic-settings` for robust environment management
- **Google Sheets:** `gspread` + `oauth2client` (optional)
- **Package Management:** `uv` (modern Python package installer)

### 📂 Project Structure

```text
src/
├── bot.py                 # Entry point
└── dairy_bot/
    ├── config.py          # Configuration loading
    ├── handlers/          # Telegram message handlers
    │   ├── deep_question.py # Deep question generation and answering
    │   ├── journal.py     # Text/voice processing
    │   └── survey.py      # Morning/evening surveys
    ├── middlewares/       # Auth and processing pipelines
    │   └── auth.py        # Security (white-list user)
    ├── services/          # Business logic
    │   ├── ai_service.py  # Voice transcription wrapper
    │   ├── deep_question_service.py # Deep questions AI generation
    │   ├── git_sync.py    # Git operations (pull/commit/push)
    │   ├── scheduler.py   # Survey triggers
    │   ├── sheets_service.py # Google Sheets sync (optional)
    │   ├── storage.py     # File system + YAML frontmatter
    │   ├── toc_service.py # Table of Contents indexing (optional)
    │   └── weather_service.py # Open-Meteo weather API
    └── texts/             # Static text messages
```

### 🚀 Getting Started

#### Prerequisites

- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather)).
- An OpenAI/OpenRouter API Key (for voice transcription).
- A local folder initialized as a Git repository (optional but recommended for sync).

#### Option A: Docker (Recommended)

1.  **Clone this repo.**
2.  **Create `.env` file:**
    ```bash
    BOT_TOKEN=your_bot_token
    ALLOWED_USER_ID=123456789
    OPENROUTER_API_KEY=your_key
    # AI Models config
    VOICE_MODEL_NAME=mistralai/voxtral-small-24b-2507
    QUESTION_MODEL_NAME=openai/gpt-4.1-mini
    QUESTION_LANGUAGE=en
    DEEP_QUESTION_START_HOUR=11
    DEEP_QUESTION_END_HOUR=20
    # Path inside container
    JOURNAL_DIR=/data
    # Path on your host machine to your notes repo
    HOST_JOURNAL_DIR=/Users/you/obsidian/vault
    TIMEZONE=Europe/Vienna
    GIT_ENABLED=true
    ```
3.  **Run:**
    ```bash
    docker compose up -d --build
    ```
    _Note: Ensure your SSH keys are mounted or configured if using Git over SSH._

#### Option B: Local Development

1.  **Install `uv`** (if not installed): `curl -LsSf https://astral.sh/uv/install.sh | sh`
2.  **Install dependencies:**
    ```bash
    uv sync
    ```
3.  **Configure `.env`:**
    ```bash
    BOT_TOKEN=...
    ALLOWED_USER_ID=...
    JOURNAL_DIR=/path/to/your/notes
    ```
4.  **Run:**
    ```bash
    uv run python src/bot.py
    ```

### 📜 Commands

| Command | Description |
|---------|-------------|
| `/start` | Initialize bot, choose language |
| `/today` | View today's journal entries |
| `/morning` | Start or view morning survey |
| `/evening` | Start or view evening survey |
| `/deep_question` | Generate a deep reflection question |

### 🔧 Developer Guide: Adding New Fields

When customizing surveys or adding new habits, follow these rules to avoid data loss.

#### ⚠️ Critical: Google Sheets Column Order

**Always add new columns to the END of the `HEADERS` list in `sheets_service.py`!**

```python
# ✅ CORRECT: Add new columns at the end
HEADERS = [
    "date",
    # ... existing columns ...
    "reading",
    "new_habit",      # ← Add here
    "new_metric",     # ← Add here
]
```

**❌ NEVER insert columns in the middle** — this will shift all existing data and corrupt your spreadsheet!

#### Adding a New Habit

1. **`survey.py`** — Add to `HABITS` list:
   ```python
   HABITS = [
       # ... existing ...
       ("new_habit", "My new habit"),
   ]
   ```

2. **`storage.py`** — Add to `DEFAULT_SURVEY_DATA["habits"]`:
   ```python
   "habits": {
       # ... existing ...
       "new_habit": None,
   }
   ```

3. **`sheets_service.py`** — Add to END of `HEADERS` and `row_data`:
   ```python
   HEADERS = [..., "new_habit"]  # At the END!
   row_data = [..., habits.get("new_habit")]  # Same order
   ```

#### Adding a New Survey Question

1. Add `State` to `MorningSurveyStates` or `EveningSurveyStates`
2. Add callback prefix constant
3. Add handlers (answer + skip)
4. Add field to `DEFAULT_SURVEY_DATA`
5. Add column to END of `HEADERS`
6. Add value to END of `row_data`
7. Add message texts to `messages.py`

#### Safe Operations

- **YAML frontmatter**: Adding new fields is always safe (key-value format)
- **Google Sheets**: Only safe if you add columns at the END
- **Reordering columns**: Export to CSV, modify `HEADERS`, rearrange CSV manually, reimport

---

<div id="russian"></div>

## 🇷🇺 Русский

**dAIry** — это умный Telegram-бот для ведения личного дневника и трекинга состояния. Он соединяет ваши повседневные мысли со структурированной базой знаний (например, Obsidian).

### 💡 Философия: Владейте данными, анализируйте с ИИ

В эпоху закрытых платформ **dAIry** делает ставку на суверенитет данных.

1.  **Markdown — это база:** Все записи сохраняются в чистых текстовых файлах (Markdown) с YAML-метаданными. Вы не привязаны к проприетарным базам данных.
2.  **Готовность к LLM:** Ведя хронологический текстовый дневник со структурированными метаданными, вы создаете идеальный датасет для языковых моделей. Вы сможете "скормить" свои записи ИИ, чтобы найти паттерны в поведении, подвести итоги недели или "поговорить" с собой из прошлого.
3.  **Git Синхронизация:** Ваш дневник — это Git-репозиторий. Каждая запись автоматически коммитится и отправляется в облако, обеспечивая историю изменений и синхронизацию между устройствами.

### ✨ Ключевые Возможности

- **📝 Текст и Голос:** Отправляйте текстовые сообщения или голосовые заметки.
- **🎙️ AI Транскрибация:** Голосовые сообщения автоматически расшифровываются в текст с помощью современных моделей (через OpenRouter/VoxTral).
- **🧠 Глубокие вопросы (Deep Questions):** Ежедневные философские вопросы для рефлексии от AI, помогающие вести дневник даже в рутинные дни.
- **📊 Ежедневные опросы:** Утренний (10:00) и вечерний (20:00) чек-ины для отслеживания настроения, энергии, сна, привычек и многого другого.
- **🔄 Авто-Git Sync:** Бот делает `git pull` перед записью и `git push` после.
- **🔒 Приватность:** Бот работает только для одного пользователя (вас).
- **📂 Совместимость с Obsidian:** Файлы сохраняются по датам (`YYYY-MM-DD.md`) с YAML-заголовком для метаданных.
- **📊 Экспорт в Google Sheets:** Опционально синхронизировать данные опросов в Google Таблицы для удобной визуализации и анализа.

### 📋 Система опросов

#### Вечерний опрос (20:00 или `/evening`)
- 😊 Настроение (1-5)
- ⚡ Уровень энергии (1-5)
- 😰 Уровень тревожности (1-5)
- 🎯 Фокус/продуктивность (0-3)
- 🍬 Тяга к сладкому (1-5)
- 🏃 Спорт (да/нет)
- 🏆 Чеклист привычек (шаги, траты, обучение, БАДы и др.)

#### Утренний опрос (10:00 или `/morning`)
- 📍 Проверка локации (Вена или другой город)
- 🌤 Данные о погоде (автоматически через Open-Meteo API)
- 😊 Настрой на день (1-5)
- 😴 Продолжительность сна
- 📊 Sleep Score (0-100)
- 🛏️ Время отбоя (сохраняется в предыдущий день)
- ⏰ Время подъёма
- 📚 Чтение перед сном (сохраняется в предыдущий день)

#### 🌤 Отслеживание погоды

Утренний опрос спрашивает о вашем местоположении. Если вы в Вене (по умолчанию), погода загружается автоматически. Иначе можно ввести любой город.

Сохраняемые данные о погоде:
- **Температура** (°C) — дневной максимум
- **Давление** (hPa) — атмосферное давление на уровне моря
- **Облачность** (%) — покрытие неба облаками
- **UV индекс** — уровень ультрафиолетового излучения

Это позволяет анализировать корреляции между вашим настроением, энергией, сном и погодными условиями.

Все данные опросов сохраняются в YAML-заголовке ежедневных заметок, сгруппированные тематически:
- **Настроение:** mood_morning, mood_evening, energy, anxiety, focus
- **Сон:** sleep_duration, sleep_score, bedtime, wake_time
- **Питание:** cravings, no_junk_food, no_eating_out
- **Физическая активность:** sport, steps_8k
- **Погода:** city, temperature_max, pressure, cloud_cover, uv_index
- **Привычки:** supplements, tea_time, english_words, zero_spending, reading

### 🧠 Глубокие вопросы (Deep Questions)

Бот автоматически задаст вам один глубокий вопрос в случайное время дня (между заданными дневными часами), чтобы стимулировать рефлексию даже в обычные, «рутинные» дни.

- **Генерация через AI:** Уникальные вопросы, основанные на КПТ (когнитивно-поведенческой терапии), стоицизме и практиках осознанности.
- **Учёт контекста:** Иногда (с вероятностью 40%) бот берёт случайную прошлую запись из вашего дневника и генерирует очень личный, персонализированный вопрос.
- **Без повторов:** Бот анализирует 15 последних заданных вопросов и следит за тем, чтобы темы не повторялись.
- **Ответы прямо в Telegram:** Вы можете нажать «Ответить» и записать голосовое или текстовое сообщение. Ответ автоматически встроится в блок вопроса в дневной Markdown-заметке.
- **По запросу:** Вы всегда можете использовать команду `/deep_question`, чтобы вручную сгенерировать вопрос для рефлексии в любой момент.

### 📊 Интеграция с Google Sheets (Опционально)

Можно включить автоматический экспорт данных опросов в Google Таблицы:

1. Создайте проект в Google Cloud и включите Sheets API
2. Создайте сервисный аккаунт и скачайте JSON с ключами
3. Создайте новую Google Таблицу и поделитесь ей с email сервисного аккаунта
4. Добавьте в `.env`:
   ```bash
   GOOGLE_SHEETS_ENABLED=true
   GOOGLE_SHEETS_ID=id_вашей_таблицы
   GOOGLE_CREDS_FILE=gcp_creds.json
   ```

Каждая строка в таблице соответствует одному дню, колонки сгруппированы тематически (как и в YAML).

### 📑 Индексация оглавления (Опционально)

Бот может автоматически поддерживать файл `table_of_contents.md` в корне вашего хранилища заметок. Этот файл даёт LLM-агентам (и людям) быстрый обзор каждой содержательной заметки — с кратким описанием на английском (1-2 предложения) и набором контролируемых тегов.

**Как это работает:**

- После каждой записи бота (журнальная запись, опрос, ответ на глубокий вопрос) оглавление обновляется для изменённого файла.
- Периодическое фоновое сканирование (настраиваемый интервал, по умолчанию 10 мин) обнаруживает ручные правки старых заметок и переиндексирует их.
- Пустые или шаблонные дневные заметки (только frontmatter + заголовок даты) исключаются, пока в них не появится реальное содержимое.
- Описания и теги генерируются LLM через OpenRouter.

**Контролируемый словарь тегов:** Теги выбираются из фиксированного англоязычного словаря (~29 тегов, покрывающих эмоции, здоровье, работу, отношения, сон, терапию и др.). LLM может выбирать только из этого набора, обеспечивая стабильную таксономию для поиска и фильтрации.

**Включение в `.env`:**

```bash
TOC_ENABLED=true
# Дополнительные папки для индексации (через запятую, относительно JOURNAL_DIR)
TOC_EXTRA_INCLUDE_DIRS=EXTRA
# Интервал сканирования ручных правок (минуты)
TOC_SCAN_INTERVAL_MINUTES=10
```

### 🛠 Технологии

- **Ядро:** Python 3.12+, `aiogram` 3.x (Асинхронный Telegram API)
- **Данные:** Локальная файловая система (Markdown + YAML), `GitPython` для контроля версий
- **AI:** библиотека `openai` (совместима с OpenRouter) для транскрибации
- **Погода:** `httpx` для запросов к Open-Meteo API
- **Планировщик:** `APScheduler` для триггеров опросов
- **Конфигурация:** `pydantic-settings`
- **Google Sheets:** `gspread` + `oauth2client` (опционально)
- **Менеджер пакетов:** `uv`

### 📂 Структура Проекта

```text
src/
├── bot.py                 # Точка входа
└── dairy_bot/
    ├── config.py          # Загрузка настроек
    ├── handlers/          # Обработчики сообщений
    │   ├── deep_question.py # Генерация и ответы на глубокие вопросы
    │   ├── journal.py     # Основная логика (текст/голос)
    │   └── survey.py      # Утренние/вечерние опросы
    ├── middlewares/       # Middleware (авторизация)
    │   └── auth.py        # Проверка ID пользователя
    ├── services/          # Бизнес-логика
    │   ├── ai_service.py  # Обертка для транскрибации
    │   ├── deep_question_service.py # AI генерация вопросов
    │   ├── git_sync.py    # Работа с Git (pull/commit/push)
    │   ├── scheduler.py   # Триггеры опросов
    │   ├── sheets_service.py # Синхронизация с Google Sheets (опц.)
    │   ├── storage.py     # Работа с файлами + YAML frontmatter
    │   ├── toc_service.py # Индексация оглавления (опц.)
    │   └── weather_service.py # Open-Meteo API для погоды
    └── texts/             # Текстовые константы
```

### 🚀 Установка и Запуск

#### Требования

- Токен Telegram бота (от [@BotFather](https://t.me/BotFather)).
- API Key от OpenAI или OpenRouter (для расшифровки голоса).
- Локальная папка, инициализированная как Git репозиторий.

#### Вариант А: Docker (Рекомендуется)

1.  **Склонируйте репозиторий.**
2.  **Создайте файл `.env`:**
    ```bash
    BOT_TOKEN=your_bot_token
    ALLOWED_USER_ID=123456789
    OPENROUTER_API_KEY=your_key
    # Настройки AI Моделей
    VOICE_MODEL_NAME=mistralai/voxtral-small-24b-2507
    QUESTION_MODEL_NAME=openai/gpt-4.1-mini
    QUESTION_LANGUAGE=ru
    DEEP_QUESTION_START_HOUR=11
    DEEP_QUESTION_END_HOUR=20
    # Путь внутри контейнера
    JOURNAL_DIR=/data
    # Путь на хост-машине к вашему репозиторию с заметками
    HOST_JOURNAL_DIR=/Users/you/obsidian/vault
    TIMEZONE=Europe/Vienna
    GIT_ENABLED=true
    ```
3.  **Запустите:**
    ```bash
    docker compose up -d --build
    ```
    _Примечание: Убедитесь, что SSH ключи проброшены в контейнер, если используете Git через SSH._

#### Вариант Б: Локальная разработка

1.  **Установите `uv`:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
2.  **Установите зависимости:**
    ```bash
    uv sync
    ```
3.  **Настройте `.env`:**
    ```bash
    BOT_TOKEN=...
    ALLOWED_USER_ID=...
    JOURNAL_DIR=/path/to/your/notes
    ```
4.  **Запустите:**
    ```bash
    uv run python src/bot.py
    ```

### 📜 Команды

| Команда | Описание |
|---------|----------|
| `/start` | Инициализация бота, выбор языка |
| `/today` | Показать записи за сегодня |
| `/morning` | Начать или посмотреть утренний опрос |
| `/evening` | Начать или посмотреть вечерний опрос |
| `/deep_question` | Сгенерировать глубокий вопрос для рефлексии |

### 🔧 Руководство разработчика: Добавление новых полей

При кастомизации опросов или добавлении новых привычек следуйте этим правилам, чтобы не потерять данные.

#### ⚠️ Критично: Порядок колонок в Google Sheets

**Всегда добавляйте новые колонки В КОНЕЦ списка `HEADERS` в `sheets_service.py`!**

```python
# ✅ ПРАВИЛЬНО: Новые колонки в конце
HEADERS = [
    "date",
    # ... существующие колонки ...
    "reading",
    "new_habit",      # ← Добавлять сюда
    "new_metric",     # ← Добавлять сюда
]
```

**❌ НИКОГДА не вставляйте колонки в середину** — это сдвинет все данные и испортит таблицу!

#### Добавление новой привычки

1. **`survey.py`** — Добавьте в список `HABITS`:
   ```python
   HABITS = [
       # ... существующие ...
       ("new_habit", "Моя новая привычка"),
   ]
   ```

2. **`storage.py`** — Добавьте в `DEFAULT_SURVEY_DATA["habits"]`:
   ```python
   "habits": {
       # ... существующие ...
       "new_habit": None,
   }
   ```

3. **`sheets_service.py`** — Добавьте в КОНЕЦ `HEADERS` и `row_data`:
   ```python
   HEADERS = [..., "new_habit"]  # В КОНЕЦ!
   row_data = [..., habits.get("new_habit")]  # Тот же порядок
   ```

#### Добавление нового вопроса опроса

1. Добавьте `State` в `MorningSurveyStates` или `EveningSurveyStates`
2. Добавьте константу callback prefix
3. Добавьте обработчики (answer + skip)
4. Добавьте поле в `DEFAULT_SURVEY_DATA`
5. Добавьте колонку в КОНЕЦ `HEADERS`
6. Добавьте значение в КОНЕЦ `row_data`
7. Добавьте текстовые сообщения в `messages.py`

#### Безопасные операции

- **YAML frontmatter**: Добавление новых полей всегда безопасно (формат key-value)
- **Google Sheets**: Безопасно только если добавлять колонки В КОНЕЦ
- **Переупорядочивание колонок**: Экспортируйте в CSV, измените `HEADERS`, переставьте колонки в CSV вручную, импортируйте обратно

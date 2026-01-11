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
- **📊 Daily Surveys:** Morning (10:00) and evening (20:00) check-ins to track your mood, energy, sleep, habits, and more.
- **🔄 Auto-Git Sync:** Automatically pulls changes before writing and pushes updates after saving.
- **🔒 Privacy Focused:** Single-user architecture. The bot only talks to _you_.
- **📂 Obsidian Compatible:** Files are organized by date (`YYYY-MM-DD.md`) with YAML frontmatter for metadata.

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
- 😊 Mood for the day (1-5)
- 😴 Sleep duration
- 📊 Sleep score (0-100)
- 🛏️ Bedtime (saved to previous day)
- ⏰ Wake time
- 📚 Reading before sleep (saved to previous day)

All survey data is stored in YAML frontmatter of daily notes for easy analysis.

### 🛠 Tech Stack

- **Core:** Python 3.12+, `aiogram` 3.x (Async Telegram API)
- **Data:** Local Filesystem (Markdown + YAML), `GitPython` for version control
- **AI:** `openai` library (compatible with OpenRouter) for transcription
- **Scheduling:** `APScheduler` for survey triggers
- **Config:** `pydantic-settings` for robust environment management
- **Package Management:** `uv` (modern Python package installer)

### 📂 Project Structure

```text
src/
├── bot.py                 # Entry point
└── dairy_bot/
    ├── config.py          # Configuration loading
    ├── handlers/          # Telegram message handlers
    │   ├── journal.py     # Text/voice processing
    │   └── survey.py      # Morning/evening surveys
    ├── middlewares/       # Auth and processing pipelines
    │   └── auth.py        # Security (white-list user)
    ├── services/          # Business logic
    │   ├── ai_service.py  # Voice transcription wrapper
    │   ├── git_sync.py    # Git operations (pull/commit/push)
    │   ├── scheduler.py   # Survey triggers
    │   └── storage.py     # File system + YAML frontmatter
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
- **📊 Ежедневные опросы:** Утренний (10:00) и вечерний (20:00) чек-ины для отслеживания настроения, энергии, сна, привычек и многого другого.
- **🔄 Авто-Git Sync:** Бот делает `git pull` перед записью и `git push` после.
- **🔒 Приватность:** Бот работает только для одного пользователя (вас).
- **📂 Совместимость с Obsidian:** Файлы сохраняются по датам (`YYYY-MM-DD.md`) с YAML-заголовком для метаданных.

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
- 😊 Настрой на день (1-5)
- 😴 Продолжительность сна
- 📊 Sleep Score (0-100)
- 🛏️ Время отбоя (сохраняется в предыдущий день)
- ⏰ Время подъёма
- 📚 Чтение перед сном (сохраняется в предыдущий день)

Все данные опросов сохраняются в YAML-заголовке ежедневных заметок для удобного анализа.

### 🛠 Технологии

- **Ядро:** Python 3.12+, `aiogram` 3.x (Асинхронный Telegram API)
- **Данные:** Локальная файловая система (Markdown + YAML), `GitPython` для контроля версий
- **AI:** библиотека `openai` (совместима с OpenRouter) для транскрибации
- **Планировщик:** `APScheduler` для триггеров опросов
- **Конфигурация:** `pydantic-settings`
- **Менеджер пакетов:** `uv`

### 📂 Структура Проекта

```text
src/
├── bot.py                 # Точка входа
└── dairy_bot/
    ├── config.py          # Загрузка настроек
    ├── handlers/          # Обработчики сообщений
    │   ├── journal.py     # Основная логика (текст/голос)
    │   └── survey.py      # Утренние/вечерние опросы
    ├── middlewares/       # Middleware (авторизация)
    │   └── auth.py        # Проверка ID пользователя
    ├── services/          # Бизнес-логика
    │   ├── ai_service.py  # Обертка для транскрибации
    │   ├── git_sync.py    # Работа с Git (pull/commit/push)
    │   ├── scheduler.py   # Триггеры опросов
    │   └── storage.py     # Работа с файлами + YAML frontmatter
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

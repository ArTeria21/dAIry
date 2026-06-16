> **You are an AI coding agent (Claude Code / Codex) working on the `dAIry` repository.**
> This document is your complete brief. Read it fully before writing any code.
> The project already exists (Telegram journaling bot, Python 3.12, aiogram 3.x,
> Markdown + YAML in a git-synced Obsidian vault). You are re-shaping it, not
> starting from scratch.

# Task
Я хочу, чтобы мы максимально упростили сервис сейчас и удалили оттуда лишние и неиспользуемые функции:
- Утренние и вечерние опросы
- Deep Questions
- Сохранение метрик в Google таблицы

Сервис должен остаться только capturing layer, который принимает голосвые или текстовые сообщения. Голосовые сообщения транскрибируются через VOICE_MODEL_NAME и сохраняться в папку HOST_JOURNAL_DIR и синхронизироваться с github репозиторием.

Давай проведём эту очистку и упрощение проекта и оставим только эти функции.

## 14. Tech notes
- Python 3.12, `uv`, `aiogram` 3.x, `GitPython`, `pydantic` v2, `pydantic-settings`.
- LLM via **OpenRouter** (`openai` lib) for transcription **and** enrichment; use structured output / JSON Schema (constrained decoding) for the enrichment models. Keep model names in config.
- Follow the existing repo conventions; update `README.md` to reflect the new philosophy and commands.
- Write README.md и комментарии в коде на русском языке

## dAIry

Personal single-user Telegram journaling bot.

### Setup

- Create `.env` with: `BOT_TOKEN`, `ALLOWED_USER_ID`, `OPENROUTER_API_KEY`, `JOURNAL_PATH`, optional `VOICE_MODEL_NAME`, `OPENROUTER_BASE_URL`, and `TIMEZONE` (default `Europe/Vienna`).
- Install deps: `uv sync` (env already created; update if needed).
- Run: `uv run python src/bot.py`.

Example `.env`:

```
BOT_TOKEN=123456:telegram-bot-token
ALLOWED_USER_ID=123456789
OPENROUTER_API_KEY=sk-or-xxx
JOURNAL_PATH=/Users/you/Notes/dairy
TIMEZONE=Europe/Vienna
# Optional:
# VOICE_MODEL_NAME=mistralai/voxtral-small-24b-2507
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
# GIT_ENABLED=true
```

### Behavior

- Text messages append immediately to today's note under `## HH:MM` in your configured timezone (default Europe/Vienna) and reply with `✅`.
- Voice messages are transcribed via OpenRouter VoxTral, then you confirm or edit before saving.
- Daily reminder at 20:00 in your configured timezone if today's note is missing/empty.

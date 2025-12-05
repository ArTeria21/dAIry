## dAIry

Personal single-user Telegram journaling bot.

### Setup

- Create `.env` with: `BOT_TOKEN`, `ALLOWED_USER_ID`, `OPENROUTER_API_KEY`, `JOURNAL_PATH`, optional `VOXTRAIL_MODEL_NAME` and `OPENROUTER_BASE_URL`.
- Install deps: `uv sync` (env already created; update if needed).
- Run: `uv run python src/bot.py`.

### Behavior

- Text messages append immediately to today's note under `## HH:MM` (Europe/Vienna) and reply with `✅`.
- Voice messages are transcribed via OpenRouter VoxTral, then you confirm or edit before saving.
- Daily reminder at 20:00 Vienna time if today's note is missing/empty.

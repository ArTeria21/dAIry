from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dairy_bot.config import Settings
from dairy_bot.services.language_store import get_language
from dairy_bot.services.storage import note_has_content
from dairy_bot.texts import messages


def setup_scheduler(bot: Bot, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def send_reminder() -> None:
        lang = get_language(settings.allowed_user_id)
        has_entry = await note_has_content(
            settings.journal_dir, timezone=settings.timezone
        )
        if not has_entry:
            await bot.send_message(
                chat_id=settings.allowed_user_id,
                text=messages.t("reminder_message", lang),
            )

    scheduler.add_job(
        send_reminder,
        trigger=CronTrigger(hour=20, minute=0, timezone=settings.timezone),
        id="daily_reminder",
        replace_existing=True,
    )
    return scheduler

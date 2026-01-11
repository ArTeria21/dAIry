from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dairy_bot.config import Settings
from dairy_bot.handlers.survey import send_evening_invite, send_morning_invite


def setup_scheduler(bot: Bot, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def morning_survey_job() -> None:
        await send_morning_invite(bot, settings.allowed_user_id, settings)

    async def evening_survey_job() -> None:
        await send_evening_invite(bot, settings.allowed_user_id, settings)

    scheduler.add_job(
        morning_survey_job,
        trigger=CronTrigger(hour=10, minute=0, timezone=settings.timezone),
        id="morning_survey",
        replace_existing=True,
    )

    scheduler.add_job(
        evening_survey_job,
        trigger=CronTrigger(hour=20, minute=0, timezone=settings.timezone),
        id="evening_survey",
        replace_existing=True,
    )

    return scheduler

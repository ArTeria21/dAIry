import asyncio
import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from dairy_bot.config import Settings
from dairy_bot.handlers.deep_question import send_daily_deep_question
from dairy_bot.handlers.survey import send_evening_invite, send_morning_invite
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.storage import day_has_daily_question_sent
from dairy_bot.services.toc_service import reconcile_toc

logger = logging.getLogger(__name__)


def _randomize_delivery_time(now: datetime, settings: Settings) -> datetime:
    start_today = now.replace(
        hour=settings.deep_question_start_hour, minute=0, second=0, microsecond=0
    )
    end_today = now.replace(
        hour=settings.deep_question_end_hour, minute=0, second=0, microsecond=0
    )

    if settings.deep_question_end_hour <= settings.deep_question_start_hour:
        # Defensive fallback for misconfiguration.
        start_today = now.replace(hour=11, minute=0, second=0, microsecond=0)
        end_today = now.replace(hour=20, minute=0, second=0, microsecond=0)

    if now < start_today:
        lower = start_today
        upper = end_today
    elif now >= end_today:
        lower = start_today + timedelta(days=1)
        upper = end_today + timedelta(days=1)
    else:
        lower = now + timedelta(minutes=1)
        upper = end_today

    if lower >= upper:
        return lower

    total_seconds = int((upper - lower).total_seconds())
    random_offset = random.randint(0, total_seconds)
    return lower + timedelta(seconds=random_offset)


def schedule_daily_deep_question_delivery(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    settings: Settings,
    git_service: GitService,
) -> None:
    now = datetime.now(settings.timezone)
    run_at = _randomize_delivery_time(now, settings)

    async def deliver_deep_question() -> None:
        already_sent = await day_has_daily_question_sent(
            settings.journal_dir, moment=run_at, timezone=settings.timezone
        )
        if already_sent:
            return
        sent = await send_daily_deep_question(
            bot=bot,
            user_id=settings.allowed_user_id,
            settings=settings,
            git_service=git_service,
        )
        if not sent:
            logger.warning("Daily deep question delivery failed")

    scheduler.add_job(
        deliver_deep_question,
        trigger=DateTrigger(run_date=run_at, timezone=settings.timezone),
        id="daily_deep_question_delivery",
        replace_existing=True,
    )


async def recover_daily_deep_question(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    settings: Settings,
    git_service: GitService,
) -> None:
    now = datetime.now(settings.timezone)
    already_sent = await day_has_daily_question_sent(
        settings.journal_dir, moment=now, timezone=settings.timezone
    )
    if already_sent:
        return

    start_today = now.replace(
        hour=settings.deep_question_start_hour, minute=0, second=0, microsecond=0
    )
    end_today = now.replace(
        hour=settings.deep_question_end_hour, minute=0, second=0, microsecond=0
    )
    if start_today <= now < end_today:
        await send_daily_deep_question(
            bot=bot,
            user_id=settings.allowed_user_id,
            settings=settings,
            git_service=git_service,
        )
        return

    if now < start_today:
        schedule_daily_deep_question_delivery(
            scheduler=scheduler,
            bot=bot,
            settings=settings,
            git_service=git_service,
        )


def setup_scheduler(
    bot: Bot, settings: Settings, git_service: GitService
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def morning_survey_job() -> None:
        await send_morning_invite(bot, settings.allowed_user_id, settings)

    async def evening_survey_job() -> None:
        await send_evening_invite(bot, settings.allowed_user_id, settings)

    async def deep_question_seed_job() -> None:
        schedule_daily_deep_question_delivery(
            scheduler=scheduler,
            bot=bot,
            settings=settings,
            git_service=git_service,
        )

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
    scheduler.add_job(
        deep_question_seed_job,
        trigger=CronTrigger(hour=0, minute=5, timezone=settings.timezone),
        id="deep_question_seed",
        replace_existing=True,
    )
    schedule_daily_deep_question_delivery(
        scheduler=scheduler,
        bot=bot,
        settings=settings,
        git_service=git_service,
    )

    if settings.toc_enabled:
        async def toc_reconcile_job() -> None:
            try:
                toc_paths = await reconcile_toc(settings.journal_dir, settings)
                if toc_paths:
                    await asyncio.to_thread(git_service.commit_and_push, toc_paths)
            except Exception:
                logger.exception("Periodic TOC reconciliation failed")

        scheduler.add_job(
            toc_reconcile_job,
            trigger=IntervalTrigger(
                minutes=settings.toc_scan_interval_minutes,
                timezone=settings.timezone,
            ),
            id="toc_reconcile",
            replace_existing=True,
        )

    return scheduler

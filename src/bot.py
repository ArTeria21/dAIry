import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from dairy_bot.config import Settings
from dairy_bot.handlers.deep_question import router as deep_question_router
from dairy_bot.handlers.journal import router as journal_router
from dairy_bot.handlers.survey import router as survey_router
from dairy_bot.middlewares.auth import AuthMiddleware
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.scheduler import recover_daily_deep_question, setup_scheduler
from dairy_bot.services.sheets_service import SheetsService
from dairy_bot.services.toc_service import reconcile_toc


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    settings = Settings()
    git_service = GitService(
        settings.journal_dir, enabled=settings.git_enabled, timezone=settings.timezone
    )
    sheets_service = SheetsService(
        enabled=settings.google_sheets_enabled,
        spreadsheet_id=settings.google_sheets_id,
        creds_file=settings.google_creds_file,
        timezone=settings.timezone,
    )
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["git_service"] = git_service
    dispatcher["sheets_service"] = sheets_service

    auth_middleware = AuthMiddleware(settings.allowed_user_id)
    dispatcher.message.middleware(auth_middleware)
    dispatcher.callback_query.middleware(auth_middleware)
    dispatcher.include_router(survey_router)
    dispatcher.include_router(deep_question_router)
    dispatcher.include_router(journal_router)

    scheduler = setup_scheduler(bot=bot, settings=settings, git_service=git_service)
    await bot.delete_webhook(drop_pending_updates=True)
    scheduler.start()
    await recover_daily_deep_question(
        scheduler=scheduler, bot=bot, settings=settings, git_service=git_service
    )

    if settings.toc_enabled:
        logger = logging.getLogger(__name__)
        logger.info("Running initial TOC indexing...")
        try:
            toc_paths = await reconcile_toc(settings.journal_dir, settings)
            if toc_paths:
                await asyncio.to_thread(git_service.commit_and_push, toc_paths)
                logger.info("Initial TOC indexing complete, %d files updated", len(toc_paths))
            else:
                logger.info("Initial TOC indexing complete, everything up to date")
        except Exception:
            logger.exception("Initial TOC indexing failed, will retry on next periodic scan")

    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

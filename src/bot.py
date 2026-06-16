import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from dairy_bot.config import Settings
from dairy_bot.handlers.journal import get_journal_lock, router as journal_router
from dairy_bot.middlewares.auth import AuthMiddleware
from dairy_bot.services.git_sync import GitPushError, GitService, GitSyncError
from dairy_bot.services.toc_service import reconcile_toc

logger = logging.getLogger(__name__)


async def _reconcile_toc_once(
    settings: Settings, git_service: GitService, label: str
) -> None:
    """Синхронизировать оглавление без конкуренции с записью дневника."""
    if not settings.toc_enabled:
        return

    async with get_journal_lock():
        try:
            await asyncio.to_thread(git_service.prepare_for_write)
            toc_paths = await reconcile_toc(settings.journal_dir, settings)
            if not toc_paths:
                logger.info("%s TOC indexing complete, everything up to date", label)
                return

            try:
                await asyncio.to_thread(git_service.commit_and_push, toc_paths)
            except GitPushError:
                logger.warning(
                    "%s TOC indexing saved locally, but push failed",
                    label,
                    exc_info=True,
                )
            logger.info("%s TOC indexing complete, %d files updated", label, len(toc_paths))
        except GitSyncError:
            logger.warning(
                "%s TOC indexing skipped because repo sync is blocked",
                label,
                exc_info=True,
            )
        except Exception:
            logger.exception("%s TOC indexing failed", label)


async def _periodic_toc_loop(settings: Settings, git_service: GitService) -> None:
    """Периодически переиндексировать ручные правки в vault."""
    interval_seconds = max(settings.toc_scan_interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval_seconds)
        await _reconcile_toc_once(settings, git_service, "Periodic")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    settings = Settings()
    git_service = GitService(
        settings.journal_dir, enabled=settings.git_enabled, timezone=settings.timezone
    )
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["git_service"] = git_service

    auth_middleware = AuthMiddleware(settings.allowed_user_id)
    dispatcher.message.middleware(auth_middleware)
    dispatcher.callback_query.middleware(auth_middleware)
    dispatcher.include_router(journal_router)

    await bot.delete_webhook(drop_pending_updates=True)

    toc_task: asyncio.Task[None] | None = None
    if settings.toc_enabled:
        logger.info("Running initial TOC indexing...")
        await _reconcile_toc_once(settings, git_service, "Initial")
        toc_task = asyncio.create_task(_periodic_toc_loop(settings, git_service))

    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        if toc_task:
            toc_task.cancel()
            try:
                await toc_task
            except asyncio.CancelledError:
                pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

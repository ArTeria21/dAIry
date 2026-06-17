import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from dairy_bot.config import Settings
from dairy_bot.handlers.journal import router as journal_router
from dairy_bot.middlewares.auth import AuthMiddleware
from dairy_bot.services.background_reconciler import (
    nightly_enrichment_loop,
    periodic_background_loop,
    reconcile_background_once,
    reconcile_changed_enrichment,
    reconcile_nightly_enrichment_once,
    start_background_reconciliation,
    stop_background_reconciliation,
)
from dairy_bot.services.enrichment_client import build_enrichment_client
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.toc_service import reconcile_toc


async def _reconcile_toc_once(
    settings: Settings, git_service: GitService, label: str
) -> None:
    """Backward-compatible wrapper for the combined background reconciler."""
    await _reconcile_background_once(settings, git_service, label)


async def _reconcile_background_once(
    settings: Settings,
    git_service: GitService,
    label: str,
    now=None,
) -> None:
    await reconcile_background_once(
        settings,
        git_service,
        label,
        now=now,
        client_factory=build_enrichment_client,
        reconcile_toc_func=reconcile_toc,
    )


async def _reconcile_changed_enrichment(
    settings: Settings,
    *,
    now=None,
) -> list[Path]:
    return await reconcile_changed_enrichment(
        settings,
        now=now,
        client_factory=build_enrichment_client,
    )


async def _reconcile_nightly_enrichment_once(settings: Settings) -> list[Path]:
    return await reconcile_nightly_enrichment_once(
        settings,
        client_factory=build_enrichment_client,
    )


async def _periodic_background_loop(settings: Settings, git_service: GitService) -> None:
    await periodic_background_loop(
        settings,
        git_service,
        client_factory=build_enrichment_client,
        reconcile_toc_func=reconcile_toc,
    )


async def _nightly_enrichment_loop(settings: Settings, git_service: GitService) -> None:
    await nightly_enrichment_loop(
        settings,
        git_service,
        client_factory=build_enrichment_client,
    )


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

    background_tasks = await start_background_reconciliation(settings, git_service)

    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await stop_background_reconciliation(background_tasks)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

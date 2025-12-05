import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from dairy_bot.config import Settings
from dairy_bot.handlers.journal import router as journal_router
from dairy_bot.middlewares.auth import AuthMiddleware
from dairy_bot.services.git_sync import GitService
from dairy_bot.services.scheduler import setup_scheduler


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

    scheduler = setup_scheduler(bot=bot, settings=settings)
    await bot.delete_webhook(drop_pending_updates=True)
    scheduler.start()

    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

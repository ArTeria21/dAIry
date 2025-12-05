from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from dairy_bot.services.language_store import get_language
from dairy_bot.texts import messages


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_id: int) -> None:
        super().__init__()
        self.allowed_user_id = allowed_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None) or data.get("event_from_user")
        if user and user.id != self.allowed_user_id:
            if isinstance(event, (Message, CallbackQuery)):
                lang = get_language(user.id)
                await event.answer(messages.t("unauthorized", lang))
            return None
        return await handler(event, data)

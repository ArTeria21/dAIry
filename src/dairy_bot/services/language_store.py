from __future__ import annotations

from typing import Dict

from dairy_bot.texts import DEFAULT_LANG, SUPPORTED_LANGS

_user_langs: Dict[int, str] = {}


def set_language(user_id: int, lang: str) -> None:
    """Persist the user's language selection in memory."""
    lang_code = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    _user_langs[user_id] = lang_code


def get_language(user_id: int) -> str:
    """Return the user's language or the default if none was set."""
    return _user_langs.get(user_id, DEFAULT_LANG)

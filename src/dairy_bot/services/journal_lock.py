from __future__ import annotations

import asyncio

_journal_lock: asyncio.Lock | None = None


def get_journal_lock() -> asyncio.Lock:
    global _journal_lock
    if _journal_lock is None:
        _journal_lock = asyncio.Lock()
    return _journal_lock

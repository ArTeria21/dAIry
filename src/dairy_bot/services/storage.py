import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiofiles

from dairy_bot.config import DEFAULT_TZ


def _now(moment: datetime | None = None, timezone: ZoneInfo | None = None) -> datetime:
    tz = timezone or DEFAULT_TZ
    return (moment or datetime.now(tz)).astimezone(tz)


def daily_note_path(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    current = _now(moment, timezone)
    return journal_dir / f"{current:%Y-%m-%d}.md"


async def append_entry(
    journal_dir: Path,
    content: str,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    content = content.strip()
    current = _now(moment, timezone)
    note_path = daily_note_path(journal_dir, current, timezone)
    note_path.parent.mkdir(parents=True, exist_ok=True)

    payload = f"## {current:%H:%M}\n\n{content}\n\n"
    async with aiofiles.open(note_path, "a", encoding="utf-8") as file:
        await file.write(payload)

    return note_path


async def note_has_content(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> bool:
    note_path = daily_note_path(journal_dir, moment, timezone)
    if not note_path.exists():
        return False
    stat_result = await asyncio.to_thread(note_path.stat)
    return stat_result.st_size > 0


async def read_daily_note(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> str:
    """Return the full text of the daily note or an empty string if missing."""
    note_path = daily_note_path(journal_dir, moment, timezone)
    try:
        async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
            return await file.read()
    except FileNotFoundError:
        return ""

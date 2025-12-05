import asyncio
from datetime import datetime
from pathlib import Path

import aiofiles

from dairy_bot.config import VIENNA_TZ


def _now(moment: datetime | None = None) -> datetime:
    return (moment or datetime.now(VIENNA_TZ)).astimezone(VIENNA_TZ)


def daily_note_path(journal_dir: Path, moment: datetime | None = None) -> Path:
    current = _now(moment)
    return journal_dir / f"{current:%Y-%m-%d}.md"


async def append_entry(
    journal_dir: Path, content: str, moment: datetime | None = None
) -> Path:
    content = content.strip()
    current = _now(moment)
    note_path = daily_note_path(journal_dir, current)
    note_path.parent.mkdir(parents=True, exist_ok=True)

    payload = f"## {current:%H:%M}\n\n{content}\n\n"
    async with aiofiles.open(note_path, "a", encoding="utf-8") as file:
        await file.write(payload)

    return note_path


async def note_has_content(journal_dir: Path, moment: datetime | None = None) -> bool:
    note_path = daily_note_path(journal_dir, moment)
    if not note_path.exists():
        return False
    stat_result = await asyncio.to_thread(note_path.stat)
    return stat_result.st_size > 0

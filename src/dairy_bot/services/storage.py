import asyncio
from datetime import datetime, timedelta
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


def _build_nav_line(prev_label: str | None, next_label: str | None) -> str:
    links: list[str] = []
    if prev_label:
        links.append(f"[[{prev_label}|Prev day]]")
    if next_label:
        links.append(f"[[{next_label}|Next day]]")
    return " · ".join(links)


async def _write_template(note_path: Path, date_label: str, nav_line: str) -> None:
    note_path.parent.mkdir(parents=True, exist_ok=True)
    template = f"# {date_label}\n"
    if nav_line:
        template += f"{nav_line}\n"
    else:
        template += "\n"
    template += "\n"
    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(template)


async def _upsert_nav_line(note_path: Path, nav_line: str) -> None:
    """Replace or insert the nav line (second line) without touching content."""
    if not note_path.exists():
        return
    lines: list[str] = []
    async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
        lines = await file.readlines()

    if not lines:
        return

    if len(lines) == 1:
        lines.append("")

    # Ensure there's a blank separator after nav line
    if len(lines) == 2:
        lines.append("")

    lines[1] = f"{nav_line}\n" if nav_line else "\n"

    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.writelines(lines)


async def _ensure_daily_template(
    journal_dir: Path, note_path: Path, current: datetime
) -> None:
    """Create the daily file with nav links if it's empty or missing."""
    note_path.parent.mkdir(parents=True, exist_ok=True)
    needs_template = not note_path.exists()
    if not needs_template:
        stat_result = await asyncio.to_thread(note_path.stat)
        needs_template = stat_result.st_size == 0
    if not needs_template:
        return

    date_label = f"{current:%Y-%m-%d}"
    prev_date = current - timedelta(days=1)
    next_date = current + timedelta(days=1)
    prev_label = (
        f"{prev_date:%Y-%m-%d}"
        if daily_note_path(journal_dir, prev_date).exists()
        else None
    )
    next_label = (
        f"{next_date:%Y-%m-%d}"
        if daily_note_path(journal_dir, next_date).exists()
        else None
    )
    nav_line = _build_nav_line(prev_label, next_label)
    await _write_template(note_path, date_label, nav_line)


async def _update_neighbor_nav(journal_dir: Path, current: datetime) -> None:
    """When a day appears, add missing next/prev links for neighbors."""
    prev_date = current - timedelta(days=1)
    next_date = current + timedelta(days=1)
    current_label = f"{current:%Y-%m-%d}"

    # Update previous day to point to current
    prev_path = daily_note_path(journal_dir, prev_date)
    if prev_path.exists():
        prev_prev_label = (
            f"{(prev_date - timedelta(days=1)):%Y-%m-%d}"
            if daily_note_path(journal_dir, prev_date - timedelta(days=1)).exists()
            else None
        )
        nav_line = _build_nav_line(prev_prev_label, current_label)
        await _upsert_nav_line(prev_path, nav_line)

    # Update next day (if it already exists) to point back to current
    next_path = daily_note_path(journal_dir, next_date)
    if next_path.exists():
        next_next_label = (
            f"{(next_date + timedelta(days=1)):%Y-%m-%d}"
            if daily_note_path(journal_dir, next_date + timedelta(days=1)).exists()
            else None
        )
        nav_line = _build_nav_line(current_label, next_next_label)
        await _upsert_nav_line(next_path, nav_line)


async def append_entry(
    journal_dir: Path,
    content: str,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    content = content.strip()
    current = _now(moment, timezone)
    note_path = daily_note_path(journal_dir, current, timezone)
    await _ensure_daily_template(journal_dir, note_path, current)
    await _update_neighbor_nav(journal_dir, current)

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

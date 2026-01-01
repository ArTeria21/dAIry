import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiofiles

from dairy_bot.config import DEFAULT_TZ

DATE_HEADER_RE = re.compile(r"^#\s+\d{4}-\d{2}-\d{2}\s*$")


def _now(moment: datetime | None = None, timezone: ZoneInfo | None = None) -> datetime:
    tz = timezone or DEFAULT_TZ
    return (moment or datetime.now(tz)).astimezone(tz)


def daily_note_path(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    current = _now(moment, timezone)
    return journal_dir / f"{current:%Y}" / f"{current:%m}" / f"{current:%Y-%m-%d}.md"


def _looks_like_date_header(line: str) -> bool:
    return bool(DATE_HEADER_RE.match(line.strip()))


def _looks_like_nav_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if "[[" not in stripped or "]]" not in stripped:
        return False
    return "Prev day" in stripped or "Next day" in stripped


def _strip_frontmatter(lines: list[str]) -> list[str]:
    if not lines or lines[0].strip() != "---":
        return lines
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[index + 1 :]
    return lines


def _has_real_content(text: str) -> bool:
    lines = _strip_frontmatter(text.splitlines())
    if not lines:
        return False
    first_non_empty = None
    for index, line in enumerate(lines):
        if line.strip():
            first_non_empty = index
            break
    if first_non_empty is None:
        return False

    if not _looks_like_date_header(lines[first_non_empty]):
        return True

    index = first_non_empty + 1
    if index < len(lines) and _looks_like_nav_line(lines[index]):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return any(line.strip() for line in lines[index:])


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

    if not _looks_like_date_header(lines[0]):
        return

    if len(lines) == 1:
        lines.append("")

    if len(lines) > 1 and not _looks_like_nav_line(lines[1]):
        return

    # Ensure there's a blank separator after nav line
    if len(lines) == 2:
        lines.append("")

    lines[1] = f"{nav_line}\n" if nav_line else "\n"

    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.writelines(lines)


async def _find_nearest_existing_date(
    journal_dir: Path, start_date: datetime, direction: int = -1
) -> datetime | None:
    """Find the nearest existing daily note in the given direction (-1 for past, +1 for future)."""
    current_date = start_date
    max_days = 3650  # Search up to 10 years
    for _ in range(max_days):
        current_date += timedelta(days=direction)
        if daily_note_path(journal_dir, current_date).exists():
            return current_date
    return None


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
    prev_date = await _find_nearest_existing_date(journal_dir, current, direction=-1)
    next_date = await _find_nearest_existing_date(journal_dir, current, direction=1)
    prev_label = f"{prev_date:%Y-%m-%d}" if prev_date else None
    next_label = f"{next_date:%Y-%m-%d}" if next_date else None
    nav_line = _build_nav_line(prev_label, next_label)
    await _write_template(note_path, date_label, nav_line)


async def _update_neighbor_nav(journal_dir: Path, current: datetime) -> None:
    """When a day appears, update nav links for nearest existing neighbors."""
    current_label = f"{current:%Y-%m-%d}"

    # Find and update the previous existing day to point to current
    prev_date = await _find_nearest_existing_date(journal_dir, current, direction=-1)
    if prev_date:
        prev_path = daily_note_path(journal_dir, prev_date)
        prev_prev_label = (
            f"{(await _find_nearest_existing_date(journal_dir, prev_date, direction=-1)):%Y-%m-%d}"
            if await _find_nearest_existing_date(journal_dir, prev_date, direction=-1)
            else None
        )
        nav_line = _build_nav_line(prev_prev_label, current_label)
        await _upsert_nav_line(prev_path, nav_line)

    # Find and update the next existing day (if it already exists) to point back to current
    next_date = await _find_nearest_existing_date(journal_dir, current, direction=1)
    if next_date:
        next_path = daily_note_path(journal_dir, next_date)
        next_next_label = (
            f"{(await _find_nearest_existing_date(journal_dir, next_date, direction=1)):%Y-%m-%d}"
            if await _find_nearest_existing_date(journal_dir, next_date, direction=1)
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
    try:
        async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
            content = await file.read()
    except FileNotFoundError:
        return False
    return _has_real_content(content)


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

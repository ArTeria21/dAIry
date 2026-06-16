import asyncio
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiofiles

from dairy_bot.config import DEFAULT_TZ

DATE_HEADER_RE = re.compile(r"^#\s+\d{4}-\d{2}-\d{2}\s*$")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _now(moment: datetime | None = None, timezone: ZoneInfo | None = None) -> datetime:
    tz = timezone or DEFAULT_TZ
    return (moment or datetime.now(tz)).astimezone(tz)


def _target_datetime(
    current: datetime, target_date: date | datetime | None, timezone: ZoneInfo
) -> datetime:
    if target_date is None:
        return current
    if isinstance(target_date, datetime):
        return target_date.astimezone(timezone)
    return datetime.combine(target_date, time.min, tzinfo=timezone)


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


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[: index + 1]), "".join(lines[index + 1 :])
    return "", text


def _strip_frontmatter(text: str) -> str:
    _, body = _split_frontmatter(text)
    return body


def _has_real_content(text: str) -> bool:
    body = _strip_frontmatter(text)
    lines = body.splitlines()
    if not lines:
        return False

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and _looks_like_date_header(lines[index]):
        index += 1
    if index < len(lines) and _looks_like_nav_line(lines[index]):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1

    return any(line.strip() for line in lines[index:])


def _build_frontmatter(date_label: str) -> str:
    return f"---\ndate: {date_label}\ntype: daily\n---\n"


def _build_nav_line(prev_label: str | None, next_label: str | None) -> str:
    links: list[str] = []
    if prev_label:
        links.append(f"[[{prev_label}|Prev day]]")
    if next_label:
        links.append(f"[[{next_label}|Next day]]")
    return " · ".join(links)


def _entry_heading(current: datetime, target: datetime) -> str:
    if current.date() == target.date():
        return f"## {current:%H:%M}"
    month_name = MONTH_NAMES[current.month - 1]
    return f"## {month_name} {current.day} {current:%H:%M}"


async def _read_text(path: Path) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8") as file:
        return await file.read()


async def _path_has_real_content(path: Path) -> bool:
    try:
        return _has_real_content(await _read_text(path))
    except FileNotFoundError:
        return False


async def _write_template(note_path: Path, date_label: str, nav_line: str) -> None:
    note_path.parent.mkdir(parents=True, exist_ok=True)
    content = _build_frontmatter(date_label)
    content += f"# {date_label}\n"
    content += f"{nav_line}\n" if nav_line else "\n"
    content += "\n"
    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(content)


async def _find_nearest_existing_date(
    journal_dir: Path,
    start_date: datetime,
    direction: int,
    timezone: ZoneInfo | None = None,
) -> datetime | None:
    current_date = start_date
    for _ in range(3650):
        current_date += timedelta(days=direction)
        candidate_path = daily_note_path(journal_dir, current_date, timezone)
        if candidate_path.exists() and await _path_has_real_content(candidate_path):
            return current_date
    return None


async def _ensure_daily_template(
    journal_dir: Path,
    note_path: Path,
    current: datetime,
    timezone: ZoneInfo | None = None,
) -> None:
    needs_template = not note_path.exists()
    if not needs_template:
        stat_result = await asyncio.to_thread(note_path.stat)
        needs_template = stat_result.st_size == 0
    if not needs_template:
        return

    date_label = f"{current:%Y-%m-%d}"
    prev_date = await _find_nearest_existing_date(journal_dir, current, -1, timezone)
    next_date = await _find_nearest_existing_date(journal_dir, current, 1, timezone)
    prev_label = f"{prev_date:%Y-%m-%d}" if prev_date else None
    next_label = f"{next_date:%Y-%m-%d}" if next_date else None
    await _write_template(note_path, date_label, _build_nav_line(prev_label, next_label))


async def _upsert_nav_line(note_path: Path, nav_line: str) -> None:
    if not note_path.exists():
        return

    content = await _read_text(note_path)
    frontmatter, body = _split_frontmatter(content)
    lines = body.splitlines(keepends=True)
    if not lines:
        return

    header_idx = None
    for index, line in enumerate(lines):
        if _looks_like_date_header(line):
            header_idx = index
            break
    if header_idx is None:
        return

    nav_idx = header_idx + 1
    nav_text = f"{nav_line}\n" if nav_line else "\n"
    if nav_idx >= len(lines):
        lines.append(nav_text)
    elif _looks_like_nav_line(lines[nav_idx]):
        lines[nav_idx] = nav_text
    else:
        lines.insert(nav_idx, nav_text)

    if nav_idx + 1 >= len(lines):
        lines.append("\n")
    elif lines[nav_idx + 1].strip():
        lines.insert(nav_idx + 1, "\n")

    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(frontmatter + "".join(lines))


async def _refresh_nav_for_date(
    journal_dir: Path,
    target_date: datetime,
    timezone: ZoneInfo | None = None,
) -> None:
    note_path = daily_note_path(journal_dir, target_date, timezone)
    if not note_path.exists():
        return

    prev_date = await _find_nearest_existing_date(journal_dir, target_date, -1, timezone)
    next_date = await _find_nearest_existing_date(journal_dir, target_date, 1, timezone)
    prev_label = f"{prev_date:%Y-%m-%d}" if prev_date else None
    next_label = f"{next_date:%Y-%m-%d}" if next_date else None
    await _upsert_nav_line(note_path, _build_nav_line(prev_label, next_label))


async def _refresh_neighbor_nav(
    journal_dir: Path,
    current: datetime,
    timezone: ZoneInfo | None = None,
) -> None:
    prev_date = await _find_nearest_existing_date(journal_dir, current, -1, timezone)
    next_date = await _find_nearest_existing_date(journal_dir, current, 1, timezone)

    await _refresh_nav_for_date(journal_dir, current, timezone)
    if prev_date:
        await _refresh_nav_for_date(journal_dir, prev_date, timezone)
    if next_date:
        await _refresh_nav_for_date(journal_dir, next_date, timezone)


async def append_entry(
    journal_dir: Path,
    content: str,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
    target_date: date | datetime | None = None,
) -> Path:
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("Cannot append an empty journal entry")

    tz = timezone or DEFAULT_TZ
    current = _now(moment, tz)
    target = _target_datetime(current, target_date, tz)
    note_path = daily_note_path(journal_dir, target, tz)
    await _ensure_daily_template(journal_dir, note_path, target, tz)

    payload = f"{_entry_heading(current, target)}\n\n{normalized_content}\n\n"
    async with aiofiles.open(note_path, "a", encoding="utf-8") as file:
        await file.write(payload)

    await _refresh_neighbor_nav(journal_dir, target, tz)
    return note_path


async def note_has_content(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> bool:
    note_path = daily_note_path(journal_dir, moment, timezone)
    if not note_path.exists():
        return False
    return await _path_has_real_content(note_path)


async def read_daily_note(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> str:
    """Return the full daily note text, or an empty string when it is missing."""
    note_path = daily_note_path(journal_dir, moment, timezone)
    try:
        return await _read_text(note_path)
    except FileNotFoundError:
        return ""


def _strip_note_template(text: str) -> str:
    """Remove frontmatter, date heading, and navigation from a daily note."""
    body = _strip_frontmatter(text)
    lines = body.splitlines()
    if not lines:
        return ""

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and _looks_like_date_header(lines[index]):
        index += 1
    if index < len(lines) and _looks_like_nav_line(lines[index]):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1

    return "\n".join(lines[index:]).strip()


async def read_daily_note_entries(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> str:
    """Return only day entries without the service template."""
    content = await read_daily_note(journal_dir, moment=moment, timezone=timezone)
    if not content:
        return ""
    return _strip_note_template(content)

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiofiles
import yaml

from dairy_bot.config import DEFAULT_TZ

# Default structure for survey data in YAML frontmatter
DEFAULT_SURVEY_DATA: dict[str, Any] = {
    # Evening survey
    "mood_evening": None,
    "energy": None,
    "anxiety": None,
    "focus": None,
    "cravings": None,
    "sport": None,
    "habits": {
        "steps_8k": None,
        "zero_spending": None,
        "english_words": None,
        "supplements": None,
        "tea_time": None,
        "no_junk_food": None,
        "no_eating_out": None,
        "reading": None,
    },
    # Morning survey
    "mood_morning": None,
    "sleep_duration": None,
    "sleep_score": None,
    "bedtime": None,
    "wake_time": None,
}

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


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from text, return (data, rest_of_content)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, text

    yaml_content = "".join(lines[1:end_index])
    rest_content = "".join(lines[end_index + 1 :])

    try:
        data = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError:
        data = {}

    return data, rest_content


def _build_frontmatter(data: dict[str, Any]) -> str:
    """Build YAML frontmatter string from data."""
    if not data:
        return ""
    yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n"


def _merge_survey_data(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge survey updates into existing data, preserving structure."""
    result = {}
    # Start with default structure to ensure all fields exist
    for key, default_value in DEFAULT_SURVEY_DATA.items():
        if isinstance(default_value, dict):
            result[key] = dict(default_value)
        else:
            result[key] = default_value

    # Apply existing values
    for key, value in existing.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key].update(value)
        else:
            result[key] = value

    # Apply updates
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key].update(value)
        else:
            result[key] = value

    return result


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
    # Start with frontmatter
    frontmatter = _build_frontmatter(dict(DEFAULT_SURVEY_DATA))
    template = frontmatter
    template += f"# {date_label}\n"
    if nav_line:
        template += f"{nav_line}\n"
    else:
        template += "\n"
    template += "\n"
    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(template)


async def _upsert_nav_line(note_path: Path, nav_line: str) -> None:
    """Replace or insert the nav line (line after date header) without touching content."""
    if not note_path.exists():
        return

    async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
        content = await file.read()

    # Parse frontmatter and content separately
    frontmatter_data, rest_content = _parse_frontmatter(content)
    lines = rest_content.splitlines(keepends=True)

    if not lines:
        return

    # Find date header line
    header_idx = None
    for idx, line in enumerate(lines):
        if _looks_like_date_header(line):
            header_idx = idx
            break

    if header_idx is None:
        return

    nav_idx = header_idx + 1
    if nav_idx >= len(lines):
        lines.append("")

    if not _looks_like_nav_line(lines[nav_idx]):
        return

    # Ensure there's a blank separator after nav line
    if nav_idx + 1 >= len(lines):
        lines.append("")

    lines[nav_idx] = f"{nav_line}\n" if nav_line else "\n"

    # Rebuild content
    new_frontmatter = _build_frontmatter(frontmatter_data) if frontmatter_data else ""
    new_content = new_frontmatter + "".join(lines)

    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(new_content)


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


async def get_survey_data(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> dict[str, Any]:
    """Get survey data from YAML frontmatter for a specific date."""
    note_path = daily_note_path(journal_dir, moment, timezone)
    if not note_path.exists():
        return dict(DEFAULT_SURVEY_DATA)

    try:
        async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
            content = await file.read()
    except FileNotFoundError:
        return dict(DEFAULT_SURVEY_DATA)

    data, _ = _parse_frontmatter(content)
    return _merge_survey_data({}, data)


async def save_survey_data(
    journal_dir: Path,
    updates: dict[str, Any],
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    """Save survey data to YAML frontmatter, merging with existing data."""
    current = _now(moment, timezone)
    note_path = daily_note_path(journal_dir, current, timezone)

    # Ensure the note exists
    await _ensure_daily_template(journal_dir, note_path, current)
    await _update_neighbor_nav(journal_dir, current)

    # Read existing content
    try:
        async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
            content = await file.read()
    except FileNotFoundError:
        content = ""

    # Parse existing frontmatter and merge
    existing_data, rest_content = _parse_frontmatter(content)
    merged_data = _merge_survey_data(existing_data, updates)

    # Build new content
    new_frontmatter = _build_frontmatter(merged_data)
    new_content = new_frontmatter + rest_content

    # Write back
    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(new_content)

    return note_path


def is_evening_survey_filled(data: dict[str, Any]) -> bool:
    """Check if evening survey has been filled (at least mood_evening is set)."""
    return data.get("mood_evening") is not None


def is_morning_survey_filled(data: dict[str, Any]) -> bool:
    """Check if morning survey has been filled (at least mood_morning is set)."""
    return data.get("mood_morning") is not None

import asyncio
import random
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
    # Mood & Mental state
    "mood_morning": None,
    "mood_evening": None,
    "energy": None,
    "anxiety": None,
    "focus": None,
    # Sleep
    "sleep_duration": None,
    "sleep_score": None,
    "bedtime": None,
    "wake_time": None,
    # Food & Cravings
    "cravings": None,
    # Physical activity
    "sport": None,
    # Weather data
    "weather": {
        "city": None,
        "temperature_max": None,
        "pressure": None,
        "cloud_cover": None,
        "uv_index": None,
    },
    # Habits (grouped thematically)
    "habits": {
        # Food
        "no_junk_food": None,
        "no_eating_out": None,
        # Physical
        "steps_8k": None,
        # Routine
        "supplements": None,
        "tea_time": None,
        "english_words": None,
        "zero_spending": None,
        "reading": None,
    },
}

DATE_HEADER_RE = re.compile(r"^#\s+\d{4}-\d{2}-\d{2}\s*$")
SECTION_HEADER_RE = re.compile(r"^##\s+\d{2}:\d{2}\s*$")
QUESTION_ID_RE = re.compile(r"^Question ID:\s*(.+?)\s*$")
SOURCE_RE = re.compile(r"^Source:\s*(.+?)\s*$")
DEEP_QUESTION_MARKER = "**Deep Question**"
DEEP_ANSWER_MARKER = "**Deep Answer**"
DAILY_DEEP_QUESTION_KEY = "deep_question_daily_sent"


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


def _normalize_block_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


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


def _parse_deep_blocks(text: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return parsed question blocks and answer payloads, supporting old and new formats."""
    lines = _strip_frontmatter(text.splitlines())
    questions: list[dict[str, str]] = []
    answers: list[dict[str, str]] = []

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line != DEEP_QUESTION_MARKER:
            if line == DEEP_ANSWER_MARKER:
                cursor = index + 1
                while cursor < len(lines):
                    meta_line = lines[cursor].strip()
                    if QUESTION_ID_RE.match(meta_line):
                        cursor += 1
                        continue
                    if not meta_line:
                        cursor += 1
                        break
                    break
                answer_lines: list[str] = []
                while cursor < len(lines) and not SECTION_HEADER_RE.match(lines[cursor].strip()):
                    answer_lines.append(lines[cursor])
                    cursor += 1
                answer_text = "\n".join(answer_lines).strip()
                if answer_text:
                    answers.append({"text": answer_text})
                index = cursor
                continue
            index += 1
            continue

        cursor = index + 1
        source = ""
        while cursor < len(lines):
            meta_line = lines[cursor].strip()
            if QUESTION_ID_RE.match(meta_line):
                cursor += 1
                continue
            source_match = SOURCE_RE.match(meta_line)
            if source_match:
                source = source_match.group(1).strip()
                cursor += 1
                continue
            if not meta_line:
                cursor += 1
                break
            break

        question_lines: list[str] = []
        answer_lines: list[str] = []
        in_answer = False
        while cursor < len(lines):
            current = lines[cursor]
            stripped = current.strip()
            if SECTION_HEADER_RE.match(stripped):
                break
            if stripped == DEEP_ANSWER_MARKER:
                in_answer = True
                cursor += 1
                while cursor < len(lines) and QUESTION_ID_RE.match(lines[cursor].strip()):
                    cursor += 1
                if cursor < len(lines) and not lines[cursor].strip():
                    cursor += 1
                continue
            if in_answer:
                answer_lines.append(current)
            else:
                question_lines.append(current)
            cursor += 1

        question_text = "\n".join(question_lines).strip()
        answer_text = "\n".join(answer_lines).strip()
        payload = {"source": source, "text": question_text, "answer": answer_text}
        if question_text:
            questions.append(payload)
        if answer_text:
            answers.append({"text": answer_text})
        index = cursor

    return questions, answers


def _remove_deep_blocks(text: str) -> str:
    """Remove deep question/answer blocks to detect normal journal content."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return text

    result: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped not in {DEEP_QUESTION_MARKER, DEEP_ANSWER_MARKER}:
            result.append(lines[index])
            index += 1
            continue

        # Drop marker + metadata lines.
        index += 1
        while index < len(lines) and lines[index].strip():
            index += 1
        # Skip one optional separator line.
        if index < len(lines) and not lines[index].strip():
            index += 1
        # Drop content until next timestamp section.
        while index < len(lines):
            if SECTION_HEADER_RE.match(lines[index].strip()):
                break
            index += 1
    return "".join(result)


def _find_question_block_range(
    lines: list[str], question_text: str
) -> tuple[int, int, bool] | None:
    target = _normalize_block_text(question_text)
    match_with_answer: tuple[int, int, bool] | None = None

    index = 0
    while index < len(lines):
        if lines[index].strip() != DEEP_QUESTION_MARKER:
            index += 1
            continue

        cursor = index + 1
        while cursor < len(lines):
            meta_line = lines[cursor].strip()
            if QUESTION_ID_RE.match(meta_line) or SOURCE_RE.match(meta_line):
                cursor += 1
                continue
            if not meta_line:
                cursor += 1
                break
            break

        question_lines: list[str] = []
        has_answer = False
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if SECTION_HEADER_RE.match(stripped):
                break
            if stripped == DEEP_ANSWER_MARKER:
                has_answer = True
                break
            question_lines.append(lines[cursor])
            cursor += 1

        end_idx = cursor
        while end_idx < len(lines) and not SECTION_HEADER_RE.match(lines[end_idx].strip()):
            end_idx += 1

        parsed_question = _normalize_block_text("".join(question_lines))
        if parsed_question == target:
            candidate = (index, end_idx, has_answer)
            if not has_answer:
                match_with_answer = candidate
            elif match_with_answer is None:
                match_with_answer = candidate
        index = end_idx if end_idx > index else index + 1

    return match_with_answer


async def append_deep_question(
    journal_dir: Path,
    question: str,
    source: str,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    current = _now(moment, timezone)
    note_path = daily_note_path(journal_dir, current, timezone)
    await _ensure_daily_template(journal_dir, note_path, current)
    await _update_neighbor_nav(journal_dir, current)

    payload = (
        f"## {current:%H:%M}\n\n"
        f"{DEEP_QUESTION_MARKER}\n"
        "\n"
        f"{question.strip()}\n\n"
    )
    async with aiofiles.open(note_path, "a", encoding="utf-8") as file:
        await file.write(payload)
    if source == "daily":
        try:
            async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
                content = await file.read()
        except FileNotFoundError:
            content = ""
        existing_data, rest_content = _parse_frontmatter(content)
        existing_data[DAILY_DEEP_QUESTION_KEY] = True
        new_content = _build_frontmatter(existing_data) + rest_content
        async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
            await file.write(new_content)
    return note_path


async def append_deep_answer(
    journal_dir: Path,
    answer: str,
    question_text: str,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> Path:
    current = _now(moment, timezone)
    note_path = daily_note_path(journal_dir, current, timezone)
    await _ensure_daily_template(journal_dir, note_path, current)
    await _update_neighbor_nav(journal_dir, current)

    try:
        async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
            content = await file.read()
    except FileNotFoundError:
        content = ""

    frontmatter_data, rest_content = _parse_frontmatter(content)
    lines = rest_content.splitlines(keepends=True)
    block_range = _find_question_block_range(lines, question_text)
    normalized_answer = answer.strip()

    if block_range is None:
        payload = (
            f"## {current:%H:%M}\n\n"
            f"{DEEP_QUESTION_MARKER}\n\n"
            f"{question_text.strip()}\n\n"
            f"{DEEP_ANSWER_MARKER}\n\n"
            f"{normalized_answer}\n\n"
        )
        async with aiofiles.open(note_path, "a", encoding="utf-8") as file:
            await file.write(payload)
        return note_path

    _, end_idx, has_answer = block_range
    insertion_lines: list[str]
    if has_answer:
        insertion_lines = [f"\n{normalized_answer}\n\n"]
    else:
        insertion_lines = [f"\n{DEEP_ANSWER_MARKER}\n\n{normalized_answer}\n\n"]
    lines[end_idx:end_idx] = insertion_lines
    new_frontmatter = _build_frontmatter(frontmatter_data) if frontmatter_data else ""
    new_content = new_frontmatter + "".join(lines)
    async with aiofiles.open(note_path, "w", encoding="utf-8") as file:
        await file.write(new_content)
    return note_path


async def list_recent_deep_questions(journal_dir: Path, limit: int = 15) -> list[str]:
    note_paths = sorted(
        [path for path in journal_dir.rglob("*.md") if path.is_file()],
        reverse=True,
    )
    questions: list[str] = []
    for note_path in note_paths:
        try:
            async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
                content = await file.read()
        except FileNotFoundError:
            continue
        parsed_questions, _ = _parse_deep_blocks(content)
        for item in reversed(parsed_questions):
            if item["text"]:
                questions.append(item["text"])
            if len(questions) >= limit:
                return questions[:limit]
    return questions[:limit]


async def pick_random_substantive_note(
    journal_dir: Path,
    timezone: ZoneInfo | None = None,
) -> str | None:
    tz = timezone or DEFAULT_TZ
    today_path = daily_note_path(journal_dir, timezone=tz)
    candidates: list[Path] = []
    for note_path in journal_dir.rglob("*.md"):
        if not note_path.is_file() or note_path == today_path:
            continue
        candidates.append(note_path)
    random.shuffle(candidates)

    for note_path in candidates:
        try:
            async with aiofiles.open(note_path, "r", encoding="utf-8") as file:
                content = await file.read()
        except FileNotFoundError:
            continue
        cleaned = _remove_deep_blocks(content)
        if _has_real_content(cleaned):
            return cleaned.strip()
    return None


async def count_deep_answers_for_day(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> int:
    content = await read_daily_note(journal_dir, moment=moment, timezone=timezone)
    if not content:
        return 0
    questions, answers = _parse_deep_blocks(content)
    nested_count = sum(1 for question in questions if question.get("answer"))
    legacy_count = sum(1 for answer in answers if answer.get("text"))
    return max(nested_count, legacy_count)


async def day_has_daily_question_sent(
    journal_dir: Path,
    moment: datetime | None = None,
    timezone: ZoneInfo | None = None,
) -> bool:
    content = await read_daily_note(journal_dir, moment=moment, timezone=timezone)
    if not content:
        return False
    data, _ = _parse_frontmatter(content)
    if data.get(DAILY_DEEP_QUESTION_KEY) is True:
        return True
    questions, _ = _parse_deep_blocks(content)
    return any(question.get("source") == "daily" for question in questions)


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


def _strip_note_template(text: str) -> str:
    """Remove frontmatter, date header, and nav line from a daily note."""
    lines = _strip_frontmatter(text.splitlines())
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
    """Return only the journal body entries without frontmatter/template header."""
    content = await read_daily_note(journal_dir, moment=moment, timezone=timezone)
    if not content:
        return ""
    return _strip_note_template(content)


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

import calendar
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles
import yaml
from openai import AsyncOpenAI

from dairy_bot.config import Settings, language_display_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag taxonomy: a fixed English vocabulary for the LLM.
# ---------------------------------------------------------------------------

TAG_TAXONOMY: dict[str, str] = {
    "anxiety": "Worry, nervousness, panic, overthinking",
    "creativity": "Art, writing, music, creative projects, ideas",
    "decision_making": "Choices, dilemmas, trade-offs, weighing options",
    "emotions": "Emotional states, feelings, mood descriptions",
    "energy": "Energy levels, fatigue, vitality",
    "entertainment": "Movies, games, media, hobbies, leisure activities",
    "family": "Family members, family dynamics, home life",
    "fitness": "Exercise, sports, physical activity",
    "gratitude": "Thankfulness, appreciation, counting blessings",
    "habits": "Habit tracking, building or breaking habits, discipline",
    "health": "Physical health, illness, medical topics, body",
    "identity": "Self-concept, values, personal growth, who I am",
    "learning": "Education, studying, new skills, books, reading",
    "money": "Finances, spending, saving, budgeting",
    "nature": "Weather, outdoors, environment, seasons",
    "nutrition": "Food, diet, eating habits, cooking",
    "planning": "Goals, plans, scheduling, future thinking",
    "productivity": "Focus, time management, getting things done",
    "reflection": "Self-analysis, introspection, looking back at experiences",
    "relationships": "Friendships, romantic relationships, social bonds",
    "routine": "Daily habits, rituals, structure, mundane activities",
    "sleep": "Sleep quality, insomnia, dreams, rest patterns",
    "social": "Social events, gatherings, community interactions",
    "spirituality": "Meaning, purpose, mindfulness, meditation, philosophy",
    "stress": "Pressure, overwhelm, burnout, tension",
    "technology": "Software, gadgets, digital life, AI/ML, tech projects",
    "therapy": "Therapy sessions, psychological work, mental health treatment",
    "travel": "Trips, commuting, new places, relocation",
    "work": "Professional tasks, career, job-related topics",
}

ALLOWED_TAG_SET = frozenset(TAG_TAXONOMY)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOC_STATE_FILENAME = ".toc_index.json"
DAILY_NOTE_PATTERN = re.compile(r"^(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.md$")
_DATE_HEADER_RE = re.compile(r"^#\s+\d{4}-\d{2}-\d{2}\s*$")
MAX_NOTE_CONTENT_FOR_LLM = 8000
TOC_SUMMARY_ATTEMPTS = 2
TOC_SUMMARY_MAX_TOKENS = 500
_MONTH_NAMES = {i: calendar.month_name[i] for i in range(1, 13)}


class TocLLMResponseError(ValueError):
    """Raised when the TOC LLM response cannot be used."""

# ---------------------------------------------------------------------------
# Frontmatter helpers without a hard dependency on storage.py
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break
    if end_index is None:
        return {}, text
    yaml_content = "".join(lines[1:end_index])
    rest = "".join(lines[end_index + 1 :])
    try:
        data = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError:
        data = {}
    return data, rest


def _strip_frontmatter_text(text: str) -> str:
    _, body = _parse_frontmatter(text)
    return body


# ---------------------------------------------------------------------------
# Indexable content detection
# ---------------------------------------------------------------------------


def _looks_like_nav_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if "[[" not in stripped or "]]" not in stripped:
        return False
    return "Prev day" in stripped or "Next day" in stripped


def _is_daily_note(rel_path: str) -> bool:
    return bool(DAILY_NOTE_PATTERN.match(rel_path))


def _has_indexable_content(text: str, is_daily: bool) -> bool:
    body = _strip_frontmatter_text(text)
    if not body.strip():
        return False
    if not is_daily:
        return True
    lines = body.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines) and _DATE_HEADER_RE.match(lines[idx].strip()):
        idx += 1
    if idx < len(lines) and _looks_like_nav_line(lines[idx]):
        idx += 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return any(line.strip() for line in lines[idx:])


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Note cleanup before LLM processing
# ---------------------------------------------------------------------------


def _clean_for_llm(text: str, is_daily: bool) -> str:
    body = _strip_frontmatter_text(text)
    if is_daily:
        lines = body.splitlines()
        idx = 0
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx < len(lines) and _DATE_HEADER_RE.match(lines[idx].strip()):
            idx += 1
        if idx < len(lines) and _looks_like_nav_line(lines[idx]):
            idx += 1
        body = "\n".join(lines[idx:])
    body = body.strip()
    if len(body) > MAX_NOTE_CONTENT_FOR_LLM:
        body = body[:MAX_NOTE_CONTENT_FOR_LLM] + "\n[truncated]"
    return body


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def _discover_files(
    journal_dir: Path,
    extra_dirs: list[str],
    toc_filename: str,
) -> list[Path]:
    excluded_names = {toc_filename, TOC_STATE_FILENAME}
    found: set[Path] = set()

    for md_path in journal_dir.rglob("*.md"):
        if not md_path.is_file() or md_path.name in excluded_names:
            continue
        rel = str(md_path.relative_to(journal_dir))
        if _is_daily_note(rel):
            found.add(md_path)
            continue
        for extra_dir in extra_dirs:
            normalized = extra_dir.rstrip("/")
            if rel == normalized or rel.startswith(normalized + "/"):
                found.add(md_path)
                break

    return list(found)


# ---------------------------------------------------------------------------
# Index state storage
# ---------------------------------------------------------------------------


async def _load_state(journal_dir: Path) -> dict[str, Any]:
    state_path = journal_dir / TOC_STATE_FILENAME
    if not state_path.exists():
        return {}
    try:
        async with aiofiles.open(state_path, "r", encoding="utf-8") as f:
            return json.loads(await f.read())
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load TOC state, starting fresh")
        return {}


async def _save_state(journal_dir: Path, state: dict[str, Any]) -> Path:
    state_path = journal_dir / TOC_STATE_FILENAME
    async with aiofiles.open(state_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(state, indent=2, ensure_ascii=False))
    return state_path


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------


def _build_system_prompt(max_tags: int, language: str = "EN") -> str:
    output_language = language_display_name(language)
    tag_list = "\n".join(
        f"- {tag}: {desc}" for tag, desc in sorted(TAG_TAXONOMY.items())
    )
    return (
        "You are an indexer for a personal journal vault. "
        f"Produce a concise {output_language} summary and select relevant tags "
        "for a note.\n\n"
        "The journal may be written in Russian, English, German, or a mix. "
        f"Always write the summary in {output_language} regardless of source "
        "language.\n\n"
        "RULES:\n"
        "- summary: 1-2 sentences, third-person, factual. "
        "Capture the main topics and themes. No speculation.\n"
        f"- tags: 0-{max_tags} tags STRICTLY from the vocabulary below. "
        "Never invent new tags.\n\n"
        f"ALLOWED TAGS:\n{tag_list}\n\n"
        "Respond with valid JSON only, no markdown fences:\n"
        '{"summary": "...", "tags": ["tag1", "tag2"]}'
    )


def _build_user_prompt(cleaned_text: str, rel_path: str) -> str:
    return f"Note path: {rel_path}\n\nNote content:\n{cleaned_text}"


def _build_response_format(max_tags: int, language: str = "EN") -> dict[str, Any]:
    output_language = language_display_name(language)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "journal_toc_entry",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "tags"],
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": (
                            f"A concise factual {output_language} summary."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "maxItems": max_tags,
                        "items": {
                            "type": "string",
                            "enum": sorted(ALLOWED_TAG_SET),
                        },
                    },
                },
            },
        },
    }


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TocLLMResponseError(
            "TOC LLM returned invalid JSON "
            f"({exc.msg} at line {exc.lineno} column {exc.colno})"
        ) from exc
    if not isinstance(parsed, dict):
        raise TocLLMResponseError("TOC LLM returned JSON that is not an object")
    return parsed


async def _summarize_note(
    client: AsyncOpenAI,
    cleaned_text: str,
    rel_path: str,
    model_name: str,
    max_tags: int,
    language: str = "EN",
) -> dict[str, Any]:
    last_error: TocLLMResponseError | None = None
    for attempt in range(TOC_SUMMARY_ATTEMPTS):
        system_prompt = _build_system_prompt(max_tags, language)
        if attempt > 0:
            system_prompt += (
                "\n\nYour previous response could not be parsed as JSON. "
                "Return exactly one complete JSON object that matches the schema."
            )
        completion = await client.chat.completions.create(
            model=model_name,
            temperature=0.2,
            max_tokens=TOC_SUMMARY_MAX_TOKENS,
            response_format=_build_response_format(max_tags, language),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _build_user_prompt(cleaned_text, rel_path)},
            ],
        )
        raw = completion.choices[0].message.content or ""
        try:
            parsed = _parse_llm_json(raw)
            break
        except TocLLMResponseError as exc:
            last_error = exc
            finish_reason = getattr(completion.choices[0], "finish_reason", None)
            logger.warning(
                "TOC summary attempt %s/%s returned unusable output for %s "
                "(finish_reason=%s): %s",
                attempt + 1,
                TOC_SUMMARY_ATTEMPTS,
                rel_path,
                finish_reason,
                exc,
            )
    else:
        if last_error is not None:
            raise last_error
        raise TocLLMResponseError("TOC LLM did not return a usable response")

    summary = str(parsed.get("summary", "")).strip()
    raw_tags = parsed.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [t for t in raw_tags if t in ALLOWED_TAG_SET][:max_tags]
    return {"summary": summary, "tags": tags}


# ---------------------------------------------------------------------------
# TOC rendering
# ---------------------------------------------------------------------------


def _render_toc(
    state: dict[str, Any],
    extra_dirs: list[str],
    toc_filename: str,
    timezone_name: str,
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        "# Table of Contents",
        "",
        f"> **Last updated:** {now_str} ({timezone_name})  ",
    ]
    coverage_parts = ["Daily journal notes"]
    coverage_parts.extend(extra_dirs)
    lines.append(f"> **Coverage:** {' + '.join(coverage_parts)}")
    lines.append("")

    # Tag vocabulary
    lines.append("## Tag Vocabulary")
    lines.append("")
    lines.append("| Tag | Description |")
    lines.append("|-----|-------------|")
    for tag in sorted(TAG_TAXONOMY):
        lines.append(f"| {tag} | {TAG_TAXONOMY[tag]} |")
    lines.append("")

    # Split daily and additional notes
    daily_entries: dict[str, dict[str, Any]] = {}
    extra_entries: dict[str, dict[str, Any]] = {}
    for rel_path, entry in state.items():
        if _is_daily_note(rel_path):
            daily_entries[rel_path] = entry
        else:
            extra_entries[rel_path] = entry

        # Daily notes
    if daily_entries:
        lines.append("## Daily Notes")
        lines.append("")
        by_year: dict[str, dict[str, list[tuple[str, dict[str, Any]]]]] = {}
        for rel_path, entry in daily_entries.items():
            m = DAILY_NOTE_PATTERN.match(rel_path)
            if not m:
                continue
            year, month = m.group(1), m.group(2)
            by_year.setdefault(year, {}).setdefault(month, []).append(
                (rel_path, entry)
            )

        for year in sorted(by_year, reverse=True):
            lines.append(f"### {year}")
            lines.append("")
            for month in sorted(by_year[year], reverse=True):
                month_name = _MONTH_NAMES.get(int(month), month)
                lines.append(f"#### {month_name}")
                lines.append("")
                entries = sorted(by_year[year][month], key=lambda x: x[0], reverse=True)
                for rel_path, entry in entries:
                    _render_entry_line(lines, rel_path, entry, daily=True)
                lines.append("")

        # Additional notes
    if extra_entries:
        lines.append("## Additional Notes")
        lines.append("")
        by_dir: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for rel_path, entry in extra_entries.items():
            parts = Path(rel_path).parts
            group = str(Path(parts[0]) / parts[1]) if len(parts) >= 2 else parts[0]
            by_dir.setdefault(group, []).append((rel_path, entry))

        for group in sorted(by_dir):
            lines.append(f"### {group.replace('/', ' / ')}")
            lines.append("")
            entries = sorted(by_dir[group], key=lambda x: x[0], reverse=True)
            for rel_path, entry in entries:
                _render_entry_line(lines, rel_path, entry, daily=False)
            lines.append("")

    return "\n".join(lines) + "\n"


def _render_entry_line(
    lines: list[str],
    rel_path: str,
    entry: dict[str, Any],
    *,
    daily: bool,
) -> None:
    link_path = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    if daily:
        m = DAILY_NOTE_PATTERN.match(rel_path)
        display = m.group(3) if m else Path(rel_path).stem
    else:
        display = Path(rel_path).stem
    summary = entry.get("summary", "")
    tags = entry.get("tags", [])
    tags_str = ", ".join(tags)
    lines.append(f"- [[{link_path}|{display}]] :: {summary} :: tags: [{tags_str}]")


# ---------------------------------------------------------------------------
# Main index reconciliation
# ---------------------------------------------------------------------------


async def reconcile_toc(
    journal_dir: Path,
    settings: Settings,
    target_paths: list[Path] | None = None,
) -> list[Path]:
    """Reconcile the TOC index with files on disk and return changed paths."""
    if not settings.toc_enabled:
        return []

    toc_filename = settings.toc_filename
    extra_dirs = settings.toc_extra_dirs
    model_name = settings.toc_model
    max_tags = settings.toc_max_tags
    language = getattr(settings, "language", "EN")

    all_files = _discover_files(journal_dir, extra_dirs, toc_filename)
    all_rel: dict[str, Path] = {
        str(f.relative_to(journal_dir)): f for f in all_files
    }

    state = await _load_state(journal_dir)

    # Select the check scope -----------------------------------------------
    if target_paths is not None:
        paths_to_check: dict[str, Path] = {}
        for tp in target_paths:
            try:
                rel = str(tp.relative_to(journal_dir))
            except ValueError:
                continue
            if rel in all_rel:
                paths_to_check[rel] = all_rel[rel]
    else:
        paths_to_check = dict(all_rel)

    # Detect changes -------------------------------------------------------
    files_to_index: dict[str, Path] = {}
    files_to_remove: list[str] = []
    state_touched = False

    if target_paths is None:
        for rel_path in list(state):
            if rel_path not in all_rel:
                files_to_remove.append(rel_path)

    for rel_path, abs_path in paths_to_check.items():
        try:
            current_mtime = abs_path.stat().st_mtime
        except OSError:
            if rel_path in state:
                files_to_remove.append(rel_path)
            continue

        cached = state.get(rel_path)
        if cached and cached.get("mtime") == current_mtime:
            continue

        try:
            async with aiofiles.open(abs_path, "r", encoding="utf-8") as f:
                content = await f.read()
        except OSError:
            continue

        is_daily = _is_daily_note(rel_path)
        if not _has_indexable_content(content, is_daily):
            if rel_path in state:
                files_to_remove.append(rel_path)
            continue

        h = _content_hash(content)
        if cached and cached.get("content_hash") == h:
            state[rel_path]["mtime"] = current_mtime
            state_touched = True
            continue

        files_to_index[rel_path] = abs_path

    if not files_to_index and not files_to_remove and not state_touched:
        return []

    for rel_path in files_to_remove:
        state.pop(rel_path, None)

    # Call the LLM for changed files ---------------------------------------
    if files_to_index:
        client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
        )
        try:
            for rel_path, abs_path in files_to_index.items():
                try:
                    async with aiofiles.open(abs_path, "r", encoding="utf-8") as f:
                        content = await f.read()
                    is_daily = _is_daily_note(rel_path)
                    cleaned = _clean_for_llm(content, is_daily)
                    if not cleaned.strip():
                        state.pop(rel_path, None)
                        continue
                    result = await _summarize_note(
                        client, cleaned, rel_path, model_name, max_tags, language
                    )
                    state[rel_path] = {
                        "content_hash": _content_hash(content),
                        "mtime": abs_path.stat().st_mtime,
                        "summary": result["summary"],
                        "tags": result["tags"],
                        "last_indexed_at": datetime.now().isoformat(),
                    }
                except TocLLMResponseError as exc:
                    logger.warning("Failed to index %s: %s", rel_path, exc)
                except Exception:
                    logger.exception("Failed to index %s, skipping", rel_path)
        finally:
            try:
                await client.close()
            except Exception:
                pass

    # Render and save ------------------------------------------------------
    toc_content = _render_toc(state, extra_dirs, toc_filename, str(settings.timezone))
    toc_path = journal_dir / toc_filename
    async with aiofiles.open(toc_path, "w", encoding="utf-8") as f:
        await f.write(toc_content)

    state_path = await _save_state(journal_dir, state)
    return [toc_path, state_path]

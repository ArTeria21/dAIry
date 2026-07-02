from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import aiofiles
import yaml

from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.enrichment_schemas import DayEnrichment, NoteEnrichment

logger = logging.getLogger(__name__)

ENTRY_HEADING_RE = re.compile(
    r"^##\s+(?:(?P<month>[A-Z][a-z]+)\s+\d{1,2}\s+)?"
    r"(?P<ts>\d{2}:\d{2})(?:\s+—\s+(?P<kind>voice|text))?\s*$"
)
ENRICHMENT_LINE_RE = re.compile(r"^mood::\s*.+?\s*·\s*topics::\s*.*$")
ENRICHMENT_MARKER = "<!-- dairy:note-enrichment -->"
DAILY_NOTE_RE = re.compile(r"^\d{4}/\d{2}/\d{4}-\d{2}-\d{2}\.md$")


class NoteClient(Protocol):
    async def enrich_note(self, text: str) -> NoteEnrichment: ...

    async def embed_note(self, text: str) -> list[float]: ...


class DayClient(Protocol):
    async def enrich_day(self, text: str) -> DayEnrichment: ...


class NoteEnrichmentFailure(RuntimeError):
    """Raised when note-level enrichment fails before markdown can be updated."""


class DayEnrichmentFailure(RuntimeError):
    """Raised when day-level enrichment fails before markdown can be updated."""


@dataclass(slots=True)
class DailyEntry:
    entry_id: str
    date: str
    timestamp: str
    kind: str
    text: str
    start_line: int
    end_line: int
    has_inline_enrichment: bool
    content_hash: str


@dataclass(slots=True)
class NoteEnrichmentResult:
    entry_id: str
    mood: str
    mood_confidence: float
    topics: list[str]


@dataclass(slots=True)
class NoteEnrichmentRun:
    changed: bool
    results: list[NoteEnrichmentResult]


async def read_text(path: Path) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8") as file:
        return await file.read()


async def write_text(path: Path, content: str) -> None:
    async with aiofiles.open(path, "w", encoding="utf-8") as file:
        await file.write(content)


def parse_daily_entries(content: str, note_path: Path) -> list[DailyEntry]:
    lines = content.splitlines()
    note_date = _date_from_path(note_path)
    heading_indices = [
        index for index, line in enumerate(lines) if ENTRY_HEADING_RE.match(line.strip())
    ]
    entries: list[DailyEntry] = []
    seen_ids: dict[str, int] = {}

    for position, start in enumerate(heading_indices):
        end = (
            heading_indices[position + 1]
            if position + 1 < len(heading_indices)
            else len(lines)
        )
        heading = lines[start].strip()
        match = ENTRY_HEADING_RE.match(heading)
        if match is None:
            continue
        timestamp = match.group("ts")
        kind = match.group("kind") or "text"
        block_lines = lines[start + 1 : end]
        body_lines, has_inline = _split_managed_enrichment(block_lines)
        text = "\n".join(_trim_blank_lines(body_lines)).strip()
        if not text:
            continue
        base_id = f"{note_date}T{timestamp}"
        duplicate_count = seen_ids.get(base_id, 0)
        seen_ids[base_id] = duplicate_count + 1
        entry_id = base_id if duplicate_count == 0 else f"{base_id}#{duplicate_count + 1}"
        entries.append(
            DailyEntry(
                entry_id=entry_id,
                date=note_date,
                timestamp=timestamp,
                kind=kind,
                text=text,
                start_line=start,
                end_line=end,
                has_inline_enrichment=has_inline,
                content_hash=_content_hash(text),
            )
        )

    return entries


async def enrich_daily_note_notes(
    note_path: Path,
    journal_dir: Path,
    client: NoteClient,
    store: EnrichmentStore,
) -> bool:
    result = await enrich_daily_note_notes_with_results(
        note_path, journal_dir, client, store
    )
    return result.changed


async def enrich_daily_note_notes_with_results(
    note_path: Path,
    journal_dir: Path,
    client: NoteClient,
    store: EnrichmentStore,
) -> NoteEnrichmentRun:
    content = await read_text(note_path)
    entries = parse_daily_entries(content, note_path)
    pending: list[tuple[DailyEntry, NoteEnrichment, list[float]]] = []
    changed_results: list[NoteEnrichmentResult] = []

    for entry in entries:
        cached_hash = store.get_note_entry_hash(entry.entry_id)
        cached_note = store.get_note(entry.entry_id)
        if (
            cached_hash == entry.content_hash
            and cached_note is not None
            and entry.has_inline_enrichment
        ):
            continue
        try:
            enrichment = await client.enrich_note(entry.text)
            embedding = await client.embed_note(entry.text)
        except Exception as exc:
            raise NoteEnrichmentFailure(
                f"Failed to enrich note entry {entry.entry_id}"
            ) from exc
        pending.append((entry, enrichment, embedding))
        changed_results.append(
            NoteEnrichmentResult(
                entry_id=entry.entry_id,
                mood=enrichment.mood.value,
                mood_confidence=enrichment.mood_confidence,
                topics=[topic.value for topic in enrichment.topics],
            )
        )

    if not pending:
        return NoteEnrichmentRun(changed=False, results=[])

    enriched_by_id = {entry.entry_id: enrichment for entry, enrichment, _ in pending}
    updated_content = _render_note_enrichments(content, entries, enriched_by_id)
    await write_text(note_path, updated_content)
    rel_path = str(note_path.relative_to(journal_dir))

    for entry, enrichment, embedding in pending:
        store.upsert_note(
            note_id=entry.entry_id,
            date=entry.date,
            ts=entry.timestamp,
            note_path=rel_path,
            enrichment=enrichment,
            embedding=embedding,
            content_hash=entry.content_hash,
        )

    return NoteEnrichmentRun(changed=True, results=changed_results)


async def enrich_day_summary(
    note_path: Path,
    journal_dir: Path,
    client: DayClient,
    store: EnrichmentStore,
    *,
    timezone: ZoneInfo | None = None,
) -> bool:
    content = await read_text(note_path)
    note_date = _date_from_path(note_path)
    try:
        enrichment = await client.enrich_day(_clean_for_day_prompt(content))
    except Exception:
        raise DayEnrichmentFailure(f"Failed to enrich day {note_path}") from None

    # Derived fields depend only on the daily note's calendar date. The timezone
    # argument stays on this public API because callers already pass localized
    # note context, but weekday/season do not need it once the date is known.
    derived = _derived_date_fields(date.fromisoformat(note_date))
    updated = _update_frontmatter(content, enrichment, derived)
    changed = updated != content
    if changed:
        await write_text(note_path, updated)
    store.upsert_day(
        date=note_date,
        enrichment=enrichment,
        weekday=derived["weekday"],
        is_weekend=derived["is_weekend"],
        season=derived["season"],
    )
    return changed


def discover_daily_notes(journal_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in journal_dir.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(journal_dir))
        except ValueError:
            continue
        if DAILY_NOTE_RE.match(rel):
            paths.append(path)
    return sorted(paths)


def entries_fingerprint(content: str, note_path: Path) -> str:
    """Hash only the entry texts, so frontmatter/enrichment rewrites don't count."""
    entries = parse_daily_entries(content, note_path)
    return _content_hash(
        "\n".join(f"{entry.entry_id}:{entry.content_hash}" for entry in entries)
    )


def _render_note_enrichments(
    content: str,
    entries: list[DailyEntry],
    enriched_by_id: dict[str, NoteEnrichment],
) -> str:
    lines = content.splitlines()
    for entry in reversed(entries):
        enrichment = enriched_by_id.get(entry.entry_id)
        if enrichment is None:
            continue
        block = lines[entry.start_line : entry.end_line]
        heading = block[0]
        body, _ = _split_managed_enrichment(block[1:])
        body = _trim_blank_lines(body)
        inline = _format_inline_enrichment(enrichment)
        replacement = [heading, ""]
        replacement.extend(body)
        replacement.append(ENRICHMENT_MARKER)
        replacement.append(inline)
        replacement.append("")
        lines[entry.start_line : entry.end_line] = replacement
    return "\n".join(lines).rstrip() + "\n"


def _format_inline_enrichment(enrichment: NoteEnrichment) -> str:
    topics = ", ".join(topic.value for topic in enrichment.topics)
    return f"mood:: {enrichment.mood.value} · topics:: {topics}"


def _split_managed_enrichment(lines: list[str]) -> tuple[list[str], bool]:
    body: list[str] = []
    has_managed_enrichment = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() != ENRICHMENT_MARKER:
            body.append(line)
            index += 1
            continue

        has_managed_enrichment = True
        next_index = index + 1
        if (
            next_index < len(lines)
            and ENRICHMENT_LINE_RE.match(lines[next_index].strip())
        ):
            index += 2
        else:
            index += 1
    return body, has_managed_enrichment


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _date_from_path(path: Path) -> str:
    return path.stem


def _clean_for_day_prompt(content: str) -> str:
    _, body = _split_frontmatter(content)
    lines = [
        line
        for line in body.splitlines()
        if line.strip() != ENRICHMENT_MARKER
    ]
    return "\n".join(lines).strip()


def _split_frontmatter(content: str) -> tuple[dict[str, object], str]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "".join(lines[1:index])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                data = {}
            return data, "".join(lines[index + 1 :])
    return {}, content


def _update_frontmatter(
    content: str,
    enrichment: DayEnrichment,
    derived: dict[str, object],
) -> str:
    existing, body = _split_frontmatter(content)
    data = dict(existing)
    data.update(
        {
            "mood": enrichment.mood.value,
            "mood_confidence": enrichment.mood_confidence,
            "key_topics": [topic.value for topic in enrichment.key_topics],
            "sport": enrichment.sport,
            "reading": enrichment.reading,
            "purchases": enrichment.purchases,
            "eating_outside": enrichment.eating_outside,
            "deep_focus": enrichment.deep_focus,
            "sleep_quality": enrichment.sleep_quality,
            "weekday": derived["weekday"],
            "is_weekend": derived["is_weekend"],
            "season": derived["season"],
            "summary": enrichment.summary,
        }
    )
    frontmatter = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{frontmatter}---\n{body.lstrip()}"


def _derived_date_fields(value: date) -> dict[str, object]:
    return {
        "weekday": value.strftime("%A"),
        "is_weekend": value.weekday() >= 5,
        "season": _season_for_date(value),
    }


def _season_for_date(value: date) -> str:
    if value.month in (12, 1, 2):
        return "winter"
    if value.month in (3, 4, 5):
        return "spring"
    if value.month in (6, 7, 8):
        return "summer"
    return "autumn"

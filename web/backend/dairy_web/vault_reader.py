from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ENTRY_HEADING_RE = re.compile(
    r"^##\s+(?:[A-Z][a-z]+\s+\d{1,2}\s+)?"
    r"(?P<ts>\d{2}:\d{2})(?:\s+—\s+(?P<kind>voice|text))?\s*$"
)
ENRICHMENT_LINE_RE = re.compile(r"^mood::\s*.+?\s*·\s*topics::\s*.*$")
ENRICHMENT_MARKER = "<!-- dairy:note-enrichment -->"
DAY_FILE_RE = re.compile(r"^\d{4}/\d{2}/\d{4}-\d{2}-\d{2}\.md$")


class NoteRawTextNotFound(LookupError):
    """Raised when a requested journal note section cannot be read."""


class DayNotFound(LookupError):
    """Raised when a requested journal day cannot be read."""


@dataclass(frozen=True, slots=True)
class DayNoteBlock:
    ts: str
    kind: str | None
    heading_display: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class IdentifiedDayNoteBlock:
    id: str
    block: DayNoteBlock


def extract_note_raw_text(*, vault_dir: Path | str, note_path: str, ts: str) -> str:
    vault_root = Path(vault_dir).resolve()
    full_path = (vault_root / note_path).resolve()
    if not _is_relative_to(full_path, vault_root) or not full_path.is_file():
        raise NoteRawTextNotFound("Raw text is unavailable for this note")

    for block in _parse_day_blocks(full_path):
        if block.ts != ts:
            continue
        return block.raw_text

    raise NoteRawTextNotFound("Raw text is unavailable for this note")


def extract_note_raw_text_by_id(
    *,
    vault_dir: Path | str,
    note_path: str,
    note_id: str,
) -> str:
    vault_root = Path(vault_dir).resolve()
    full_path = (vault_root / note_path).resolve()
    if not _is_relative_to(full_path, vault_root) or not full_path.is_file():
        raise NoteRawTextNotFound("Raw text is unavailable for this note")

    target_date = _note_id_date(note_id)
    for identified in identify_day_note_blocks(
        blocks=_parse_day_blocks(full_path),
        target_date=target_date,
    ):
        if identified.id == note_id:
            return identified.block.raw_text

    raise NoteRawTextNotFound("Raw text is unavailable for this note")


def canonical_raw_text(raw_text: str) -> str:
    return "\n".join(_trim_blank_lines(raw_text.splitlines())).strip()


def raw_text_sha256(raw_text: str) -> str:
    return hashlib.sha256(canonical_raw_text(raw_text).encode("utf-8")).hexdigest()


def identify_day_note_blocks(
    *,
    blocks: list[DayNoteBlock],
    target_date: str,
) -> list[IdentifiedDayNoteBlock]:
    seen: dict[str, int] = {}
    identified: list[IdentifiedDayNoteBlock] = []
    for block in blocks:
        if not canonical_raw_text(block.raw_text):
            continue
        base_id = f"{target_date}T{block.ts}"
        duplicate_count = seen.get(base_id, 0)
        seen[base_id] = duplicate_count + 1
        note_id = base_id if duplicate_count == 0 else f"{base_id}#{duplicate_count + 1}"
        identified.append(IdentifiedDayNoteBlock(id=note_id, block=block))
    return identified


def read_day(*, vault_dir: Path | str, day: str) -> list[DayNoteBlock]:
    vault_root = Path(vault_dir).resolve()
    full_path = _day_path(vault_root, day).resolve()
    if not _is_relative_to(full_path, vault_root) or not full_path.is_file():
        raise DayNotFound("Day not found")
    return _parse_day_blocks(full_path)


def list_day_dates(*, vault_dir: Path | str) -> list[str]:
    vault_root = Path(vault_dir).resolve()
    if not vault_root.is_dir():
        return []

    dates: list[str] = []
    for path in vault_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/????-??-??.md"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(vault_root).as_posix()
        except ValueError:
            continue
        if not DAY_FILE_RE.match(relative):
            continue
        raw_date = path.stem
        if _is_valid_day_file_date(raw_date, relative):
            dates.append(raw_date)
    return sorted(dates)


def _parse_day_blocks(path: Path) -> list[DayNoteBlock]:
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_indexes = [
        index
        for index, line in enumerate(lines)
        if ENTRY_HEADING_RE.match(line.strip())
    ]
    blocks: list[DayNoteBlock] = []

    for position, start in enumerate(heading_indexes):
        match = ENTRY_HEADING_RE.match(lines[start].strip())
        if match is None:
            continue
        end = (
            heading_indexes[position + 1]
            if position + 1 < len(heading_indexes)
            else len(lines)
        )
        body = _strip_managed_enrichment(lines[start + 1 : end])
        blocks.append(
            DayNoteBlock(
                ts=match.group("ts"),
                kind=match.group("kind"),
                heading_display=_heading_display(lines[start]),
                raw_text="\n".join(_trim_blank_lines(body)),
            )
        )

    return blocks


def _day_path(vault_root: Path, raw_date: str) -> Path:
    return vault_root / raw_date[:4] / raw_date[5:7] / f"{raw_date}.md"


def _note_id_date(note_id: str) -> str:
    target_date, separator, _time = note_id.partition("T")
    if separator != "T" or len(target_date) != 10:
        raise NoteRawTextNotFound("Raw text is unavailable for this note")
    try:
        date.fromisoformat(target_date)
    except ValueError as exc:
        raise NoteRawTextNotFound("Raw text is unavailable for this note") from exc
    return target_date


def _heading_display(line: str) -> str:
    return line.strip().removeprefix("##").strip()


def _is_valid_day_file_date(raw_date: str, relative: str) -> bool:
    try:
        parsed = date.fromisoformat(raw_date)
    except ValueError:
        return False
    return relative == f"{parsed:%Y/%m/%Y-%m-%d}.md"


def _strip_managed_enrichment(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if line.strip() != ENRICHMENT_MARKER
        and not ENRICHMENT_LINE_RE.match(line.strip())
    ]


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True

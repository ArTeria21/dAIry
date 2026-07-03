from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from dairy_bot.services.enrichment import (
    ENRICHMENT_LINE_RE,
    ENRICHMENT_MARKER,
    ENTRY_HEADING_RE,
    parse_daily_entries,
)

MAX_NOTE_TEXT_CHARS = 50_000


class NoteEditError(RuntimeError):
    """Base note editing error."""


class NoteEditValidationError(NoteEditError):
    """Raised when replacement text is unsafe or invalid."""


class NoteEditConflict(NoteEditError):
    """Raised when optimistic locking fails."""


class NoteEditNotFound(NoteEditError):
    """Raised when the target note block cannot be found."""


@dataclass(frozen=True, slots=True)
class NoteTextReplacement:
    content: str
    new_sha256: str


@dataclass(frozen=True, slots=True)
class _EditableBlock:
    entry_id: str
    start: int
    end: int
    header: str
    body_lines: list[str]
    managed_lines: list[str]


def canonical_note_text(text: str) -> str:
    return "\n".join(_trim_blank_lines(text.splitlines())).strip()


def note_text_sha256(text: str) -> str:
    return hashlib.sha256(canonical_note_text(text).encode("utf-8")).hexdigest()


def validate_new_text(new_text: str) -> str:
    trimmed = new_text.strip()
    if not trimmed:
        raise NoteEditValidationError("text must not be empty")
    if len(trimmed) > MAX_NOTE_TEXT_CHARS:
        raise NoteEditValidationError("text is too long")
    if ENRICHMENT_MARKER in trimmed:
        raise NoteEditValidationError("text must not contain managed enrichment markers")
    for line in trimmed.splitlines():
        if line.startswith("## ") or ENTRY_HEADING_RE.match(line.strip()):
            raise NoteEditValidationError("text must not contain note headings (## HH:MM)")
    return trimmed


def replace_note_text(
    *,
    content: str,
    note_id: str,
    note_path: Path | str,
    expected_sha256: str,
    new_text: str,
) -> NoteTextReplacement:
    trimmed = validate_new_text(new_text)
    block = _find_block(content, note_id=note_id, note_path=Path(note_path))
    current_text = _canonical_lines(block.body_lines)
    if note_text_sha256(current_text) != expected_sha256:
        raise NoteEditConflict("note changed elsewhere")

    new_lines = _render_replacement_block(block.header, trimmed, block.managed_lines)
    lines = content.splitlines(keepends=True)
    updated = "".join(lines[: block.start] + new_lines + lines[block.end :])
    return NoteTextReplacement(content=updated, new_sha256=note_text_sha256(trimmed))


def _find_block(content: str, *, note_id: str, note_path: Path) -> _EditableBlock:
    lines = content.splitlines(keepends=True)
    for entry in parse_daily_entries(content, note_path):
        if entry.entry_id == note_id:
            body_lines, managed_lines = _split_managed_lines(
                lines[entry.start_line + 1 : entry.end_line]
            )
            return _EditableBlock(
                entry_id=entry.entry_id,
                start=entry.start_line,
                end=entry.end_line,
                header=lines[entry.start_line],
                body_lines=body_lines,
                managed_lines=managed_lines,
            )

    raise NoteEditNotFound("note block not found")


def _split_managed_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    body: list[str] = []
    managed: list[str] = []
    index = 0
    while index < len(lines):
        stripped = _line_text(lines[index]).strip()
        if stripped == ENRICHMENT_MARKER:
            managed.append(lines[index])
            next_index = index + 1
            if (
                next_index < len(lines)
                and ENRICHMENT_LINE_RE.match(_line_text(lines[next_index]).strip())
            ):
                managed.append(lines[next_index])
                index += 2
            else:
                index += 1
            continue
        body.append(lines[index])
        index += 1
    return body, managed


def _canonical_lines(lines: list[str]) -> str:
    return "\n".join(_trim_blank_lines([_line_text(line) for line in lines])).strip()


def _render_replacement_block(
    header: str,
    new_text: str,
    managed_lines: list[str],
) -> list[str]:
    rendered = [_ensure_newline(header), "\n"]
    rendered.extend(f"{line}\n" for line in new_text.splitlines())
    rendered.append("\n")
    if managed_lines:
        rendered.extend(_ensure_newline(line) for line in managed_lines)
        rendered.append("\n")
    return rendered


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _line_text(line: str) -> str:
    return line.rstrip("\r\n")


def _ensure_newline(line: str) -> str:
    return line if line.endswith(("\n", "\r")) else f"{line}\n"

from __future__ import annotations

import re
from pathlib import Path


ENTRY_HEADING_RE = re.compile(
    r"^##\s+(?:[A-Z][a-z]+\s+\d{1,2}\s+)?"
    r"(?P<ts>\d{2}:\d{2})(?:\s+—\s+(voice|text))?\s*$"
)
ENRICHMENT_LINE_RE = re.compile(r"^mood::\s*.+?\s*·\s*topics::\s*.*$")
ENRICHMENT_MARKER = "<!-- dairy:note-enrichment -->"


class NoteRawTextNotFound(LookupError):
    """Raised when a requested journal note section cannot be read."""


def extract_note_raw_text(*, vault_dir: Path | str, note_path: str, ts: str) -> str:
    vault_root = Path(vault_dir).resolve()
    full_path = (vault_root / note_path).resolve()
    if not _is_relative_to(full_path, vault_root) or not full_path.is_file():
        raise NoteRawTextNotFound("Raw text is unavailable for this note")

    lines = full_path.read_text(encoding="utf-8").splitlines()
    heading_indexes = [
        index
        for index, line in enumerate(lines)
        if ENTRY_HEADING_RE.match(line.strip())
    ]
    for position, start in enumerate(heading_indexes):
        match = ENTRY_HEADING_RE.match(lines[start].strip())
        if match is None or match.group("ts") != ts:
            continue
        end = (
            heading_indexes[position + 1]
            if position + 1 < len(heading_indexes)
            else len(lines)
        )
        body = _strip_managed_enrichment(lines[start + 1 : end])
        return "\n".join(_trim_blank_lines(body))

    raise NoteRawTextNotFound("Raw text is unavailable for this note")


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

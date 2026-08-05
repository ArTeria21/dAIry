from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from dairy_bot.services.enrichment import DAILY_NOTE_RE, parse_daily_entries


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    document_id: str
    source_type: str
    path: str
    heading: str | None
    text: str
    content_hash: str
    document_date: date | None
    first_seen: datetime

    def eligible_on(self, cutoff: date) -> bool:
        if self.document_date is not None:
            return self.document_date <= cutoff
        return self.first_seen.date() <= cutoff


def scan_diary_corpus(
    vault: Path,
    *,
    first_seen: datetime,
) -> list[CorpusDocument]:
    root = vault.resolve()
    documents: list[CorpusDocument] = []
    for path in sorted(vault.rglob("*.md")):
        safe_path = _safe_path(path, root)
        if safe_path is None:
            continue
        relative = safe_path.relative_to(root).as_posix()
        if DAILY_NOTE_RE.fullmatch(relative) is None:
            continue
        try:
            content = safe_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        documents.extend(
            _daily_documents(content, safe_path, relative, first_seen)
        )
    return sorted(documents, key=lambda document: document.document_id)


def _safe_path(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _daily_documents(
    content: str,
    path: Path,
    relative: str,
    first_seen: datetime,
) -> list[CorpusDocument]:
    try:
        day = date.fromisoformat(path.stem)
        entries = parse_daily_entries(content, path)
    except ValueError:
        return []
    return [
        CorpusDocument(
            document_id=f"diary:{entry.entry_id}",
            source_type="diary",
            path=relative,
            heading=entry.timestamp,
            text=entry.text,
            content_hash=hashlib.sha256(entry.text.encode("utf-8")).hexdigest(),
            document_date=day,
            first_seen=first_seen,
        )
        for entry in entries
    ]

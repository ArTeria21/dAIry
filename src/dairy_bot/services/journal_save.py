from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Literal

from dairy_bot.config import Settings
from dairy_bot.services.enrichment import (
    NoteEnrichmentFailure,
    NoteEnrichmentRun,
    NoteClient,
    enrich_daily_note_notes_with_results,
)
from dairy_bot.services.enrichment_client import build_enrichment_client
from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.git_sync import GitService, GitSyncError
from dairy_bot.services.journal_lock import get_journal_lock
from dairy_bot.services.storage import append_entry
from dairy_bot.services.toc_service import reconcile_toc

logger = logging.getLogger(__name__)

SaveState = Literal["empty", "blocked", "synced", "local_only"]
SaveProgressEvent = Literal[
    "repo_sync_blocked",
    "note_written",
    "note_processed",
    "note_failed",
    "final",
]
SaveProgressCallback = Callable[["JournalSaveProgress"], Awaitable[None]]
ClientFactory = Callable[[Settings], NoteClient]
TocReconciler = Callable[..., Awaitable[list[Path]]]


@dataclass(slots=True)
class JournalSaveProgress:
    event: SaveProgressEvent
    note_run: NoteEnrichmentRun | None = None
    enrichment_failed: bool = False
    synced: bool | None = None


async def save_entry_with_sync(
    content: str,
    settings: Settings,
    git_service: GitService,
    *,
    target_date: date | None = None,
    moment: datetime | None = None,
    entry_kind: str | None = None,
    progress: SaveProgressCallback | None = None,
    client_factory: ClientFactory = build_enrichment_client,
    reconcile_toc_func: TocReconciler = reconcile_toc,
) -> SaveState:
    if not content.strip():
        return "empty"

    async with get_journal_lock():
        try:
            await asyncio.to_thread(git_service.prepare_for_write)
        except GitSyncError:
            logger.warning("Git sync blocked journal write", exc_info=True)
            await _emit(progress, JournalSaveProgress("repo_sync_blocked"))
            return "blocked"

        current = moment or datetime.now(settings.timezone)
        immediate_enrichment = bool(getattr(settings, "enrichment_enabled", False))
        note_path = await append_entry(
            settings.journal_dir,
            content,
            moment=current,
            timezone=settings.timezone,
            target_date=target_date,
            entry_kind=entry_kind,
        )

        enrichment_paths: list[Path] = []
        note_run: NoteEnrichmentRun | None = None
        enrichment_failed = False

        if immediate_enrichment:
            await _emit(progress, JournalSaveProgress("note_written"))
            note_run, enrichment_failed = await _enrich_note(
                note_path,
                settings,
                client_factory,
            )
            if note_run is not None:
                enrichment_paths.append(note_path)
                await _emit(
                    progress,
                    JournalSaveProgress("note_processed", note_run=note_run),
                )
            elif enrichment_failed:
                await _emit(
                    progress,
                    JournalSaveProgress("note_failed", enrichment_failed=True),
                )

        toc_paths = await reconcile_toc_func(
            settings.journal_dir, settings, target_paths=[note_path]
        )
        try:
            result = await asyncio.to_thread(
                git_service.commit_and_push,
                _unique_paths([note_path] + enrichment_paths + toc_paths),
            )
        except GitSyncError:
            logger.warning("Git push failed after journal write", exc_info=True)
            if immediate_enrichment:
                await _emit(
                    progress,
                    JournalSaveProgress(
                        "final",
                        note_run=note_run,
                        enrichment_failed=enrichment_failed,
                        synced=False,
                    ),
                )
            return "local_only"

        if immediate_enrichment:
            await _emit(
                progress,
                JournalSaveProgress(
                    "final",
                    note_run=note_run,
                    enrichment_failed=enrichment_failed,
                    synced=result.pushed,
                ),
            )
        return "synced" if result.pushed else "local_only"


async def _enrich_note(
    note_path: Path,
    settings: Settings,
    client_factory: ClientFactory,
) -> tuple[NoteEnrichmentRun | None, bool]:
    client = None
    try:
        store = EnrichmentStore(settings.enrichment_db_path)
        client = client_factory(settings)
        result = await enrich_daily_note_notes_with_results(
            note_path, settings.journal_dir, client, store
        )
    except NoteEnrichmentFailure:
        logger.warning("Immediate note enrichment failed", exc_info=True)
        return None, True
    except Exception:
        logger.exception("Unexpected immediate note enrichment failure")
        return None, True
    finally:
        if client is not None:
            await _close_client(client)
    return result, False


async def _emit(
    progress: SaveProgressCallback | None,
    event: JournalSaveProgress,
) -> None:
    if progress is not None:
        await progress(event)


async def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if close is not None:
        await close()


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique

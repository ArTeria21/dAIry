from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from dairy_bot.config import Settings
from dairy_bot.services.enrichment import (
    DayClient,
    DayEnrichmentFailure,
    NoteClient,
    NoteEnrichmentFailure,
    discover_daily_notes,
    enrich_daily_note_notes,
    enrich_day_summary,
    file_content_hash,
)
from dairy_bot.services.enrichment_client import build_enrichment_client
from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.git_sync import GitPushError, GitService, GitSyncError
from dairy_bot.services.journal_lock import get_journal_lock
from dairy_bot.services.toc_service import reconcile_toc

logger = logging.getLogger(__name__)

WATCHDOG_STATE_KEY = "watchdog"
NIGHTLY_DAY_STATE_KEY = "nightly_day"

ClientFactory = Callable[[Settings], NoteClient | DayClient]
TocReconciler = Callable[..., Awaitable[list[Path]]]


async def reconcile_background_once(
    settings: Settings,
    git_service: GitService,
    label: str,
    now: datetime | None = None,
    *,
    client_factory: ClientFactory = build_enrichment_client,
    reconcile_toc_func: TocReconciler = reconcile_toc,
) -> None:
    """Run quiet enrichment first, then TOC, without racing journal writes."""
    if not settings.toc_enabled and not settings.enrichment_enabled:
        return

    async with get_journal_lock():
        try:
            await asyncio.to_thread(git_service.prepare_for_write)
            enrichment_paths = await reconcile_changed_enrichment(
                settings,
                now=now,
                client_factory=client_factory,
            )
            toc_paths = (
                await reconcile_toc_func(settings.journal_dir, settings)
                if settings.toc_enabled
                else []
            )
            changed_paths = _unique_paths(enrichment_paths + toc_paths)
            if not changed_paths:
                logger.info(
                    "%s background reconciliation complete, everything up to date",
                    label,
                )
                return

            try:
                await asyncio.to_thread(git_service.commit_and_push, changed_paths)
            except GitPushError:
                logger.warning(
                    "%s background reconciliation saved locally, but push failed",
                    label,
                    exc_info=True,
                )
            logger.info(
                "%s background reconciliation complete, %d files updated",
                label,
                len(changed_paths),
            )
        except GitSyncError:
            logger.warning(
                "%s background reconciliation skipped because repo sync is blocked",
                label,
                exc_info=True,
            )
        except Exception:
            logger.exception("%s background reconciliation failed", label)


async def reconcile_changed_enrichment(
    settings: Settings,
    *,
    now: datetime | None = None,
    client_factory: ClientFactory = build_enrichment_client,
) -> list[Path]:
    if not settings.enrichment_enabled:
        return []

    current = now or datetime.now(settings.timezone)
    today = current.astimezone(settings.timezone).date()
    store = EnrichmentStore(settings.enrichment_db_path)
    client = client_factory(settings)
    changed_paths: list[Path] = []
    try:
        for note_path in discover_daily_notes(settings.journal_dir):
            try:
                note_date = datetime.strptime(note_path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if note_date == today:
                continue
            rel_path = str(note_path.relative_to(settings.journal_dir))
            before_hash = await file_content_hash(note_path)
            if store.get_file_hash(rel_path, WATCHDOG_STATE_KEY) == before_hash:
                continue
            try:
                note_changed = await enrich_daily_note_notes(
                    note_path, settings.journal_dir, client, store
                )
            except NoteEnrichmentFailure:
                logger.warning(
                    "Skipping failed note enrichment for %s",
                    note_path,
                    exc_info=True,
                )
                continue
            try:
                day_changed = await enrich_day_summary(
                    note_path,
                    settings.journal_dir,
                    client,
                    store,
                    timezone=settings.timezone,
                )
            except DayEnrichmentFailure:
                logger.warning(
                    "Skipping failed day enrichment for %s",
                    note_path,
                    exc_info=True,
                )
                continue
            final_hash = await file_content_hash(note_path)
            store.set_file_hash(rel_path, WATCHDOG_STATE_KEY, final_hash)
            if note_changed or day_changed or final_hash != before_hash:
                changed_paths.append(note_path)
    finally:
        await _close_client(client)
    return changed_paths


async def reconcile_nightly_enrichment_once(
    settings: Settings,
    *,
    client_factory: ClientFactory = build_enrichment_client,
) -> list[Path]:
    if not settings.enrichment_enabled:
        return []

    store = EnrichmentStore(settings.enrichment_db_path)
    client = client_factory(settings)
    changed_paths: list[Path] = []
    try:
        for note_path in discover_daily_notes(settings.journal_dir):
            rel_path = str(note_path.relative_to(settings.journal_dir))
            before_hash = await file_content_hash(note_path)
            if store.get_file_hash(rel_path, NIGHTLY_DAY_STATE_KEY) == before_hash:
                continue
            try:
                changed = await enrich_day_summary(
                    note_path,
                    settings.journal_dir,
                    client,
                    store,
                    timezone=settings.timezone,
                )
            except DayEnrichmentFailure:
                logger.warning(
                    "Skipping failed nightly day enrichment for %s",
                    note_path,
                    exc_info=True,
                )
                continue
            final_hash = await file_content_hash(note_path)
            store.set_file_hash(rel_path, NIGHTLY_DAY_STATE_KEY, final_hash)
            if changed:
                changed_paths.append(note_path)
    finally:
        await _close_client(client)
    return changed_paths


async def periodic_background_loop(
    settings: Settings,
    git_service: GitService,
    *,
    client_factory: ClientFactory = build_enrichment_client,
    reconcile_toc_func: TocReconciler = reconcile_toc,
) -> None:
    """Periodically reconcile manual vault edits."""
    interval_seconds = max(settings.toc_scan_interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval_seconds)
        await reconcile_background_once(
            settings,
            git_service,
            "Periodic",
            client_factory=client_factory,
            reconcile_toc_func=reconcile_toc_func,
        )


async def nightly_enrichment_loop(
    settings: Settings,
    git_service: GitService,
    *,
    client_factory: ClientFactory = build_enrichment_client,
) -> None:
    """Run quiet day-level enrichment around 03:00 in the configured timezone."""
    while True:
        now = datetime.now(settings.timezone)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        async with get_journal_lock():
            try:
                await asyncio.to_thread(git_service.prepare_for_write)
                changed_paths = await reconcile_nightly_enrichment_once(
                    settings,
                    client_factory=client_factory,
                )
                if changed_paths:
                    await asyncio.to_thread(git_service.commit_and_push, changed_paths)
            except Exception:
                logger.exception("Nightly enrichment failed")


async def start_background_reconciliation(
    settings: Settings,
    git_service: GitService,
    *,
    client_factory: ClientFactory = build_enrichment_client,
    reconcile_toc_func: TocReconciler = reconcile_toc,
) -> list[asyncio.Task[None]]:
    tasks: list[asyncio.Task[None]] = []
    if settings.toc_enabled or settings.enrichment_enabled:
        logger.info("Running initial background reconciliation...")
        await reconcile_background_once(
            settings,
            git_service,
            "Initial",
            client_factory=client_factory,
            reconcile_toc_func=reconcile_toc_func,
        )
        tasks.append(
            asyncio.create_task(
                periodic_background_loop(
                    settings,
                    git_service,
                    client_factory=client_factory,
                    reconcile_toc_func=reconcile_toc_func,
                )
            )
        )
    if settings.enrichment_enabled:
        tasks.append(
            asyncio.create_task(
                nightly_enrichment_loop(
                    settings, git_service, client_factory=client_factory
                )
            )
        )
    return tasks


async def stop_background_reconciliation(tasks: list[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


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

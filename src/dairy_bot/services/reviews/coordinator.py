from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from dairy_bot.services.semantic_embeddings import (
    DiarySemanticIndexer,
    SemanticEmbeddingService,
)

from .corpus import scan_corpus
from .models import CorpusDocument, ReviewPeriod, ReviewRecord
from .operations import order_backfill
from .periods import discover_closed_periods
from .store import ReviewStore

REVIEWS_ACTIVATED_AT = "reviews_activated_at"

Embed = Callable[[str], Awaitable[Sequence[float]]]
logger = logging.getLogger(__name__)


class ReviewCorpusIndexer:
    def __init__(
        self,
        *,
        store: ReviewStore,
        embed: Embed,
        embedding_model: str,
        embedding_service: SemanticEmbeddingService | None = None,
        semantic_indexer: DiarySemanticIndexer | None = None,
    ) -> None:
        self.store = store
        self.embed = embed
        self.embedding_model = embedding_model
        self.embeddings = embedding_service or SemanticEmbeddingService(
            store=store.embeddings,
            embed=embed,
            model=embedding_model,
        )
        self.semantic_indexer = semantic_indexer or DiarySemanticIndexer(
            store=store.embeddings,
            embeddings=self.embeddings,
        )
        self.ready = False

    async def sync(
        self, documents: Sequence[CorpusDocument]
    ) -> list[CorpusDocument]:
        existing_documents = {
            document.document_id: document
            for document in self.store.list_corpus_documents()
        }
        merged = [
            replace(
                document,
                first_seen=min(
                    document.first_seen,
                    existing_documents.get(document.document_id, document).first_seen,
                ),
            )
            for document in documents
        ]
        self.ready = await self.semantic_indexer.sync(merged)
        if self.ready:
            self.store.replace_corpus(merged)
            active = self.store.embeddings.get_active_generation()
            if active is not None:
                self.store.enqueue_recipe_migrations(
                    model=active.model,
                    recipe=active.recipe_version,
                )
        return merged


class JobRunner(Protocol):
    async def run_next(self) -> bool: ...

    async def deliver_ready(self, record: ReviewRecord) -> Any: ...


class ReviewCoordinator:
    """Index, enqueue and run one review job per reconciliation pass."""

    def __init__(
        self,
        *,
        vault: Path,
        store: ReviewStore,
        timezone: ZoneInfo,
        weekly_time: time,
        monthly_time: time,
        runner: JobRunner,
        indexer: ReviewCorpusIndexer | None = None,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self.vault = Path(vault)
        self.store = store
        self.timezone = timezone
        self.weekly_time = weekly_time
        self.monthly_time = monthly_time
        self.runner = runner
        self.indexer = indexer
        self.poll_interval_seconds = poll_interval_seconds

    async def reconcile_once(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(self.timezone)
        local = (
            current.replace(tzinfo=self.timezone)
            if current.tzinfo is None
            else current.astimezone(self.timezone)
        )
        previous_documents = self.store.list_corpus_documents()
        scanned = scan_corpus(self.vault, first_seen=local)
        invalidated = _invalidate_changed_sources(
            self.store,
            previous=previous_documents,
            current=scanned,
        )
        candidate_documents = _preserve_first_seen(scanned, previous_documents)
        activation_raw = self.store.get_metadata(REVIEWS_ACTIVATED_AT)
        if activation_raw is None:
            activation = local
            self.store.set_metadata(REVIEWS_ACTIVATED_AT, activation.isoformat())
        else:
            activation = datetime.fromisoformat(activation_raw).astimezone(self.timezone)
        periods = order_backfill(
            discover_closed_periods(
                self.vault,
                now=local,
                timezone=self.timezone,
            )
        )
        deliveries = {
            (delivery.kind, delivery.period) for delivery in self.store.list_deliveries()
        }
        for period in periods:
            key = period.kind, period.period
            source_hash = source_hash_for_period(period, candidate_documents)
            existing = self.store.get_review(*key)
            delivery_due = activation < self._delivery_at(period) <= local
            if (
                source_hash
                and delivery_due
                and key not in deliveries
                and existing is not None
                and existing.status == "ready"
                and existing.source_hash == source_hash
            ):
                await self.runner.deliver_ready(existing)
                deliveries.add(key)

        documents = (
            await self.indexer.sync(scanned)
            if self.indexer is not None
            else candidate_documents
        )
        if self.indexer is not None and not self.indexer.ready:
            return
        if self.indexer is None:
            self.store.replace_corpus(documents)

        for period in periods:
            source_hash = source_hash_for_period(period, documents)
            if not source_hash:
                continue
            existing = self.store.get_review(period.kind, period.period)
            delivery_at = self._delivery_at(period)
            delivery_due = activation < delivery_at <= local
            already_delivered = (period.kind, period.period) in deliveries
            if (
                (period.kind, period.period) in invalidated
                and not (delivery_due and not already_delivered)
            ):
                continue
            if existing is not None and existing.source_hash == source_hash:
                if existing.status == "stale":
                    self.store.enqueue_job(
                        period.kind,
                        period.period,
                        source_hash,
                        reason=(
                            "scheduled"
                            if delivery_due and not already_delivered
                            else "stale"
                        ),
                    )
                    continue
                if delivery_due and not already_delivered:
                    if existing.status == "ready":
                        await self.runner.deliver_ready(existing)
                    else:
                        self.store.enqueue_job(
                            period.kind,
                            period.period,
                            source_hash,
                            reason="scheduled",
                        )
                continue
            if delivery_due and not already_delivered:
                reason = "scheduled"
            elif existing is not None:
                reason = "stale"
            else:
                reason = "backfill"
            self.store.enqueue_job(
                period.kind,
                period.period,
                source_hash,
                reason=reason,
            )
        await self.runner.run_next()

    async def run_forever(self) -> None:
        self.store.reset_running_jobs()
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("Review reconciliation pass failed; retrying later")
            await asyncio.sleep(self.poll_interval_seconds)

    def _delivery_at(self, period: ReviewPeriod) -> datetime:
        send_time = self.weekly_time if period.kind == "week" else self.monthly_time
        return datetime.combine(
            period.end_date + timedelta(days=1),
            send_time,
            tzinfo=self.timezone,
        )


def source_hash_for_period(
    period: ReviewPeriod, documents: Sequence[CorpusDocument]
) -> str:
    sources = [
        document
        for document in documents
        if document.source_type == "diary"
        and document.document_date is not None
        and period.start_date <= document.document_date <= period.end_date
    ]
    if not sources:
        return ""
    digest = hashlib.sha256()
    for document in sorted(sources, key=lambda item: item.document_id):
        digest.update(document.document_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.content_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _preserve_first_seen(
    incoming: Sequence[CorpusDocument], existing: Sequence[CorpusDocument]
) -> list[CorpusDocument]:
    existing_by_id = {document.document_id: document for document in existing}
    return [
        replace(
            document,
            first_seen=min(
                document.first_seen,
                existing_by_id.get(document.document_id, document).first_seen,
            ),
        )
        for document in incoming
    ]


def _invalidate_changed_sources(
    store: ReviewStore,
    *,
    previous: Sequence[CorpusDocument],
    current: Sequence[CorpusDocument],
) -> set[tuple[str, str]]:
    current_by_id = {document.document_id: document for document in current}
    invalidated: set[tuple[str, str]] = set()
    for prior in previous:
        latest = current_by_id.get(prior.document_id)
        if latest is not None and (
            latest.content_hash,
            latest.source_type,
            latest.path,
            latest.heading,
            latest.document_date,
        ) == (
            prior.content_hash,
            prior.source_type,
            prior.path,
            prior.heading,
            prior.document_date,
        ):
            continue
        affected = store.invalidate_source(
            prior.document_id,
            latest.content_hash if latest is not None else "deleted",
        )
        for kind, period in affected:
            invalidated.add((kind, period))
            record = store.get_review(kind, period)
            if record is not None:
                store.enqueue_job(
                    kind,
                    period,
                    record.source_hash,
                    reason="stale",
                )
    return invalidated


def start_review_tasks(
    settings: Any, coordinator: ReviewCoordinator
) -> list[asyncio.Task[None]]:
    if not getattr(settings, "reviews_enabled", False):
        return []
    return [asyncio.create_task(coordinator.run_forever())]


async def stop_review_tasks(tasks: Sequence[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass

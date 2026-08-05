from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from dairy_bot.services import DiarySemanticIndexer, SemanticEmbeddingService
from dairy_bot.services import reviews
from dairy_bot.services.semantic_embeddings import (
    E5_MODEL,
    E5_RECIPE_VERSION,
    RAW_RECIPE_VERSION,
)


TZ = ZoneInfo("Europe/Vienna")


def _document(
    document_id: str = "diary:2026-07-31T09:00",
    *,
    day: date = date(2026, 7, 31),
    text: str = "A grounded diary reflection.",
    content_hash: str | None = None,
) -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id=document_id,
        source_type="diary",
        path=f"{day.year}/{day.month:02d}/{day.isoformat()}.md",
        heading="09:00",
        text=text,
        content_hash=content_hash or f"hash:{document_id}",
        document_date=day,
        first_seen=datetime(2026, 8, 4, 12, tzinfo=TZ),
    )


def _record(
    kind: str,
    period: str,
    start: date,
    end: date,
    *,
    title: str,
    source_hash: str,
) -> reviews.ReviewRecord:
    return reviews.ReviewRecord(
        kind=kind,
        period=period,
        start_date=start,
        end_date=end,
        status="ready",
        title=title,
        payload={"paragraphs": [{"text": title, "evidence_refs": []}]},
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        safety_note=None,
        image_path=f"/images/{kind}-{period}-old.jpg",
        image_alt="Old generated poster",
        language="EN",
        model="test/review-model",
        source_hash=source_hash,
    )


def _synthesis(*evidence_refs: str, title: str = "A grounded review"):
    return reviews.ReviewSynthesis(
        title=title,
        paragraphs=[
            reviews.ReviewParagraph(
                text="A cohesive evidence-grounded essay paragraph.",
                evidence_refs=list(evidence_refs),
            )
        ],
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        visual_brief="One precise geometric hinge.",
    )


def _seed_vector(
    store: reviews.ReviewStore,
    document: reviews.CorpusDocument,
    *,
    recipe: str,
    vector: Sequence[float] = (1.0, 0.0),
) -> None:
    store.embeddings.upsert_embedding(
        document_id=document.document_id,
        content_hash=document.content_hash,
        model=E5_MODEL,
        recipe_version=recipe,
        embedding=vector,
    )


def test_AC_2_AC_3_reviews_reuse_active_document_vector_and_queries_follow_active_recipe(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    document = _document()
    store.replace_corpus([document])
    _seed_vector(store, document, recipe=RAW_RECIPE_VERSION)
    provider_calls: list[str] = []

    async def embed(text: str) -> Sequence[float]:
        provider_calls.append(text)
        return [1.0, 0.0]

    embeddings = SemanticEmbeddingService(
        store=store.embeddings,
        embed=embed,
        model=E5_MODEL,
    )
    assert asyncio.run(embeddings.embed_query("raw query")) == (1.0, 0.0)

    indexer = DiarySemanticIndexer(store=store.embeddings, embeddings=embeddings)
    assert asyncio.run(indexer.sync([document])) is True

    class LLM:
        async def plan(self, **kwargs):
            return reviews.ReviewPlan(
                tool_calls=[
                    reviews.ReviewToolCall(
                        tool="search_diary",
                        query="earlier pattern",
                    )
                ]
            )

        async def draft(self, **kwargs):
            assert [item.evidence_id for item in kwargs["context"]] == [
                document.document_id
            ]
            return _synthesis(document.document_id)

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            raise AssertionError("approved synthesis must not be revised")

    service = reviews.ReviewGenerationService(
        store=store,
        llm=LLM(),
        embed=embed,
        embedding_model=E5_MODEL,
        embedding_service=embeddings,
        parallel_client=None,
        language="EN",
        model="test/review-model",
    )
    job = store.enqueue_job(
        "week",
        "2026-07-26",
        "period-source-v1",
        reason="backfill",
    )
    generated = asyncio.run(service.generate(job))

    shared = store.embeddings.list_embeddings()[0]
    review_vector = store.list_embedded_documents()[0]
    assert provider_calls == [
        "raw query",
        f"passage: {document.text}",
        "query: earlier pattern",
    ]
    assert review_vector.embedding == shared.embedding == (1.0, 0.0)
    assert review_vector.content_hash == shared.content_hash == document.content_hash
    assert generated.record.retrieval_model == E5_MODEL
    assert generated.record.retrieval_recipe == E5_RECIPE_VERSION
    assert "weekly_trajectory" not in generated.record.payload


def test_AC_4_e5_cutover_queues_each_legacy_review_once_in_dependency_order(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    document = _document(day=date(2026, 1, 31))
    store.replace_corpus([document])
    _seed_vector(store, document, recipe=RAW_RECIPE_VERSION)
    records = [
        _record(
            "month",
            "2026-01",
            date(2026, 1, 1),
            date(2026, 1, 31),
            title="January",
            source_hash="month-hash",
        ),
        _record(
            "week",
            "2026-01-25",
            date(2026, 1, 25),
            date(2026, 1, 31),
            title="Late January week",
            source_hash="late-week-hash",
        ),
        _record(
            "week",
            "2026-01-11",
            date(2026, 1, 11),
            date(2026, 1, 17),
            title="Early January week",
            source_hash="early-week-hash",
        ),
    ]
    for record in records:
        store.upsert_review(record, sources=[])
    store.record_delivery("week", "2026-01-25", chat_id=42, status="sent")

    async def embed(text: str) -> Sequence[float]:
        return [1.0, 0.0]

    embeddings = SemanticEmbeddingService(
        store=store.embeddings,
        embed=embed,
        model=E5_MODEL,
    )
    corpus_indexer = reviews.ReviewCorpusIndexer(
        store=store,
        embed=embed,
        embedding_model=E5_MODEL,
        embedding_service=embeddings,
        semantic_indexer=DiarySemanticIndexer(
            store=store.embeddings,
            embeddings=embeddings,
        ),
    )

    asyncio.run(corpus_indexer.sync([document]))
    asyncio.run(corpus_indexer.sync([document]))

    assert [
        (job.kind, job.period, job.reason, job.status)
        for job in store.list_jobs()
    ] == [
        ("week", "2026-01-11", "recipe_migration", "pending"),
        ("week", "2026-01-25", "recipe_migration", "pending"),
        ("month", "2026-01", "recipe_migration", "pending"),
    ]
    assert store.list_deliveries() == [
        reviews.TelegramDelivery("week", "2026-01-25", 42, "sent")
    ]


def test_AC_5_same_end_dependency_is_week_to_month_only(tmp_path: Path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    document = _document(day=date(2026, 1, 31))
    store.replace_corpus([document])
    _seed_vector(store, document, recipe=E5_RECIPE_VERSION)
    week = _record(
        "week",
        "2026-01-25",
        date(2026, 1, 25),
        date(2026, 1, 31),
        title="Week ending January 31",
        source_hash="week-hash",
    )
    month = _record(
        "month",
        "2026-01",
        date(2026, 1, 1),
        date(2026, 1, 31),
        title="January",
        source_hash="month-hash",
    )
    store.upsert_review(month, sources=[])
    store.upsert_review(week, sources=[])
    contexts: dict[str, list[str]] = {}

    class LLM:
        async def plan(self, **kwargs):
            return reviews.ReviewPlan(tool_calls=[])

        async def draft(self, **kwargs):
            contexts[kwargs["kind"]] = [
                item.evidence_id for item in kwargs["context"]
            ]
            return _synthesis(title=f"Generated {kwargs['kind']}")

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            raise AssertionError("approved synthesis must not be revised")

    async def embed(text: str) -> Sequence[float]:
        return [1.0, 0.0]

    service = reviews.ReviewGenerationService(
        store=store,
        llm=LLM(),
        embed=embed,
        embedding_model=E5_MODEL,
        parallel_client=None,
        language="EN",
        model="test/review-model",
    )
    week_job = store.enqueue_job(
        "week", "2026-01-25", "week-hash", reason="backfill"
    )
    month_job = store.enqueue_job(
        "month", "2026-01", "month-hash", reason="backfill"
    )
    asyncio.run(service.generate(week_job))
    asyncio.run(service.generate(month_job))

    assert contexts == {
        "week": [],
        "month": ["review:week:2026-01-25"],
    }


def test_AC_6_EC_1_public_evidence_is_temporal_allowlist_and_unknown_ids_are_internal(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    period_document = _document()
    earlier = _document(
        "diary:2026-07-20T09:00",
        day=date(2026, 7, 20),
        text="An earlier form of the same pattern.",
    )
    future = _document(
        "diary:2026-08-03T09:00",
        day=date(2026, 8, 3),
        text="A future entry that must not become public evidence.",
    )
    store.replace_corpus([period_document, earlier, future])
    for document in (period_document, earlier, future):
        _seed_vector(store, document, recipe=E5_RECIPE_VERSION)
    prior = _record(
        "week",
        "2026-07-12",
        date(2026, 7, 12),
        date(2026, 7, 18),
        title="An earlier week",
        source_hash="prior-review-hash",
    )
    store.upsert_review(prior, sources=[])
    prior_id = "review:week:2026-07-12"
    unknown_id = "diary:unknown"

    class LLM:
        async def plan(self, **kwargs):
            return reviews.ReviewPlan(
                tool_calls=[
                    reviews.ReviewToolCall(
                        tool="search_diary",
                        query="recurring pattern",
                    )
                ]
            )

        async def draft(self, **kwargs):
            context_ids = {item.evidence_id for item in kwargs["context"]}
            assert earlier.document_id in context_ids
            assert prior_id in context_ids
            assert future.document_id not in context_ids
            return _synthesis(
                period_document.document_id,
                earlier.document_id,
                prior_id,
                future.document_id,
                unknown_id,
            )

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            raise AssertionError("approved synthesis must not be revised")

    async def embed(text: str) -> Sequence[float]:
        return [1.0, 0.0]

    service = reviews.ReviewGenerationService(
        store=store,
        llm=LLM(),
        embed=embed,
        embedding_model=E5_MODEL,
        parallel_client=None,
        language="EN",
        model="test/review-model",
    )
    job = store.enqueue_job(
        "week", "2026-07-26", "period-source", reason="backfill"
    )
    generated = asyncio.run(service.generate(job))

    assert [
        (source.source_id, source.source_type)
        for source in generated.sources
    ] == [
        (period_document.document_id, "diary"),
        (earlier.document_id, "diary"),
        (prior_id, "review"),
        (future.document_id, "unresolved"),
        (unknown_id, "unresolved"),
    ]
    assert {
        source.source_id
        for source in generated.sources
        if source.source_type in {"diary", "review"}
    } == {period_document.document_id, earlier.document_id, prior_id}


def test_AC_7_recipe_migration_replaces_text_and_image_without_telegram_resend(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    original = _record(
        "week",
        "2026-07-26",
        date(2026, 7, 26),
        date(2026, 8, 1),
        title="Legacy review",
        source_hash="period-source",
    )
    store.upsert_review(original, sources=[])
    store.record_delivery("week", "2026-07-26", chat_id=42, status="sent")
    job = store.enqueue_job(
        "week",
        "2026-07-26",
        "period-source",
        reason="recipe_migration",
    )
    generation_calls: list[int] = []
    image_calls: list[str] = []
    delivery_calls: list[str] = []

    async def generate(current: reviews.GenerationJob) -> reviews.GeneratedReview:
        generation_calls.append(current.job_id)
        return reviews.GeneratedReview(
            record=replace(
                original,
                status="generating",
                title="E5-regenerated review",
                image_path=None,
                retrieval_model=E5_MODEL,
                retrieval_recipe=E5_RECIPE_VERSION,
            ),
            sources=[],
        )

    async def image(record: reviews.ReviewRecord) -> Path:
        image_calls.append(record.title)
        return tmp_path / "week-2026-07-26-job-2.jpg"

    async def deliver(record: reviews.ReviewRecord) -> None:
        delivery_calls.append(record.title)

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=image,
        deliver=deliver,
    )
    assert asyncio.run(runner.run_next()) is True

    stored = store.get_review("week", "2026-07-26")
    assert generation_calls == [job.job_id]
    assert image_calls == ["E5-regenerated review"]
    assert delivery_calls == []
    assert stored is not None
    assert (
        stored.title,
        stored.image_path,
        stored.retrieval_model,
        stored.retrieval_recipe,
    ) == (
        "E5-regenerated review",
        str(tmp_path / "week-2026-07-26-job-2.jpg"),
        E5_MODEL,
        E5_RECIPE_VERSION,
    )
    assert store.list_deliveries() == [
        reviews.TelegramDelivery("week", "2026-07-26", 42, "sent")
    ]


def test_ERR_1_raw_only_index_blocks_llm_and_image_calls(tmp_path: Path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    document = _document()
    store.replace_corpus([document])
    _seed_vector(store, document, recipe=RAW_RECIPE_VERSION)
    llm_calls: list[str] = []
    image_calls: list[str] = []

    class LLM:
        async def plan(self, **kwargs):
            llm_calls.append("plan")
            return reviews.ReviewPlan(tool_calls=[])

        async def draft(self, **kwargs):
            llm_calls.append("draft")
            return _synthesis()

        async def critique(self, **kwargs):
            llm_calls.append("critique")
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            llm_calls.append("revise")
            return _synthesis()

    async def embed(text: str) -> Sequence[float]:
        return [1.0, 0.0]

    service = reviews.ReviewGenerationService(
        store=store,
        llm=LLM(),
        embed=embed,
        embedding_model=E5_MODEL,
        parallel_client=None,
        language="EN",
        model="test/review-model",
    )
    store.enqueue_job(
        "week", "2026-07-26", "period-source", reason="backfill"
    )

    async def image(record: reviews.ReviewRecord) -> Path:
        image_calls.append(record.period)
        return tmp_path / "unexpected.jpg"

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=service.generate,
        generate_image=image,
        deliver=lambda record: asyncio.sleep(0),
    )

    assert asyncio.run(runner.run_next()) is True

    assert llm_calls == []
    assert image_calls == []
    failed = store.list_jobs()[0]
    assert failed.status == "pending"
    assert failed.attempt_count == 1
    assert failed.next_attempt_at is not None

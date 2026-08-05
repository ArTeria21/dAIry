from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

import httpx
from openai import AsyncOpenAI

from dairy_bot.config import Settings
from dairy_bot.services.semantic_embeddings import (
    SemanticIndexUnavailable,
    SemanticEmbeddingService,
    SemanticRuntime,
    build_semantic_runtime,
)

from .coordinator import ReviewCoordinator, ReviewCorpusIndexer, source_hash_for_period
from .corpus import scan_corpus
from .images import OpenRouterImageGenerator
from .models import (
    CorpusDocument,
    GenerationJob,
    ReviewPeriod,
    ReviewRecord,
    ReviewSource,
)
from .operations import GeneratedReview, ReviewJobRunner, ReviewTelegramSender
from .parallel_search import ParallelSearchClient, ParallelSource, ReviewPlannerTools
from .pipeline import (
    OpenRouterReviewLLM,
    ReviewContextItem,
    ReviewGenerationPipeline,
)
from .retrieval import search_corpus
from .store import ReviewStore

Embed = Callable[[str], Awaitable[Sequence[float]]]


class _DisabledParallelRun:
    @property
    def remaining_calls(self) -> int:
        return 0

    async def search(
        self, *, objective: str, search_queries: Sequence[str]
    ) -> list[ParallelSource]:
        return []


class ReviewGenerationService:
    """Turn a durable job into grounded text and its internal source trace."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        llm: Any,
        embed: Embed,
        embedding_model: str,
        parallel_client: ParallelSearchClient | None,
        language: str,
        model: str,
        vault: Path | None = None,
        embedding_service: SemanticEmbeddingService | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.embed = embed
        self.embedding_model = embedding_model
        self.embeddings = embedding_service or SemanticEmbeddingService(
            store=store.embeddings,
            embed=embed,
            model=embedding_model,
        )
        self.parallel_client = parallel_client
        self.language = language
        self.model = model
        self.vault = Path(vault) if vault is not None else None

    async def generate(self, job: GenerationJob) -> GeneratedReview:
        active_generation = self.store.embeddings.get_active_generation()
        expected_recipe = self.embeddings.recipe_version
        if (
            active_generation is None
            or active_generation.status != "ready"
            or active_generation.model != self.embedding_model
            or active_generation.recipe_version != expected_recipe
        ):
            raise SemanticIndexUnavailable(
                "Reviews require a compatible active semantic generation"
            )
        period = _period_from_job(job)
        all_documents = self.store.list_corpus_documents()
        period_documents = [
            document
            for document in all_documents
            if document.source_type == "diary"
            and document.document_date is not None
            and period.start_date <= document.document_date <= period.end_date
        ]
        stats = {
            "entry_count": len(period_documents),
            "active_days": len(
                {document.document_date for document in period_documents}
            ),
        }
        prior_review_context = _prior_review_context(
            self.store.list_reviews(),
            period=period,
        )

        async def diary_search(query: str, cutoff: date) -> list[ReviewContextItem]:
            query_embedding = await self.embeddings.embed_query(query)
            hits = search_corpus(
                query_embedding,
                self.store.list_embedded_documents(),
                cutoff=cutoff,
                embedding_model=self.embedding_model,
                limit=5,
            )
            return [ReviewContextItem.from_document(hit.document) for hit in hits]

        if self.parallel_client is None:
            parallel_run: Any = _DisabledParallelRun()
        else:
            parallel_run = self.parallel_client.begin_run()
        tools = ReviewPlannerTools(
            cutoff=period.end_date,
            diary_search=diary_search,
            parallel_run=parallel_run,
        )
        result = await ReviewGenerationPipeline(llm=self.llm, tools=tools).generate(
            kind=job.kind,
            review_end=period.end_date,
            documents=period_documents,
            deterministic_stats=stats,
            initial_context=prior_review_context,
        )
        synthesis = result.synthesis
        payload = {
            "paragraphs": [
                paragraph.model_dump(mode="json")
                for paragraph in synthesis.paragraphs
            ],
            "visual_brief": synthesis.visual_brief,
            "counts": stats,
        }
        record = ReviewRecord(
            kind=job.kind,
            period=job.period,
            start_date=period.start_date,
            end_date=period.end_date,
            status="generating",
            title=synthesis.title,
            payload=payload,
            telegram_caption=synthesis.telegram_caption,
            reflection_question=synthesis.reflection_question,
            safety_note=synthesis.safety_note,
            image_path=None,
            image_alt=(
                f"Archival abstract poster for the "
                f"{'weekly' if job.kind == 'week' else 'monthly'} review."
            ),
            language=self.language,
            model=self.model,
            source_hash=job.source_hash,
            retrieval_model=active_generation.model,
            retrieval_recipe=active_generation.recipe_version,
        )
        sources = _review_sources(
            synthesis.paragraphs,
            period_documents,
            result.used_evidence,
        )
        return GeneratedReview(record=record, sources=sources)

    def current_source_hash(self, kind: str, period_id: str) -> str:
        job = GenerationJob(0, kind, period_id, "", "stale", "pending")
        period = _period_from_job(job)
        documents = self.store.list_corpus_documents()
        if self.vault is not None:
            documents = scan_corpus(
                self.vault,
                first_seen=datetime.now(timezone.utc),
            )
        return source_hash_for_period(period, documents)


@dataclass(slots=True)
class ReviewRuntime:
    store: ReviewStore
    coordinator: ReviewCoordinator
    image_generator: OpenRouterImageGenerator
    telegram_sender: ReviewTelegramSender
    generation_service: ReviewGenerationService
    http_client: Any
    openai_client: Any
    semantic_runtime: SemanticRuntime
    _closed: bool = False

    async def run_forever(self) -> None:
        try:
            await self.coordinator.run_forever()
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_http = getattr(self.http_client, "aclose", None)
        if close_http is not None:
            await close_http()
        await self.semantic_runtime.close()


def build_review_runtime(
    settings: Settings,
    bot: Any,
    *,
    http_client: Any | None = None,
    openai_client: Any | None = None,
) -> ReviewRuntime | None:
    if not settings.reviews_enabled:
        return None
    openai = openai_client or AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )
    http = http_client or httpx.AsyncClient()
    semantic_runtime = build_semantic_runtime(settings, openai_client=openai)
    if semantic_runtime is None:
        raise RuntimeError("Reviews require the semantic embedding runtime")
    store = ReviewStore(
        settings.reviews_db_path,
        embeddings_db_path=settings.embeddings_db_path,
        semantic_store=semantic_runtime.store,
    )

    llm = OpenRouterReviewLLM(
        client=openai,
        model=settings.review_model_name,
        language=settings.language,
    )
    parallel = None
    if settings.parallel_api_key is not None and settings.review_max_search_calls > 0:
        parallel = ParallelSearchClient(
            api_key=settings.parallel_api_key.get_secret_value(),
            http_client=http,
            client_model=settings.review_model_name,
            max_calls=settings.review_max_search_calls,
        )
    generation = ReviewGenerationService(
        store=store,
        llm=llm,
        embed=semantic_runtime.embeddings.embed,
        embedding_model=settings.embedding_model_name,
        parallel_client=parallel,
        language=settings.language,
        model=settings.review_model_name,
        vault=settings.journal_dir,
        embedding_service=semantic_runtime.embeddings,
    )
    image_generator = OpenRouterImageGenerator(
        api_key=settings.openrouter_api_key.get_secret_value(),
        http_client=http,
        output_dir=settings.review_assets_dir,
        primary_model=settings.review_image_model_name,
        fallback_model=settings.review_image_fallback_model_name,
    )
    telegram_sender = ReviewTelegramSender(
        bot=bot,
        public_base_url=settings.web_public_base_url,
    )

    async def generate_image(record: ReviewRecord, job: GenerationJob):
        return await image_generator.generate(
            kind=record.kind,
            period=record.period,
            visual_brief=str(record.payload["visual_brief"]),
            job_id=job.job_id,
        )

    async def deliver(record: ReviewRecord):
        prior = store.get_delivery(
            record.kind, record.period, settings.allowed_user_id
        )
        if prior is not None and prior.status in {"sent", "delivery_unknown"}:
            return prior
        result = await telegram_sender.send(record, chat_id=settings.allowed_user_id)
        store.record_delivery(
            record.kind,
            record.period,
            chat_id=settings.allowed_user_id,
            status=result.status,
        )
        return result

    runner = ReviewJobRunner(
        store=store,
        generate_review=generation.generate,
        generate_image=generate_image,
        deliver=deliver,
        current_source_hash=generation.current_source_hash,
    )
    indexer = ReviewCorpusIndexer(
        store=store,
        embed=semantic_runtime.embeddings.embed,
        embedding_model=settings.embedding_model_name,
        embedding_service=semantic_runtime.embeddings,
        semantic_indexer=semantic_runtime.indexer,
    )
    coordinator = ReviewCoordinator(
        vault=settings.journal_dir,
        store=store,
        timezone=settings.timezone,
        weekly_time=settings.review_weekly_send_time,
        monthly_time=settings.review_monthly_send_time,
        runner=runner,
        indexer=indexer,
    )
    return ReviewRuntime(
        store=store,
        coordinator=coordinator,
        image_generator=image_generator,
        telegram_sender=telegram_sender,
        generation_service=generation,
        http_client=http,
        openai_client=openai,
        semantic_runtime=semantic_runtime,
    )


def _period_from_job(job: GenerationJob) -> ReviewPeriod:
    if job.kind == "week":
        start = date.fromisoformat(job.period)
        return ReviewPeriod("week", job.period, start, start + timedelta(days=6))
    if job.kind == "month":
        start = date.fromisoformat(f"{job.period}-01")
        next_month = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return ReviewPeriod("month", job.period, start, next_month - timedelta(days=1))
    raise ValueError(f"Unsupported review kind: {job.kind}")


def _review_sources(
    paragraphs: Sequence[Any],
    period_documents: Sequence[CorpusDocument],
    context: Sequence[ReviewContextItem],
) -> list[ReviewSource]:
    public_by_id = {
        document.document_id: document for document in period_documents
    }
    context_by_id = {item.evidence_id: item for item in context}
    ordered_ids: list[str] = []
    for paragraph in paragraphs:
        for evidence_id in paragraph.evidence_refs:
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    # External results are retained in the private trace even though they can never
    # be public paragraph references.
    ordered_ids.extend(
        item.evidence_id
        for item in context
        if item.internal_only and item.evidence_id not in ordered_ids
    )
    sources: list[ReviewSource] = []
    for position, evidence_id in enumerate(ordered_ids):
        document = public_by_id.get(evidence_id)
        item = context_by_id.get(evidence_id)
        if document is not None:
            sources.append(
                ReviewSource(
                    source_id=evidence_id,
                    source_type=document.source_type,
                    source_hash=document.content_hash,
                    label=_document_label(document),
                    position=position,
                )
            )
        elif item is not None:
            sources.append(
                ReviewSource(
                    source_id=evidence_id,
                    source_type=item.source_type,
                    source_hash=item.source_hash or "internal",
                    label=item.label,
                    position=position,
                )
            )
        else:
            sources.append(
                ReviewSource(
                    source_id=evidence_id,
                    source_type="unresolved",
                    source_hash="unresolved",
                    label=evidence_id,
                    position=position,
                )
            )
    return sources


def _prior_review_context(
    records: Sequence[ReviewRecord],
    *,
    period: ReviewPeriod,
    limit: int = 8,
) -> list[ReviewContextItem]:
    target_key = _review_dependency_key(
        period.end_date,
        period.kind,
        period.period,
    )
    eligible = [
        record
        for record in records
        if (record.kind, record.period) != (period.kind, period.period)
        and _review_dependency_key(
            record.end_date,
            record.kind,
            record.period,
        )
        < target_key
    ]
    recent = sorted(
        eligible,
        key=lambda record: (record.end_date, record.kind, record.period),
        reverse=True,
    )[:limit]
    return [
        ReviewContextItem(
            evidence_id=f"review:{record.kind}:{record.period}",
            source_type="review",
            label=record.title,
            text=_review_context_text(record),
            internal_only=False,
            document_date=record.end_date,
            source_hash=record.source_hash,
        )
        for record in sorted(
            recent,
            key=lambda record: (record.end_date, record.kind, record.period),
        )
    ]


def _review_dependency_key(
    end_date: date,
    kind: str,
    period: str,
) -> tuple[date, int, str]:
    return end_date, 0 if kind == "week" else 1, period


def _review_context_text(record: ReviewRecord) -> str:
    paragraphs = record.payload.get("paragraphs", [])
    texts = [
        str(paragraph["text"])
        for paragraph in paragraphs
        if isinstance(paragraph, dict) and isinstance(paragraph.get("text"), str)
    ]
    return "\n\n".join([record.title, *texts, record.reflection_question])


def _document_label(document: CorpusDocument) -> str:
    if document.source_type == "diary" and document.document_date:
        suffix = f", {document.heading}" if document.heading else ""
        return f"{document.document_date.isoformat()}{suffix}"
    return f"{document.path}{' · ' + document.heading if document.heading else ''}"

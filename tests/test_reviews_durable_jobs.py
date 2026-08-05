from __future__ import annotations

import asyncio
import base64
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.services import reviews


TZ = ZoneInfo("Europe/Vienna")


def _record(
    *,
    title: str = "Existing review",
    source_hash: str = "source-v1",
    image_path: str | None = None,
) -> reviews.ReviewRecord:
    return reviews.ReviewRecord(
        kind="week",
        period="2026-08-02",
        start_date=date(2026, 8, 2),
        end_date=date(2026, 8, 8),
        status="ready",
        title=title,
        payload={
            "paragraphs": [
                {"text": "A grounded paragraph.", "evidence_refs": []}
            ],
            "visual_brief": "One central hinge.",
        },
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        safety_note=None,
        image_path=image_path,
        image_alt="A generated poster" if image_path else None,
        language="EN",
        model="test/review-model",
        source_hash=source_hash,
        retrieval_model="intfloat/multilingual-e5-large",
        retrieval_recipe="e5-query-passage-v1",
    )


def test_AC_1_AC_2_generation_failures_persist_backoff_and_fifth_attempt_is_terminal(
    tmp_path: Path,
):
    db_path = tmp_path / "reviews.sqlite3"
    store = reviews.ReviewStore(db_path)
    queued = store.enqueue_job(
        "week", "2026-08-02", "source-v1", reason="backfill"
    )
    current = [datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)]
    generation_calls: list[int] = []
    image_calls: list[str] = []

    async def fail(job: reviews.GenerationJob) -> reviews.GeneratedReview:
        generation_calls.append(job.attempt_count)
        raise RuntimeError("provider unavailable")

    async def image(record: reviews.ReviewRecord) -> Path:
        image_calls.append(record.period)
        return tmp_path / "unexpected.jpg"

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=fail,
        generate_image=image,
        deliver=lambda record: asyncio.sleep(0),
        now=lambda: current[0],
    )
    assert asyncio.run(runner.run_next()) is True
    first = store.get_job(queued.job_id)
    assert first is not None
    assert (
        first.status,
        first.attempt_count,
        first.next_attempt_at,
        first.last_error,
    ) == (
        "pending",
        1,
        current[0] + timedelta(minutes=1),
        "provider unavailable",
    )
    assert asyncio.run(runner.run_next()) is False

    restarted_store = reviews.ReviewStore(db_path)
    restarted_runner = reviews.ReviewJobRunner(
        store=restarted_store,
        generate_review=fail,
        generate_image=image,
        deliver=lambda record: asyncio.sleep(0),
        now=lambda: current[0],
    )
    expected_delays = [
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
    ]
    for expected_attempt, delay in enumerate(expected_delays, start=2):
        pending = restarted_store.get_job(queued.job_id)
        assert pending is not None and pending.next_attempt_at is not None
        current[0] = pending.next_attempt_at
        assert asyncio.run(restarted_runner.run_next()) is True
        retried = restarted_store.get_job(queued.job_id)
        assert retried is not None
        assert (
            retried.status,
            retried.attempt_count,
            retried.next_attempt_at,
        ) == (
            "pending",
            expected_attempt,
            current[0] + delay,
        )

    fourth = restarted_store.get_job(queued.job_id)
    assert fourth is not None and fourth.next_attempt_at is not None
    current[0] = fourth.next_attempt_at
    assert asyncio.run(restarted_runner.run_next()) is True
    terminal = reviews.ReviewStore(db_path).get_job(queued.job_id)
    assert terminal is not None
    assert (
        terminal.status,
        terminal.attempt_count,
        terminal.next_attempt_at,
        terminal.last_error,
    ) == ("failed", 5, None, "provider unavailable")
    assert reviews.ReviewStore(db_path).claim_next_job(now=current[0]) is None
    assert generation_calls == [0, 1, 2, 3, 4]
    assert image_calls == []


class _ParallelResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "results": [
                {
                    "title": "One research result",
                    "url": "https://example.test/result",
                    "excerpts": ["One useful excerpt."],
                }
            ]
        }


class _ParallelHTTP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _ParallelResponse:
        self.calls.append({"url": url, **kwargs})
        return _ParallelResponse()


class _Completions:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_AC_3_planner_receives_actual_parallel_budget_and_overflow_calls_are_skipped():
    plan = {
        "tool_calls": [
            {
                "tool": "parallel_search",
                "objective": f"Research mechanism {index}",
                "search_queries": [f"mechanism {index} systematic review"],
            }
            for index in range(3)
        ]
    }
    synthesis = {
        "title": "A grounded review",
        "paragraphs": [{"text": "One paragraph.", "evidence_refs": []}],
        "telegram_caption": "C" * 600,
        "reflection_question": "What remains open?",
        "safety_note": None,
        "visual_brief": "One central signal.",
    }
    completions = _Completions(
        [plan, synthesis, {"approved": True, "issues": []}]
    )
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="EN",
    )
    parallel_http = _ParallelHTTP()
    run = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=parallel_http,
        client_model="openai/gpt-5.6-terra",
        max_calls=1,
    ).begin_run()

    async def diary_search(query: str, cutoff: date):
        return []

    tools = reviews.ReviewPlannerTools(
        cutoff=date(2026, 8, 8),
        diary_search=diary_search,
        parallel_run=run,
    )
    result = asyncio.run(
        reviews.ReviewGenerationPipeline(llm=llm, tools=tools).generate(
            kind="week",
            review_end=date(2026, 8, 8),
            documents=[],
            deterministic_stats={"entry_count": 1, "active_days": 1},
        )
    )

    planner_prompt = completions.calls[0]["messages"][0]["content"]
    planner_payload = json.loads(completions.calls[0]["messages"][1]["content"])
    assert "Parallel Search budget for this run: 1 call" in planner_prompt
    assert planner_payload["parallel_search_budget"] == 1
    assert len(parallel_http.calls) == 1
    assert [item.source_type for item in result.used_evidence] == ["external"]


def test_AC_5_due_ready_review_is_delivered_even_when_semantic_rebuild_is_unavailable(
    tmp_path: Path,
):
    vault = tmp_path / "vault"
    path = vault / "2026" / "08" / "2026-08-07.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ndate: 2026-08-07\ntype: daily\n---\n"
        "# 2026-08-07\n\n## 09:00\n\nA closed weekly reflection.\n",
        encoding="utf-8",
    )
    due = datetime(2026, 8, 9, 9, 0, tzinfo=TZ)
    documents = reviews.scan_corpus(vault, first_seen=due)
    period = reviews.ReviewPeriod(
        "week", "2026-08-02", date(2026, 8, 2), date(2026, 8, 8)
    )
    source_hash = reviews.source_hash_for_period(period, documents)
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.replace_corpus(documents)
    store.upsert_review(
        _record(source_hash=source_hash),
        sources=[],
    )
    store.set_metadata("reviews_activated_at", "2026-07-01T00:00:00+02:00")

    class UnavailableIndexer:
        ready = False

        def __init__(self) -> None:
            self.calls = 0

        async def sync(self, incoming):
            self.calls += 1
            self.ready = False
            return list(incoming)

    class Runner:
        def __init__(self) -> None:
            self.delivered: list[reviews.ReviewRecord] = []
            self.run_calls = 0

        async def deliver_ready(self, record: reviews.ReviewRecord) -> None:
            self.delivered.append(record)
            store.record_delivery(
                record.kind,
                record.period,
                chat_id=42,
                status="sent",
            )

        async def run_next(self) -> bool:
            self.run_calls += 1
            return False

    indexer = UnavailableIndexer()
    runner = Runner()
    coordinator = reviews.ReviewCoordinator(
        vault=vault,
        store=store,
        timezone=TZ,
        weekly_time=time(9),
        monthly_time=time(10),
        runner=runner,
        indexer=indexer,
    )

    asyncio.run(coordinator.reconcile_once(now=due))

    assert [record.source_hash for record in runner.delivered] == [source_hash]
    assert indexer.calls == 1
    assert runner.run_calls == 0
    assert store.list_deliveries() == [
        reviews.TelegramDelivery("week", "2026-08-02", 42, "sent")
    ]


class _ImageResponse:
    def __init__(self, image: bytes) -> None:
        self.image = image

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "data": [
                {
                    "b64_json": base64.b64encode(self.image).decode("ascii"),
                    "media_type": "image/jpeg",
                }
            ]
        }


class _ImageHTTP:
    def __init__(self, image: bytes) -> None:
        self.image = image

    async def post(self, url: str, **kwargs: Any) -> _ImageResponse:
        return _ImageResponse(self.image)


@pytest.mark.parametrize("reason", ["scheduled", "regenerate"])
def test_AC_6_superseded_generation_never_replaces_active_review_or_jpeg(
    tmp_path: Path,
    reason: str,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    old_image = tmp_path / "active-old.jpg"
    old_image.write_bytes(b"\xff\xd8old-active\xff\xd9")
    existing = _record(
        source_hash="existing-source" if reason == "scheduled" else "source-v1",
        image_path=str(old_image),
    )
    store.upsert_review(existing, sources=[])
    if reason == "scheduled":
        job = store.enqueue_job(
            "week", "2026-08-02", "source-v1", reason="scheduled"
        )
    else:
        job = store.enqueue_regeneration("week", "2026-08-02", "source-v1")
    generator = reviews.OpenRouterImageGenerator(
        api_key="secret",
        http_client=_ImageHTTP(b"\xff\xd8new-generated\xff\xd9"),
        output_dir=tmp_path,
    )
    delivery_calls: list[str] = []

    async def generate(current: reviews.GenerationJob) -> reviews.GeneratedReview:
        return reviews.GeneratedReview(
            record=_record(
                title="Superseded new review",
                source_hash=current.source_hash,
                image_path=None,
            ),
            sources=[],
        )

    async def generate_image(
        record: reviews.ReviewRecord,
        current: reviews.GenerationJob,
    ) -> Path | None:
        return await generator.generate(
            kind=record.kind,
            period=record.period,
            visual_brief=str(record.payload["visual_brief"]),
            job_id=current.job_id,
        )

    async def deliver(record: reviews.ReviewRecord) -> None:
        delivery_calls.append(record.title)

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=generate_image,
        deliver=deliver,
        current_source_hash=lambda kind, period: "source-v2",
    )
    assert asyncio.run(runner.run_next()) is True

    generated_path = tmp_path / f"week-2026-08-02-job-{job.job_id}.jpg"
    assert store.get_review("week", "2026-08-02") == existing
    assert old_image.read_bytes() == b"\xff\xd8old-active\xff\xd9"
    assert generated_path.read_bytes() == b"\xff\xd8new-generated\xff\xd9"
    assert store.get_job(job.job_id).status == "superseded"
    assert [
        (item.source_hash, item.reason, item.status)
        for item in store.list_jobs()
        if item.job_id != job.job_id
    ] == [("source-v2", "stale", "pending")]
    assert delivery_calls == []


def test_AC_7_claim_priority_is_scheduled_regenerate_recipe_migration_backfill(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    backfill = store.enqueue_job(
        "week", "2026-07-05", "backfill", reason="backfill"
    )
    recipe = store.enqueue_job(
        "week", "2026-07-12", "recipe", reason="recipe_migration"
    )
    regeneration = store.enqueue_regeneration(
        "week", "2026-07-19", "regenerate"
    )
    scheduled = store.enqueue_job(
        "week", "2026-07-26", "scheduled", reason="scheduled"
    )

    claimed: list[int] = []
    for _ in range(4):
        job = store.claim_next_job()
        assert job is not None
        claimed.append(job.job_id)
        store.set_job_status(job.job_id, "complete")

    assert claimed == [
        scheduled.job_id,
        regeneration.job_id,
        recipe.job_id,
        backfill.job_id,
    ]

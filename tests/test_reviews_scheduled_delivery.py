from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dairy_bot.services import reviews


def _record(
    *,
    title: str,
    status: str,
    source_hash: str,
    image_path: str | None,
    period: str = "2026-07-26",
    start_date: date = date(2026, 7, 26),
    end_date: date = date(2026, 8, 1),
) -> reviews.ReviewRecord:
    return reviews.ReviewRecord(
        kind="week",
        period=period,
        start_date=start_date,
        end_date=end_date,
        status=status,
        title=title,
        payload={
            "paragraphs": [
                {
                    "text": "A grounded paragraph.",
                    "evidence_refs": ["diary:2026-07-31T09:00"],
                }
            ]
        },
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        safety_note=None,
        image_path=image_path,
        image_alt="A generated review poster",
        language="EN",
        model="test/model",
        source_hash=source_hash,
    )


def _source() -> reviews.ReviewSource:
    return reviews.ReviewSource(
        source_id="diary:2026-07-31T09:00",
        source_type="diary",
        source_hash="entry-v1",
        label="2026-07-31, 09:00",
        position=0,
    )


def test_AC_1_scheduled_job_delivers_matching_ready_backfill_without_regeneration(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    source_hash = "period-source-v1"
    backfill = store.enqueue_job(
        "week", "2026-07-26", source_hash, reason="backfill"
    )
    store.set_job_status(backfill.job_id, "complete")
    original = _record(
        title="Original backfill review",
        status="ready",
        source_hash=source_hash,
        image_path=str(tmp_path / "original.jpg"),
    )
    original_source = _source()
    store.upsert_review(original, sources=[original_source])
    stored_before = store.get_review("week", "2026-07-26")
    scheduled = store.enqueue_job(
        "week", "2026-07-26", source_hash, reason="scheduled"
    )
    generate_calls: list[int] = []
    image_calls: list[str] = []
    deliveries: list[reviews.ReviewRecord] = []

    async def generate(job: reviews.GenerationJob) -> reviews.GeneratedReview:
        generate_calls.append(job.job_id)
        return reviews.GeneratedReview(
            record=_record(
                title="Unexpected regenerated review",
                status="generating",
                source_hash=source_hash,
                image_path=None,
            ),
            sources=[],
        )

    async def generate_image(record: reviews.ReviewRecord) -> Path:
        image_calls.append(record.title)
        return tmp_path / "unexpected-new.jpg"

    async def deliver(record: reviews.ReviewRecord) -> None:
        deliveries.append(record)

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=generate_image,
        deliver=deliver,
    )

    assert asyncio.run(runner.run_next()) is True

    assert generate_calls == []
    assert image_calls == []
    assert deliveries == [stored_before]
    assert store.get_review("week", "2026-07-26") == stored_before
    assert store.list_review_sources("week", "2026-07-26") == [original_source]
    assert store.get_job(scheduled.job_id).status == "complete"


def test_EC_1_scheduled_job_without_matching_ready_review_generates_then_delivers(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    source_hash = "period-source-v1"
    scheduled = store.enqueue_job(
        "week", "2026-07-26", source_hash, reason="scheduled"
    )
    generation_calls: list[int] = []
    image_calls: list[str] = []
    deliveries: list[reviews.ReviewRecord] = []
    generated = _record(
        title="Fresh scheduled review",
        status="generating",
        source_hash=source_hash,
        image_path=None,
    )

    async def generate(job: reviews.GenerationJob) -> reviews.GeneratedReview:
        generation_calls.append(job.job_id)
        return reviews.GeneratedReview(record=generated, sources=[_source()])

    async def generate_image(record: reviews.ReviewRecord) -> Path:
        image_calls.append(record.title)
        return tmp_path / "fresh.jpg"

    async def deliver(record: reviews.ReviewRecord) -> None:
        deliveries.append(record)

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=generate_image,
        deliver=deliver,
    )

    assert asyncio.run(runner.run_next()) is True

    expected = _record(
        title="Fresh scheduled review",
        status="ready",
        source_hash=source_hash,
        image_path=str(tmp_path / "fresh.jpg"),
    )
    assert generation_calls == [scheduled.job_id]
    assert image_calls == ["Fresh scheduled review"]
    assert deliveries == [expected]
    assert store.get_review("week", "2026-07-26") == expected
    assert store.list_review_sources("week", "2026-07-26") == [_source()]
    assert store.get_job(scheduled.job_id).status == "complete"


def test_AC_2_coordinator_delivers_due_ready_backfill_once_without_scheduled_job(
    tmp_path: Path,
):
    timezone = ZoneInfo("Europe/Vienna")
    vault = tmp_path / "vault"
    daily = vault / "2026" / "08" / "2026-08-07.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        "---\ndate: 2026-08-07\ntype: daily\n---\n"
        "# 2026-08-07\n\n## 09:00\n\nA closed weekly reflection.\n",
        encoding="utf-8",
    )
    due = datetime(2026, 8, 9, 9, 0, tzinfo=timezone)
    documents = reviews.scan_corpus(vault, first_seen=due)
    period = reviews.ReviewPeriod(
        "week", "2026-08-02", date(2026, 8, 2), date(2026, 8, 8)
    )
    source_hash = reviews.source_hash_for_period(period, documents)
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.replace_corpus(documents)
    store.set_metadata("reviews_activated_at", "2026-07-01T00:00:00+02:00")
    backfill = store.enqueue_job(
        "week", period.period, source_hash, reason="backfill"
    )
    store.set_job_status(backfill.job_id, "complete")
    ready = _record(
        title="Already generated backfill review",
        status="ready",
        source_hash=source_hash,
        image_path=str(tmp_path / "ready.jpg"),
        period=period.period,
        start_date=period.start_date,
        end_date=period.end_date,
    )
    ready_source = reviews.ReviewSource(
        source_id="diary:2026-08-07T09:00",
        source_type="diary",
        source_hash=documents[0].content_hash,
        label="2026-08-07, 09:00",
        position=0,
    )
    store.upsert_review(ready, sources=[ready_source])
    stored_before = store.get_review("week", period.period)

    class Runner:
        def __init__(self) -> None:
            self.deliveries: list[reviews.ReviewRecord] = []
            self.generated_jobs: list[reviews.GenerationJob] = []

        async def deliver_ready(self, record: reviews.ReviewRecord) -> None:
            self.deliveries.append(record)
            store.record_delivery(
                record.kind,
                record.period,
                chat_id=42,
                status="sent",
            )

        async def run_next(self) -> bool:
            job = store.claim_next_job()
            if job is None:
                return False
            self.generated_jobs.append(job)
            store.set_job_status(job.job_id, "complete")
            return True

    runner = Runner()
    coordinator = reviews.ReviewCoordinator(
        vault=vault,
        store=store,
        timezone=timezone,
        weekly_time=time(9, 0),
        monthly_time=time(10, 0),
        runner=runner,
    )

    async def reconcile_twice() -> None:
        await coordinator.reconcile_once(now=due)

        assert runner.deliveries == [stored_before]
        assert runner.generated_jobs == []
        assert store.list_deliveries() == [
            reviews.TelegramDelivery("week", period.period, 42, "sent")
        ]
        assert store.list_jobs() == [
            reviews.GenerationJob(
                backfill.job_id,
                "week",
                period.period,
                source_hash,
                "backfill",
                "complete",
            )
        ]
        assert store.get_review("week", period.period) == stored_before
        assert store.list_review_sources("week", period.period) == [ready_source]

        await coordinator.reconcile_once(
            now=datetime(2026, 8, 9, 9, 5, tzinfo=timezone)
        )

    asyncio.run(reconcile_twice())

    assert runner.deliveries == [stored_before]
    assert runner.generated_jobs == []
    assert len(store.list_deliveries()) == 1
    assert store.get_review("week", period.period) == stored_before


def test_AC_3_due_changed_review_queues_scheduled_job_with_current_hash(
    tmp_path: Path,
):
    timezone = ZoneInfo("Europe/Vienna")
    vault = tmp_path / "vault"
    daily = vault / "2026" / "08" / "2026-08-07.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        "---\ndate: 2026-08-07\ntype: daily\n---\n"
        "# 2026-08-07\n\n## 09:00\n\nThe newly changed weekly source.\n",
        encoding="utf-8",
    )
    due = datetime(2026, 8, 9, 9, 0, tzinfo=timezone)
    period = reviews.ReviewPeriod(
        "week", "2026-08-02", date(2026, 8, 2), date(2026, 8, 8)
    )
    current_hash = reviews.source_hash_for_period(
        period,
        reviews.scan_corpus(vault, first_seen=due),
    )
    old_hash = "old-period-source-hash"
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.set_metadata("reviews_activated_at", "2026-07-01T00:00:00+02:00")
    existing = _record(
        title="Outdated ready review",
        status="ready",
        source_hash=old_hash,
        image_path=str(tmp_path / "old.jpg"),
        period=period.period,
        start_date=period.start_date,
        end_date=period.end_date,
    )
    store.upsert_review(existing, sources=[])

    class IdleRunner:
        def __init__(self) -> None:
            self.deliveries: list[reviews.ReviewRecord] = []

        async def deliver_ready(self, record: reviews.ReviewRecord) -> None:
            self.deliveries.append(record)

        async def run_next(self) -> bool:
            return False

    runner = IdleRunner()
    coordinator = reviews.ReviewCoordinator(
        vault=vault,
        store=store,
        timezone=timezone,
        weekly_time=time(9, 0),
        monthly_time=time(10, 0),
        runner=runner,
    )

    asyncio.run(coordinator.reconcile_once(now=due))

    assert current_hash and current_hash != old_hash
    assert runner.deliveries == []
    assert store.list_deliveries() == []
    assert store.list_jobs() == [
        reviews.GenerationJob(
            1,
            "week",
            period.period,
            current_hash,
            "scheduled",
            "pending",
        )
    ]
    assert store.get_review("week", period.period) == existing

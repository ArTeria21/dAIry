from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from dairy_bot.config import Settings
from dairy_bot.services import reviews

TZ = ZoneInfo("Europe/Vienna")


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "BOT_TOKEN": "123:test",
        "ALLOWED_USER_ID": 42,
        "OPENROUTER_API_KEY": "sk-test",
        "JOURNAL_DIR": tmp_path,
        "REVIEW_IMAGE_MODEL_NAME": "test/primary-image",
        "REVIEW_IMAGE_FALLBACK_MODEL_NAME": "test/fallback-image",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _document(*, content_hash: str, first_seen: datetime) -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id="diary:2026-07-31T09:00",
        source_type="diary",
        path="2026/07/2026-07-31.md",
        heading="09:00",
        text=f"Text {content_hash}",
        content_hash=content_hash,
        document_date=date(2026, 7, 31),
        first_seen=first_seen,
    )


def _write_daily(root: Path) -> None:
    path = root / "2026" / "07" / "2026-07-31.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ndate: 2026-07-31\ntype: daily\n---\n# 2026-07-31\n\n"
        "## 09:00\n\nA substantive reflection.\n",
        encoding="utf-8",
    )


def test_AC_3b_1_review_settings_have_locked_defaults_and_parse_overrides(tmp_path):
    defaults = _settings(tmp_path)
    custom = _settings(
        tmp_path,
        REVIEWS_ENABLED="true",
        WEB_PUBLIC_BASE_URL="https://diary.example.org",
        REVIEW_WEEKLY_SEND_TIME="08:30",
        REVIEW_MONTHLY_SEND_TIME="11:15",
        REVIEW_MAX_SEARCH_CALLS="4",
        EMBEDDINGS_DB_PATH=tmp_path / "semantic.sqlite3",
        REVIEW_IMAGE_MODEL_NAME="custom/primary-image",
        REVIEW_IMAGE_FALLBACK_MODEL_NAME="custom/fallback-image",
    )

    assert defaults.reviews_enabled is False
    assert defaults.reviews_db_path == Path("data/reviews.sqlite3")
    assert defaults.review_assets_dir == Path("data/review_images")
    assert defaults.embeddings_db_path == Path("data/embeddings.sqlite3")
    assert defaults.review_model_name == "openai/gpt-5.6-terra"
    assert defaults.review_image_model_name == "test/primary-image"
    assert defaults.review_image_fallback_model_name == "test/fallback-image"
    assert defaults.review_max_search_calls == 6
    assert defaults.review_weekly_send_time.isoformat(timespec="minutes") == "09:00"
    assert defaults.review_monthly_send_time.isoformat(timespec="minutes") == "10:00"
    assert custom.reviews_enabled is True
    assert custom.review_weekly_send_time.isoformat(timespec="minutes") == "08:30"
    assert custom.review_monthly_send_time.isoformat(timespec="minutes") == "11:15"
    assert custom.review_max_search_calls == 4
    assert custom.embeddings_db_path == tmp_path / "semantic.sqlite3"
    assert custom.review_image_model_name == "custom/primary-image"
    assert custom.review_image_fallback_model_name == "custom/fallback-image"


def test_AC_3b_2_corpus_indexer_caches_embedding_metadata_and_first_seen(tmp_path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    calls: list[str] = []

    async def embed(text: str):
        calls.append(text)
        return [1.0, 0.5]

    indexer = reviews.ReviewCorpusIndexer(
        store=store, embed=embed, embedding_model="embed-v1"
    )
    first = _document(
        content_hash="v1", first_seen=datetime(2026, 8, 4, 12, tzinfo=TZ)
    )
    seen_later = _document(
        content_hash="v1", first_seen=datetime(2026, 8, 5, 12, tzinfo=TZ)
    )
    changed = _document(
        content_hash="v2", first_seen=datetime(2026, 8, 6, 12, tzinfo=TZ)
    )

    asyncio.run(indexer.sync([first]))
    asyncio.run(indexer.sync([seen_later]))
    asyncio.run(indexer.sync([changed]))

    stored = store.list_corpus_documents()[0]
    embeddings = store.list_embedded_documents()
    assert calls == ["Text v1", "Text v2"]
    assert stored.first_seen == first.first_seen
    assert len(embeddings) == 1
    assert embeddings[0].embedding == (1.0, 0.5)
    assert embeddings[0].embedding_model == "embed-v1"
    assert embeddings[0].embedding_dimension == 2
    assert embeddings[0].content_hash == "v2"


class _IdleRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run_next(self) -> bool:
        self.calls += 1
        return False


def test_AC_3b_3_coordinator_activation_backfills_without_delivery_and_recovers_due(tmp_path):
    _write_daily(tmp_path / "vault")
    first_store = reviews.ReviewStore(tmp_path / "first.sqlite3")
    first_runner = _IdleRunner()
    first = reviews.ReviewCoordinator(
        vault=tmp_path / "vault",
        store=first_store,
        timezone=TZ,
        weekly_time=datetime.strptime("09:00", "%H:%M").time(),
        monthly_time=datetime.strptime("10:00", "%H:%M").time(),
        runner=first_runner,
    )

    asyncio.run(first.reconcile_once(now=datetime(2026, 8, 4, 12, tzinfo=TZ)))
    assert {job.reason for job in first_store.list_jobs()} == {"backfill"}
    assert first_store.list_deliveries() == []
    assert first_runner.calls == 1

    recovered_store = reviews.ReviewStore(tmp_path / "recovered.sqlite3")
    recovered_store.set_metadata("reviews_activated_at", "2026-07-01T00:00:00+02:00")
    recovered_runner = _IdleRunner()
    recovered = reviews.ReviewCoordinator(
        vault=tmp_path / "vault",
        store=recovered_store,
        timezone=TZ,
        weekly_time=datetime.strptime("09:00", "%H:%M").time(),
        monthly_time=datetime.strptime("10:00", "%H:%M").time(),
        runner=recovered_runner,
    )

    asyncio.run(recovered.reconcile_once(now=datetime(2026, 8, 4, 12, tzinfo=TZ)))
    assert {job.reason for job in recovered_store.list_jobs()} == {"scheduled"}
    assert recovered_runner.calls == 1


def test_AC_3b_4_job_runner_rechecks_source_hash_before_atomic_ready_save(tmp_path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.enqueue_job("week", "2026-07-26", "source-v1", reason="scheduled")
    image_calls: list[str] = []

    async def generate(job):
        return reviews.GeneratedReview(
            record=reviews.ReviewRecord(
                kind="week",
                period="2026-07-26",
                start_date=date(2026, 7, 26),
                end_date=date(2026, 8, 1),
                status="generating",
                title="Title",
                payload={"paragraphs": []},
                telegram_caption="C" * 600,
                reflection_question="Question?",
                safety_note=None,
                image_path=None,
                image_alt=None,
                language="EN",
                model="model",
                source_hash=job.source_hash,
            ),
            sources=[],
        )

    async def image(record):
        image_calls.append(record.period)
        return tmp_path / "poster.jpg"

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=image,
        deliver=lambda record: asyncio.sleep(0),
        current_source_hash=lambda kind, period: "source-v2",
    )

    assert asyncio.run(runner.run_next()) is True
    assert store.get_review("week", "2026-07-26") is None
    assert [(job.source_hash, job.reason, job.status) for job in store.list_jobs()] == [
        ("source-v1", "scheduled", "superseded"),
        ("source-v2", "stale", "pending"),
    ]
    assert image_calls == ["2026-07-26"]


def test_AC_3b_5_review_task_starts_independently_of_toc_and_enrichment_flags():
    started = asyncio.Event()

    class Coordinator:
        async def run_forever(self):
            started.set()
            await asyncio.Event().wait()

    async def scenario():
        settings = SimpleNamespace(
            reviews_enabled=True,
            toc_enabled=False,
            enrichment_enabled=False,
        )
        tasks = reviews.start_review_tasks(settings, Coordinator())
        await asyncio.wait_for(started.wait(), timeout=1)
        assert len(tasks) == 1 and not tasks[0].done()
        await reviews.stop_review_tasks(tasks)

    asyncio.run(scenario())

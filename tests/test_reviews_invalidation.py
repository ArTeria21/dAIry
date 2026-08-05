from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.config import Settings
from dairy_bot.services import reviews

TZ = ZoneInfo("Europe/Vienna")


def _write_daily(vault: Path, body: str = "A substantive reflection.") -> Path:
    path = vault / "2026" / "07" / "2026-07-31.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ndate: 2026-07-31\ntype: daily\n---\n"
        "# 2026-07-31\n\n## 09:00\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _record(
    *,
    kind: str,
    period: str,
    start: date,
    end: date,
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
        payload={"paragraphs": []},
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        safety_note=None,
        image_path=None,
        image_alt=None,
        language="EN",
        model="test/model",
        source_hash=source_hash,
    )


class _IdleRunner:
    async def run_next(self) -> bool:
        return False


@pytest.mark.parametrize("mutation", ["changed", "removed"])
def test_AC_6_1_changed_or_removed_evidence_is_invalidated_without_telegram_resend(
    tmp_path: Path, mutation: str
):
    vault = tmp_path / "vault"
    evidence_path = _write_daily(vault, "The original supporting observation.")
    now = datetime(2026, 8, 4, 12, tzinfo=TZ)
    original_documents = reviews.scan_corpus(vault, first_seen=now)
    period = reviews.ReviewPeriod(
        "week", "2026-07-26", date(2026, 7, 26), date(2026, 8, 1)
    )
    period_hash = reviews.source_hash_for_period(period, original_documents)
    evidence = next(item for item in original_documents if item.source_type == "diary")
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.replace_corpus(original_documents)
    store.upsert_review(
        _record(
            kind="week",
            period=period.period,
            start=period.start_date,
            end=period.end_date,
            title="A grounded week",
            source_hash=period_hash,
        ),
        sources=[
            reviews.ReviewSource(
                source_id=evidence.document_id,
                source_type="diary",
                source_hash=evidence.content_hash,
                label="31 Jul, 09:00",
                position=0,
            )
        ],
    )
    store.record_delivery("week", period.period, chat_id=42, status="sent")

    if mutation == "changed":
        _write_daily(vault, "A materially changed supporting observation.")
    else:
        evidence_path.unlink()

    coordinator = reviews.ReviewCoordinator(
        vault=vault,
        store=store,
        timezone=TZ,
        weekly_time=time(9),
        monthly_time=time(10),
        runner=_IdleRunner(),
    )
    asyncio.run(coordinator.reconcile_once(now=now))

    stored = store.get_review("week", period.period)
    week_jobs = [
        job
        for job in store.list_jobs()
        if (job.kind, job.period) == ("week", period.period)
    ]
    assert stored is not None and stored.status == "stale"
    assert [(job.reason, job.source_hash, job.status) for job in week_jobs] == [
        ("stale", period_hash, "pending")
    ]
    assert store.list_deliveries() == [
        reviews.TelegramDelivery("week", period.period, 42, "sent")
    ]


def test_AC_6_2_monthly_generation_persists_prior_weekly_evidence_without_future_leakage(
    tmp_path: Path,
):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    diary = reviews.CorpusDocument(
        document_id="diary:2026-07-12T09:00",
        source_type="diary",
        path="2026/07/2026-07-12.md",
        heading="09:00",
        text="A July reflection.",
        content_hash="diary-v1",
        document_date=date(2026, 7, 12),
        first_seen=datetime(2026, 7, 12, tzinfo=TZ),
    )
    store.replace_corpus([diary])
    store.upsert_embedding(
        diary.document_id,
        [1.0, 0.0],
        embedding_model="embed-v1",
        content_hash=diary.content_hash,
    )
    prior_id = "review:week:2026-07-05"
    store.upsert_review(
        _record(
            kind="week",
            period="2026-07-05",
            start=date(2026, 7, 5),
            end=date(2026, 7, 11),
            title="Week one",
            source_hash="week-source-v1",
        ),
        sources=[],
    )
    store.upsert_review(
        _record(
            kind="week",
            period="2026-08-02",
            start=date(2026, 8, 2),
            end=date(2026, 8, 8),
            title="A future week",
            source_hash="future-source-v1",
        ),
        sources=[],
    )

    class LLM:
        async def plan(self, **kwargs):
            return reviews.ReviewPlan(
                tool_calls=[
                    reviews.ReviewToolCall(
                        tool="search_diary", query="trajectory across weeks"
                    )
                ]
            )

        async def draft(self, **kwargs):
            evidence_ids = {item.evidence_id for item in kwargs["context"]}
            assert prior_id in evidence_ids
            assert "review:week:2026-08-02" not in evidence_ids
            return reviews.ReviewSynthesis(
                title="A July trajectory",
                paragraphs=[
                    reviews.ReviewParagraph(
                        text=" ".join(f"word{i}" for i in range(500)),
                        evidence_refs=[prior_id],
                    )
                ],
                telegram_caption="C" * 600,
                reflection_question="What changed across these weeks?",
                visual_brief="Layered weekly motifs converging into one monthly field.",
            )

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            raise AssertionError("approved synthesis must not be revised")

    async def embed(text: str):
        return [1.0, 0.0]

    service = reviews.ReviewGenerationService(
        store=store,
        llm=LLM(),
        embed=embed,
        embedding_model="embed-v1",
        parallel_client=None,
        language="EN",
        model="test/model",
    )
    job = store.enqueue_job("month", "2026-07", "month-source-v1", reason="backfill")

    generated = asyncio.run(service.generate(job))
    store.upsert_review(generated.record, sources=generated.sources)

    assert generated.record.payload["paragraphs"][0]["evidence_refs"] == [prior_id]
    assert store.list_review_sources("month", "2026-07") == [
        reviews.ReviewSource(
            source_id=prior_id,
            source_type="review",
            source_hash="week-source-v1",
            label="Week one",
            position=0,
        )
    ]

class _ClosableHTTP:
    async def aclose(self) -> None:
        return None


class _ClosableOpenAI:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace())
        self.embeddings = SimpleNamespace()

    async def close(self) -> None:
        return None


def test_EC_6_2_atomic_source_recheck_reads_raw_vault_instead_of_stale_index(
    tmp_path: Path,
):
    vault = tmp_path / "vault"
    daily_path = _write_daily(vault, "Original reflection.")
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123:test",
        ALLOWED_USER_ID=42,
        OPENROUTER_API_KEY="sk-test",
        JOURNAL_DIR=vault,
        REVIEWS_ENABLED=True,
        REVIEWS_DB_PATH=tmp_path / "reviews.sqlite3",
        REVIEW_ASSETS_DIR=tmp_path / "images",
        REVIEW_IMAGE_MODEL_NAME="test/primary-image",
        REVIEW_IMAGE_FALLBACK_MODEL_NAME="test/fallback-image",
        WEB_PUBLIC_BASE_URL="https://diary.example",
        LANGUAGE="EN",
    )
    runtime = reviews.build_review_runtime(
        settings,
        SimpleNamespace(),
        http_client=_ClosableHTTP(),
        openai_client=_ClosableOpenAI(),
    )
    assert runtime is not None
    indexed_at = datetime(2026, 8, 1, 12, tzinfo=TZ)
    original_documents = reviews.scan_corpus(vault, first_seen=indexed_at)
    runtime.store.replace_corpus(original_documents)
    period = reviews.ReviewPeriod(
        "week", "2026-07-26", date(2026, 7, 26), date(2026, 8, 1)
    )
    original_hash = reviews.source_hash_for_period(period, original_documents)

    daily_path.write_text(
        "---\ndate: 2026-07-31\ntype: daily\n---\n"
        "# 2026-07-31\n\n## 09:00\n\nEdited after generation.\n",
        encoding="utf-8",
    )
    current_documents = reviews.scan_corpus(vault, first_seen=indexed_at)
    expected_hash = reviews.source_hash_for_period(period, current_documents)

    actual_hash = runtime.generation_service.current_source_hash(
        "week", "2026-07-26"
    )

    assert expected_hash != original_hash
    assert actual_hash == expected_hash
    asyncio.run(runtime.close())

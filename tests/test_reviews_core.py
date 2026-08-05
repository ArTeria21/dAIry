from __future__ import annotations

import importlib
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Vienna")


def _reviews_api():
    services = importlib.import_module("dairy_bot.services")
    if not hasattr(services, "reviews"):
        pytest.fail("dairy_bot.services.reviews public API is not implemented")
    return services.reviews


def _write_daily(root: Path, day: str, body: str) -> Path:
    year, month, _ = day.split("-")
    path = root / year / month / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ndate: {day}\ntype: daily\n---\n# {day}\n\n{body}",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("moment", "kind", "expected"),
    [
        (
            datetime(2026, 8, 1, 23, 59, tzinfo=TZ),
            "week",
            ("2026-07-26", date(2026, 7, 26), date(2026, 8, 1)),
        ),
        (
            datetime(2026, 8, 2, 0, 0, tzinfo=TZ),
            "week",
            ("2026-08-02", date(2026, 8, 2), date(2026, 8, 8)),
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=TZ),
            "month",
            ("2026-07", date(2026, 7, 1), date(2026, 7, 31)),
        ),
        (
            datetime(2026, 3, 29, 3, 30, tzinfo=TZ),
            "week",
            ("2026-03-29", date(2026, 3, 29), date(2026, 4, 4)),
        ),
        (
            datetime(2026, 1, 1, 0, 0, tzinfo=TZ),
            "week",
            ("2025-12-28", date(2025, 12, 28), date(2026, 1, 3)),
        ),
    ],
)
def test_AC_1_period_boundaries_are_sunday_based_and_calendar_months(
    moment, kind, expected
):
    reviews = _reviews_api()

    period = reviews.period_for(moment, kind=kind, timezone=TZ)

    assert (period.period, period.start_date, period.end_date) == expected


def test_AC_2_discovery_returns_only_closed_nonempty_periods(tmp_path):
    reviews = _reviews_api()
    _write_daily(tmp_path, "2026-07-31", "## 09:00\n\nA closed-period entry.\n")
    _write_daily(tmp_path, "2026-08-01", "")
    _write_daily(tmp_path, "2026-08-02", "## 10:00\n\nAn active-week entry.\n")

    periods = reviews.discover_closed_periods(
        tmp_path,
        now=datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
        timezone=TZ,
    )

    assert [(period.kind, period.period) for period in periods] == [
        ("month", "2026-07"),
        ("week", "2026-07-26"),
    ]


def test_AC_1_corpus_scan_indexes_only_daily_entries(tmp_path):
    reviews = _reviews_api()
    _write_daily(
        tmp_path,
        "2026-07-31",
        "## 09:00\n\nMorning reflection.\n\n## 18:00\n\nEvening reflection.\n",
    )
    project = tmp_path / "projects" / "idea.md"
    project.parent.mkdir(parents=True)
    project.write_text(
        "---\ndate: 2026-07-01\n---\n# Overview\nFirst idea.\n## Details\nSecond idea.\n",
        encoding="utf-8",
    )
    undated = tmp_path / "notes" / "undated.md"
    undated.parent.mkdir(parents=True)
    undated.write_text("# Observation\nA recent undated thought.\n", encoding="utf-8")
    hidden = tmp_path / ".obsidian" / "private.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("# Hidden\nNever index this.\n", encoding="utf-8")
    (tmp_path / "table_of_contents.md").write_text("# TOC\n", encoding="utf-8")

    documents = reviews.scan_corpus(
        tmp_path,
        first_seen=datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
    )

    assert [document.document_id for document in documents] == [
        "diary:2026-07-31T09:00",
        "diary:2026-07-31T18:00",
    ]
    eligibility = {
        document.document_id: document.eligible_on(date(2026, 7, 31))
        for document in documents
    }
    assert eligibility == {
        "diary:2026-07-31T09:00": True,
        "diary:2026-07-31T18:00": True,
    }


def test_AC_4_review_store_migrates_and_round_trips_ready_review(tmp_path):
    reviews = _reviews_api()
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    record = reviews.ReviewRecord(
        kind="week",
        period="2026-07-26",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 8, 1),
        status="ready",
        title="A week of recalibration",
        payload={"paragraphs": [{"text": "A grounded paragraph."}]},
        telegram_caption="A concise grounded caption.",
        reflection_question="What changed when the pressure eased?",
        safety_note=None,
        image_path=None,
        image_alt=None,
        language="RU",
        model="openai/gpt-5.6-terra",
        source_hash="source-v1",
    )
    sources = [
        reviews.ReviewSource(
            source_id="diary:2026-07-31T09:00",
            source_type="diary",
            source_hash="note-v1",
            label="31 Jul, 09:00",
            position=0,
        ),
        reviews.ReviewSource(
            source_id="vault:projects/idea.md#overview",
            source_type="vault",
            source_hash="idea-v1",
            label="projects/idea.md · Overview",
            position=1,
        ),
    ]

    store.upsert_review(record, sources=sources)

    with sqlite3.connect(store.db_path) as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert schema_version == 3
    assert store.get_review("week", "2026-07-26") == record
    assert store.list_review_sources("week", "2026-07-26") == sources


def test_AC_5_jobs_are_idempotent_and_invalidation_preserves_delivery(tmp_path):
    reviews = _reviews_api()
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    record = reviews.ReviewRecord(
        kind="week",
        period="2026-07-26",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 8, 1),
        status="ready",
        title="Title",
        payload={"paragraphs": []},
        telegram_caption="Caption",
        reflection_question="Question?",
        safety_note=None,
        image_path=None,
        image_alt=None,
        language="RU",
        model="test/model",
        source_hash="source-v1",
    )
    store.upsert_review(
        record,
        sources=[
            reviews.ReviewSource(
                source_id="diary:2026-07-31T09:00",
                source_type="diary",
                source_hash="note-v1",
                label="31 Jul",
                position=0,
            )
        ],
    )
    store.record_delivery("week", "2026-07-26", chat_id=42, status="sent")

    first = store.enqueue_job("week", "2026-07-26", "source-v1", reason="backfill")
    duplicate = store.enqueue_job(
        "week", "2026-07-26", "source-v1", reason="backfill"
    )
    changed = store.enqueue_job("week", "2026-07-26", "source-v2", reason="stale")
    claimed = store.claim_next_job()
    affected = store.invalidate_source("diary:2026-07-31T09:00", "note-v2")

    assert duplicate.job_id == first.job_id
    assert changed.job_id != first.job_id
    assert claimed is not None and claimed.status == "running"
    assert affected == [("week", "2026-07-26")]
    assert store.get_review("week", "2026-07-26").status == "stale"
    assert store.get_delivery("week", "2026-07-26", 42).status == "sent"


def test_EC_1_one_entry_is_eligible_but_frontmatter_only_is_empty(tmp_path):
    reviews = _reviews_api()
    _write_daily(tmp_path, "2026-07-31", "")
    now = datetime(2026, 8, 4, 12, 0, tzinfo=TZ)

    empty = reviews.discover_closed_periods(tmp_path, now=now, timezone=TZ)
    _write_daily(tmp_path, "2026-07-31", "## 09:00\n\nOnly one entry.\n")
    one_entry = reviews.discover_closed_periods(tmp_path, now=now, timezone=TZ)

    assert empty == []
    assert [(period.kind, period.period) for period in one_entry] == [
        ("month", "2026-07"),
        ("week", "2026-07-26"),
    ]


def test_ERR_1_malformed_dates_and_outside_symlinks_leave_corpus_empty(tmp_path):
    reviews = _reviews_api()
    vault = tmp_path / "vault"
    vault.mkdir()
    malformed = vault / "bad.md"
    malformed.write_text(
        "---\ndate: definitely-not-a-date\n---\n# Invalid\nDo not index.\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\ndate: 2026-07-01\n---\n# Outside\nDo not follow.\n",
        encoding="utf-8",
    )
    try:
        (vault / "outside-link.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not supported on this filesystem")
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")

    documents = reviews.scan_corpus(
        vault,
        first_seen=datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
    )
    store.replace_corpus(documents)

    assert documents == []
    assert store.list_corpus_documents() == []

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
        "JOURNAL_DIR": tmp_path / "vault",
        "REVIEWS_ENABLED": True,
        "REVIEWS_DB_PATH": tmp_path / "reviews.sqlite3",
        "REVIEW_ASSETS_DIR": tmp_path / "images",
        "WEB_PUBLIC_BASE_URL": "https://diary.example",
        "LANGUAGE": "EN",
        "REVIEW_IMAGE_MODEL_NAME": "test/primary-image",
        "REVIEW_IMAGE_FALLBACK_MODEL_NAME": "test/fallback-image",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _document() -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id="diary:2026-07-31T09:00",
        source_type="diary",
        path="2026/07/2026-07-31.md",
        heading="09:00",
        text="A grounded reflection.",
        content_hash="entry-v1",
        document_date=date(2026, 7, 31),
        first_seen=datetime(2026, 8, 1, tzinfo=TZ),
    )


def _synthesis() -> reviews.ReviewSynthesis:
    return reviews.ReviewSynthesis(
        title="A grounded week",
        paragraphs=[
            reviews.ReviewParagraph(
                text=" ".join(f"word{i}" for i in range(300)),
                evidence_refs=["diary:2026-07-31T09:00"],
            )
        ],
        telegram_caption="C" * 600,
        reflection_question="What remains open?",
        visual_brief="One central compass.",
    )


def test_AC_3c_1_generation_service_builds_durable_grounded_review(tmp_path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    document = _document()
    store.replace_corpus([document])
    store.upsert_embedding(
        document.document_id,
        [1.0, 0.0],
        embedding_model="embed-v1",
        content_hash=document.content_hash,
    )
    events: list[str] = []

    class LLM:
        async def plan(self, **kwargs):
            events.append("plan")
            return reviews.ReviewPlan(tool_calls=[])

        async def draft(self, **kwargs):
            events.append("draft")
            assert kwargs["stats"] == {"entry_count": 1, "active_days": 1}
            return _synthesis()

        async def critique(self, **kwargs):
            events.append("critique")
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
    job = store.enqueue_job("week", "2026-07-26", "source-v1", reason="backfill")

    generated = asyncio.run(service.generate(job))

    assert events == ["plan", "draft", "critique"]
    assert generated.record.status == "generating"
    assert generated.record.payload["paragraphs"] == [
        {
            "text": _synthesis().paragraphs[0].text,
            "evidence_refs": ["diary:2026-07-31T09:00"],
        }
    ]
    assert generated.record.payload["visual_brief"] == "One central compass."
    assert generated.record.image_alt == "Archival abstract poster for the weekly review."
    assert generated.record.source_hash == "source-v1"
    assert [(source.source_id, source.source_type) for source in generated.sources] == [
        ("diary:2026-07-31T09:00", "diary")
    ]
    serialized = str(generated.record.payload)
    assert "parallel" not in serialized and "prompt" not in serialized


class _ClosableHTTP:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class _ClosableOpenAI:
    def __init__(self):
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace())
        self.embeddings = SimpleNamespace()

    async def close(self):
        self.closed = True


def test_AC_3c_2_runtime_wires_private_stores_clients_and_closes_owners(tmp_path):
    settings = _settings(
        tmp_path,
        PARALLEL_API_KEY="parallel-secret",
        REVIEW_MODEL_NAME="test/provider-review-model",
    )
    settings.journal_dir.mkdir()
    http = _ClosableHTTP()
    openai = _ClosableOpenAI()
    bot = SimpleNamespace()

    runtime = reviews.build_review_runtime(
        settings,
        bot,
        http_client=http,
        openai_client=openai,
    )

    assert runtime is not None
    assert runtime.store.db_path == tmp_path / "reviews.sqlite3"
    assert runtime.coordinator.vault == tmp_path / "vault"
    assert runtime.image_generator.output_dir == tmp_path / "images"
    assert runtime.telegram_sender.public_base_url == "https://diary.example"
    assert runtime.generation_service.parallel_client is not None
    assert (
        runtime.generation_service.parallel_client.client_model
        == "test/provider-review-model"
    )
    asyncio.run(runtime.close())
    assert http.closed is True and openai.closed is True

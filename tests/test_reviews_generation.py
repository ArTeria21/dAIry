from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.services import reviews

TZ = ZoneInfo("Europe/Vienna")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _document(
    document_id: str,
    *,
    day: date,
    path: str,
    text: str = "Grounded diary material.",
) -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id=document_id,
        source_type="diary" if document_id.startswith("diary:") else "vault",
        path=path,
        heading=None,
        text=text,
        content_hash=f"hash:{document_id}",
        document_date=day,
        first_seen=datetime(2026, 8, 4, 12, tzinfo=TZ),
    )


def _embedded(
    document: reviews.CorpusDocument,
    vector: tuple[float, ...],
    *,
    model: str = "embed-v1",
    content_hash: str | None = None,
) -> reviews.EmbeddedDocument:
    return reviews.EmbeddedDocument(
        document=document,
        embedding=vector,
        embedding_model=model,
        embedding_dimension=len(vector),
        content_hash=content_hash or document.content_hash,
    )


def test_AC_6_diary_retrieval_has_temporal_embedding_and_date_diversity_guardrails():
    first = _document(
        "diary:2026-07-31T09:00", day=date(2026, 7, 31), path="2026/07/2026-07-31.md"
    )
    same_day = _document(
        "diary:2026-07-31T18:00", day=date(2026, 7, 31), path="2026/07/2026-07-31.md"
    )
    other_day = _document(
        "diary:2026-07-30T08:00",
        day=date(2026, 7, 30),
        path="2026/07/2026-07-30.md",
    )
    future = _document(
        "diary:2026-08-02T10:00", day=date(2026, 8, 2), path="2026/08/2026-08-02.md"
    )
    stale = _document(
        "diary:2026-07-29T08:00",
        day=date(2026, 7, 29),
        path="2026/07/2026-07-29.md",
    )

    hits = reviews.search_corpus(
        (1.0, 0.0),
        [
            _embedded(first, (1.0, 0.0)),
            _embedded(same_day, (0.99, 0.01)),
            _embedded(other_day, (0.9, 0.1)),
            _embedded(future, (1.0, 0.0)),
            _embedded(stale, (1.0, 0.0), content_hash="old-hash"),
            _embedded(other_day, (1.0,), model="wrong-model"),
        ],
        cutoff=date(2026, 8, 1),
        embedding_model="embed-v1",
        limit=3,
    )

    assert [hit.document.document_id for hit in hits] == [
        "diary:2026-07-31T09:00",
        "diary:2026-07-30T08:00",
        "diary:2026-07-31T18:00",
    ]
    assert all(-1.0 <= hit.score <= 1.0 for hit in hits)


class _HTTPResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "results": [
                {
                    "title": f"Result {index}",
                    "url": f"https://example.test/{index}",
                    "excerpts": [f"Excerpt {index}"],
                }
                for index in range(8)
            ]
        }


class _RecordingHTTPClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs) -> _HTTPResponse:
        self.calls.append({"url": url, **kwargs})
        return _HTTPResponse()


@pytest.mark.anyio
async def test_ERR_1_parallel_search_enforces_six_call_budget_before_http():
    http = _RecordingHTTPClient()
    client = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=http,
        client_model="openai/gpt-5.6-terra",
    )
    run = client.begin_run()

    for _ in range(6):
        await run.search(
            objective="Research a grounded mechanism",
            search_queries=["agency under uncertainty"],
        )
    with pytest.raises(reviews.SearchBudgetExceeded):
        await run.search(objective="seventh", search_queries=["seventh"])

    assert len(http.calls) == 6
    assert all(call["url"] == "https://api.parallel.ai/v1/search" for call in http.calls)
    assert http.calls[0]["headers"]["x-api-key"] == "parallel-secret"


@pytest.mark.anyio
async def test_AC_2_3_planner_tools_propagate_cutoff_and_share_parallel_budget():
    diary_calls: list[tuple[str, date]] = []

    async def diary_search(query: str, cutoff: date):
        diary_calls.append((query, cutoff))
        return ["diary-hit"]

    http = _RecordingHTTPClient()
    run = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=http,
        client_model="openai/gpt-5.6-terra",
        max_calls=1,
    ).begin_run()
    tools = reviews.ReviewPlannerTools(
        cutoff=date(2026, 8, 1), diary_search=diary_search, parallel_run=run
    )

    assert await tools.search_diary("pressure") == ["diary-hit"]
    await tools.parallel_search(objective="research", search_queries=["pressure"])
    assert await tools.parallel_search(
        objective="again", search_queries=["pressure"]
    ) == []

    assert diary_calls == [("pressure", date(2026, 8, 1))]
    assert len(http.calls) == 1

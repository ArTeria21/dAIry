from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx

from dairy_bot.services import reviews


class _ParallelResponse:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"results": self.results}


class _ParallelHTTP:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _ParallelResponse:
        self.calls.append({"url": url, **kwargs})
        return _ParallelResponse(self.results)


def _results(count: int) -> list[dict[str, object]]:
    return [
        {
            "title": f"Result {index}",
            "url": f"https://example.test/{index}",
            "excerpts": [f"Excerpt {index}"],
        }
        for index in range(count)
    ]


def test_AC_1_parallel_returns_all_eight_valid_results_in_source_order():
    http = _ParallelHTTP(_results(8))
    run = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=http,
        client_model="openai/gpt-5.6-terra",
    ).begin_run()

    results = asyncio.run(
        run.search(
            objective="Research a grounded mechanism",
            search_queries=["agency under uncertainty"],
        )
    )

    assert [source.title for source in results] == [
        "Result 0",
        "Result 1",
        "Result 2",
        "Result 3",
        "Result 4",
        "Result 5",
        "Result 6",
        "Result 7",
    ]


def test_AC_2_parallel_client_sends_model_and_one_session_id_per_run_without_result_cap():
    http = _ParallelHTTP([])
    client = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=http,
        client_model="openai/gpt-5.6-terra",
    )

    assert "max_results" not in inspect.signature(
        reviews.ParallelSearchClient
    ).parameters
    assert list(inspect.signature(client.begin_run).parameters) == []
    first_run = client.begin_run()
    objective = "Understand a conflict involving Anna Petrova"
    queries = ["Anna Petrova conflict pattern", "team boundary uncertainty"]

    asyncio.run(first_run.search(objective=objective, search_queries=queries))
    asyncio.run(
        first_run.search(
            objective="Find complementary evidence",
            search_queries=["workplace conflict uncertainty"],
        )
    )
    second_run = client.begin_run()
    asyncio.run(
        second_run.search(
            objective="Research a different review",
            search_queries=["perfectionism experiential avoidance"],
        )
    )

    assert len(http.calls) == 3
    assert all(
        call["url"] == "https://api.parallel.ai/v1/search" for call in http.calls
    )
    payloads = [call["json"] for call in http.calls]
    assert payloads[0]["objective"] == objective
    assert payloads[0]["search_queries"] == queries
    assert all(
        payload["client_model"] == "openai/gpt-5.6-terra" for payload in payloads
    )
    assert isinstance(payloads[0]["session_id"], str)
    assert payloads[0]["session_id"]
    assert payloads[0]["session_id"] == payloads[1]["session_id"]
    assert payloads[2]["session_id"] != payloads[0]["session_id"]
    assert all("max_results" not in payload for payload in payloads)


class _CapturingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps({"tool_calls": []}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_AC_3_planner_prompt_teaches_vault_first_parallel_research():
    completions = _CapturingCompletions()
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="test/model",
        language="EN",
    )

    asyncio.run(
        llm.plan(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[],
            stats={"entry_count": 1},
        )
    )

    raw_prompt = completions.calls[0]["messages"][0]["content"]
    system_prompt = " ".join(raw_prompt.lower().split())
    for required_phrase in (
        "always call `search_diary` at least once",
        "earlier diary entries",
        "previous reviews",
        "primary context tool",
        "`objective` and every `search_queries` item",
        "written in english",
        "self-contained natural-language research objective",
        "2-3 complementary",
        "3-6 words",
        "systematic reviews",
        "meta-analyses",
        "peer-reviewed primary research",
        "official primary sources",
        "seo articles",
        "`site:` operators",
        "identifying details",
        "neutral, non-identifying constructs",
    ):
        assert required_phrase in system_prompt
    for example_query in (
        "perfectionistic concerns procrastination meta-analysis",
        "intolerance of uncertainty avoidance systematic review",
        "psychological detachment work recovery systematic review",
        "chosen solitude wellbeing longitudinal study",
    ):
        assert example_query in raw_prompt
    assert not any("\u0400" <= character <= "\u04ff" for character in raw_prompt)


def test_EC_1_parallel_empty_results_returns_empty_list():
    http = _ParallelHTTP([])
    run = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=http,
        client_model="openai/gpt-5.6-terra",
    ).begin_run()

    results = asyncio.run(
        run.search(objective="Research context", search_queries=["relevant context"])
    )

    assert results == []


class _ParallelPaymentRequiredResponse:
    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://api.parallel.ai/v1/search")
        response = httpx.Response(402, request=request)
        raise httpx.HTTPStatusError(
            "Payment Required",
            request=request,
            response=response,
        )

    def json(self) -> dict[str, object]:
        raise AssertionError("A failed response must not be parsed")


class _ParallelPaymentRequiredHTTP:
    async def post(self, url: str, **kwargs: Any) -> _ParallelPaymentRequiredResponse:
        return _ParallelPaymentRequiredResponse()


def test_EC_2_parallel_http_failure_degrades_to_empty_results(caplog):
    run = reviews.ParallelSearchClient(
        api_key="parallel-secret",
        http_client=_ParallelPaymentRequiredHTTP(),
        client_model="openai/gpt-5.6-terra",
    ).begin_run()

    results = asyncio.run(
        run.search(objective="Research context", search_queries=["relevant context"])
    )

    assert results == []
    assert "HTTP 402" in caplog.text

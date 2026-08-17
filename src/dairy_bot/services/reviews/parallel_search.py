from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable, Protocol, Sequence
from uuid import uuid4

import httpx

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"
logger = logging.getLogger(__name__)


class SearchBudgetExceeded(RuntimeError):
    """Raised before an agent can exceed its per-review web-search budget."""


class HTTPResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]: ...


class HTTPClient(Protocol):
    async def post(self, url: str, **kwargs: Any) -> HTTPResponse: ...


@dataclass(frozen=True, slots=True)
class ParallelSource:
    title: str
    url: str
    excerpts: tuple[str, ...]
    publish_date: str | None = None


class ParallelSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        http_client: HTTPClient,
        client_model: str | None = None,
        max_calls: int = 6,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.http_client = http_client
        self.client_model = client_model
        self.max_calls = max(0, max_calls)
        self.timeout_seconds = timeout_seconds

    def begin_run(self) -> ParallelSearchRun:
        return ParallelSearchRun(self, session_id=str(uuid4()))


class ParallelSearchRun:
    def __init__(self, client: ParallelSearchClient, *, session_id: str) -> None:
        self._client = client
        self._session_id = session_id
        self._calls = 0

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def max_calls(self) -> int:
        return self._client.max_calls

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self._calls)

    async def search(
        self,
        *,
        objective: str,
        search_queries: Sequence[str],
    ) -> list[ParallelSource]:
        if self._calls >= self._client.max_calls:
            raise SearchBudgetExceeded(
                f"Parallel search budget exhausted ({self._client.max_calls} calls)"
            )
        self._calls += 1
        payload = {
            "objective": objective,
            "search_queries": list(search_queries),
            "session_id": self._session_id,
        }
        if self._client.client_model is not None:
            payload["client_model"] = self._client.client_model
        try:
            response = await self._client.http_client.post(
                PARALLEL_SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self._client.api_key,
                },
                json=payload,
                timeout=self._client.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            logger.warning(
                "Parallel search failed with HTTP %s; "
                "continuing without external sources",
                error.response.status_code,
            )
            return []
        except httpx.RequestError as error:
            logger.warning(
                "Parallel search request failed (%s); "
                "continuing without external sources",
                type(error).__name__,
            )
            return []
        raw_results = response.json().get("results", [])
        sources: list[ParallelSource] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            raw_excerpts = item.get("excerpts", [])
            excerpts = tuple(
                excerpt for excerpt in raw_excerpts if isinstance(excerpt, str)
            )
            publish_date = item.get("publish_date")
            sources.append(
                ParallelSource(
                    title=title,
                    url=url,
                    excerpts=excerpts,
                    publish_date=publish_date
                    if isinstance(publish_date, str)
                    else None,
                )
            )
        return sources


DiarySearch = Callable[[str, date], Awaitable[Any]]


class ReviewPlannerTools:
    """Bound tool facade passed to the review planner."""

    def __init__(
        self,
        *,
        cutoff: date,
        diary_search: DiarySearch,
        parallel_run: ParallelSearchRun,
    ) -> None:
        self.cutoff = cutoff
        self._diary_search = diary_search
        self._parallel_run = parallel_run

    async def search_diary(self, query: str) -> Any:
        return await self._diary_search(query, self.cutoff)

    @property
    def parallel_budget(self) -> int:
        remaining = getattr(self._parallel_run, "remaining_calls", None)
        return 6 if remaining is None else max(0, int(remaining))

    async def parallel_search(
        self, *, objective: str, search_queries: Sequence[str]
    ) -> list[ParallelSource]:
        if self.parallel_budget <= 0:
            return []
        return await self._parallel_run.search(
            objective=objective,
            search_queries=search_queries,
        )

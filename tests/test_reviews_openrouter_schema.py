from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import Any

from dairy_bot.services import reviews


class _CapturingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps({"tool_calls": []}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    reference = node.get("$ref")
    if reference is None:
        return node
    resolved: Any = schema
    for part in reference.removeprefix("#/").split("/"):
        resolved = resolved[part]
    assert isinstance(resolved, dict)
    return resolved


def test_AC_6_4_openrouter_plan_schema_discriminates_tool_argument_shapes():
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

    schema = completions.calls[0]["response_format"]["json_schema"]["schema"]
    item_schema = schema["properties"]["tool_calls"]["items"]
    raw_branches = item_schema.get("oneOf") or item_schema.get("anyOf")
    assert raw_branches is not None, (
        "tool_calls items must be a union of mutually exclusive tool schemas"
    )
    branches = [_resolve(schema, branch) for branch in raw_branches]
    by_tool: dict[str, dict[str, Any]] = {}
    for branch in branches:
        tool_schema = branch["properties"]["tool"]
        discriminator = tool_schema.get("const")
        if discriminator is None and len(tool_schema.get("enum", [])) == 1:
            discriminator = tool_schema["enum"][0]
        by_tool[str(discriminator)] = branch

    assert set(by_tool) == {"search_diary", "parallel_search"}
    assert by_tool["search_diary"]["additionalProperties"] is False
    assert set(by_tool["search_diary"]["properties"]) == {"tool", "query"}
    assert set(by_tool["search_diary"]["required"]) == {"tool", "query"}
    assert by_tool["parallel_search"]["additionalProperties"] is False
    assert set(by_tool["parallel_search"]["properties"]) == {
        "tool",
        "objective",
        "search_queries",
    }
    assert set(by_tool["parallel_search"]["required"]) == {
        "tool",
        "objective",
        "search_queries",
    }

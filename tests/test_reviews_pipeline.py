from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from dairy_bot.services import reviews


def _document(document_id: str, day: date) -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id=document_id,
        source_type="diary",
        path=f"2026/07/{day.isoformat()}.md",
        heading="09:00",
        text=f"Evidence from {day.isoformat()}",
        content_hash=f"hash:{document_id}",
        document_date=day,
        first_seen=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def _paragraph(word_count: int, evidence: str) -> reviews.ReviewParagraph:
    text = " ".join(f"наблюдение{i}" for i in range(word_count))
    return reviews.ReviewParagraph(text=text, evidence_refs=[evidence])


def _synthesis(word_count: int = 300, evidence: str = "diary:past"):
    return reviews.ReviewSynthesis(
        title="Линия недели",
        paragraphs=[_paragraph(word_count, evidence)],
        telegram_caption="с" * 600,
        reflection_question="Что изменится, если оставить этот вопрос открытым?",
        visual_brief="One central compass-like symbol.",
    )


def test_AC_2b_1_review_plan_enforces_six_combined_calls_and_five_queries():
    calls = [reviews.ReviewToolCall(tool="search_diary", query=f"theme {i}") for i in range(5)]
    calls.append(
        reviews.ReviewToolCall(
            tool="parallel_search",
            objective="Research one relevant mechanism",
            search_queries=[f"query {i}" for i in range(5)],
        )
    )

    plan = reviews.ReviewPlan(tool_calls=calls)

    assert len(plan.tool_calls) == 6
    with pytest.raises(ValidationError, match="6"):
        reviews.ReviewPlan(
            tool_calls=calls
            + [reviews.ReviewToolCall(tool="search_diary", query="overflow")]
        )
    with pytest.raises(ValidationError, match="5"):
        reviews.ReviewToolCall(
            tool="parallel_search",
            objective="Too broad",
            search_queries=[f"query {i}" for i in range(6)],
        )


def test_AC_2b_2_pipeline_snapshots_executes_tools_and_passes_grounded_context():
    events: list[str] = []
    past = _document("diary:past", date(2026, 7, 30))
    future = _document("diary:future", date(2026, 8, 2))

    async def diary_search(query: str, cutoff: date):
        events.append(f"diary:{query}:{cutoff.isoformat()}")
        return [reviews.ReviewContextItem.from_document(past)]

    class ParallelRun:
        async def search(self, *, objective, search_queries):
            events.append(f"parallel:{objective}:{','.join(search_queries)}")
            return [
                reviews.ParallelSource(
                    title="Research note",
                    url="https://example.test/research",
                    excerpts=("A relevant mechanism.",),
                )
            ]

    tools = reviews.ReviewPlannerTools(
        cutoff=date(2026, 8, 1),
        diary_search=diary_search,
        parallel_run=ParallelRun(),
    )

    class FakeLLM:
        async def plan(self, *, kind, review_end, documents, stats):
            events.append("plan")
            assert kind == "week"
            assert review_end == date(2026, 8, 1)
            assert [item.document_id for item in documents] == ["diary:past"]
            assert stats == {"entry_count": 1, "active_days": 1}
            return reviews.ReviewPlan(
                tool_calls=[
                    reviews.ReviewToolCall(tool="search_diary", query="agency"),
                    reviews.ReviewToolCall(
                        tool="parallel_search",
                        objective="Learned helplessness research",
                        search_queries=["agency under uncertainty"],
                    ),
                ]
            )

        async def draft(self, *, kind, review_end, documents, stats, context):
            events.append("draft")
            assert [item.document_id for item in documents] == ["diary:past"]
            assert stats == {"entry_count": 1, "active_days": 1}
            assert [(item.source_type, item.internal_only) for item in context] == [
                ("diary", False),
                ("external", True),
            ]
            return _synthesis(word_count=299)

        async def critique(self, *, kind, synthesis, context):
            events.append("critique")
            return reviews.ReviewCritique(approved=False, issues=["too short"])

        async def revise(self, *, kind, synthesis, critique, context):
            events.append("revise")
            assert critique.issues == ["too short"]
            return _synthesis()

    result = asyncio.run(
        reviews.ReviewGenerationPipeline(llm=FakeLLM(), tools=tools).generate(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[future, past],
            deterministic_stats={"entry_count": 1, "active_days": 1},
        )
    )

    assert events == [
        "plan",
        "diary:agency:2026-08-01",
        "parallel:Learned helplessness research:agency under uncertainty",
        "draft",
        "critique",
        "revise",
    ]
    assert result.synthesis == _synthesis()
    assert [item.source_type for item in result.used_evidence] == ["diary", "external"]
    assert result.synthesis.paragraphs[0].evidence_refs == ["diary:past"]


def test_EC_2b_future_sources_are_filtered_but_external_context_is_left_to_the_llm():
    past = _document("diary:past", date(2026, 7, 30))
    future = _document("diary:future", date(2026, 8, 2))

    async def diary_search(query: str, cutoff: date):
        return [reviews.ReviewContextItem.from_document(future)]

    class ParallelRun:
        async def search(self, *, objective, search_queries):
            return [
                reviews.ParallelSource(
                    title="External",
                    url="https://example.test/external",
                    excerpts=("Excerpt",),
                )
            ]

    class LeakingLLM:
        async def plan(self, **kwargs):
            return reviews.ReviewPlan(
                tool_calls=[
                    reviews.ReviewToolCall(
                        tool="parallel_search",
                        objective="Context",
                        search_queries=["context"],
                    )
                ]
            )

        async def draft(self, **kwargs):
            external_id = next(
                item.evidence_id
                for item in kwargs["context"]
                if item.source_type == "external"
            )
            return _synthesis(evidence=external_id)

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            raise AssertionError("approved draft must not be revised")

    tools = reviews.ReviewPlannerTools(
        cutoff=date(2026, 8, 1),
        diary_search=diary_search,
        parallel_run=ParallelRun(),
    )
    result = asyncio.run(
        reviews.ReviewGenerationPipeline(llm=LeakingLLM(), tools=tools).generate(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[past, future],
            deterministic_stats={"entry_count": 1},
        )
    )

    assert result.synthesis.paragraphs[0].evidence_refs[0].startswith("parallel:")
    assert all(item.evidence_id != "diary:future" for item in result.used_evidence)


class _FakeCompletions:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_AC_2b_3_openrouter_adapter_uses_model_strict_schemas_and_prompt_language(
    tmp_path: Path,
):
    plan_payload = {"tool_calls": []}
    synthesis_payload = _synthesis().model_dump()
    critique_payload = {"approved": False, "issues": ["tighten grounding"]}
    completions = _FakeCompletions(
        [plan_payload, synthesis_payload, critique_payload, synthesis_payload, plan_payload]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    ru = reviews.OpenRouterReviewLLM(
        client=client,
        model="openai/gpt-5.6-terra",
        language="RU",
    )
    en = reviews.OpenRouterReviewLLM(
        client=client,
        model="openai/gpt-5.6-terra",
        language="EN",
    )
    kwargs = {
        "kind": "week",
        "review_end": date(2026, 8, 1),
        "documents": [_document("diary:past", date(2026, 7, 30))],
        "stats": {"entry_count": 1},
    }

    plan = asyncio.run(ru.plan(**kwargs))
    draft = asyncio.run(ru.draft(**kwargs, context=[]))
    critique = asyncio.run(ru.critique(kind="week", synthesis=draft, context=[]))
    revised = asyncio.run(
        ru.revise(kind="week", synthesis=draft, critique=critique, context=[])
    )
    asyncio.run(en.plan(**kwargs))

    assert plan == reviews.ReviewPlan(tool_calls=[])
    assert draft == _synthesis()
    assert critique == reviews.ReviewCritique(
        approved=False, issues=["tighten grounding"]
    )
    assert revised == _synthesis()
    assert len(completions.calls) == 5
    assert {call["model"] for call in completions.calls} == {
        "openai/gpt-5.6-terra"
    }
    assert all(call["reasoning_effort"] == "high" for call in completions.calls)
    assert all(call["max_completion_tokens"] == 16_000 for call in completions.calls)
    assert all("temperature" not in call for call in completions.calls)
    assert all(
        call["extra_body"] == {"provider": {"require_parameters": True}}
        for call in completions.calls
    )
    assert all(call["response_format"]["type"] == "json_schema" for call in completions.calls)
    assert all(
        call["response_format"]["json_schema"]["strict"] is True
        for call in completions.calls
    )
    system_prompts = [call["messages"][0]["content"] for call in completions.calls]
    assert all(
        not any("\u0400" <= character <= "\u04ff" for character in prompt)
        for prompt in system_prompts
    )
    planner_prompt, draft_prompt, critic_prompt, revision_prompt, en_planner = (
        system_prompts
    )
    assert "Both `objective` and every `search_queries` item" in planner_prompt
    assert "must be written in English" in planner_prompt
    assert "reader-facing fields in Russian" in draft_prompt
    assert "`visual_brief` in English" in draft_prompt
    assert "every `issues` item in English" in critic_prompt
    assert "reader-facing fields in Russian" in revision_prompt
    assert "`visual_brief` in English" in revision_prompt
    assert "must be written in English" in en_planner
    assert list(tmp_path.iterdir()) == []

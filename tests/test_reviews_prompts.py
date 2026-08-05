from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import Any

from dairy_bot.services import reviews


class _RecordingCompletions:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _synthesis() -> reviews.ReviewSynthesis:
    return reviews.ReviewSynthesis(
        title="Неделя между контролем и действием",
        paragraphs=[
            reviews.ReviewParagraph(
                text="Подготовка временами становилась способом отложить действие.",
                evidence_refs=["diary:2026-07-31T09:00"],
            )
        ],
        telegram_caption="На этой неделе подготовка временами заменяла действие.",
        reflection_question="Какой шаг сейчас важнее ещё одного объяснения?",
        safety_note=None,
        visual_brief="A precise bridge blueprint interrupted by one rough footpath.",
    )


def _normalise(prompt: str) -> str:
    return " ".join(prompt.lower().replace("`", "").split())


def test_AC_PROMPT_1_all_review_system_prompts_are_english_with_phase_specific_output_languages():
    synthesis = _synthesis()
    completions = _RecordingCompletions(
        [
            {"tool_calls": []},
            synthesis.model_dump(mode="json"),
            {"approved": False, "issues": ["Ground the second hypothesis."]},
            synthesis.model_dump(mode="json"),
        ]
    )
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )
    common = {
        "kind": "week",
        "review_end": date(2026, 8, 1),
        "documents": [],
        "stats": {"entry_count": 1},
    }

    asyncio.run(llm.plan(**common))
    draft = asyncio.run(llm.draft(**common, context=[]))
    critique = asyncio.run(llm.critique(kind="week", synthesis=draft, context=[]))
    asyncio.run(
        llm.revise(
            kind="week",
            synthesis=draft,
            critique=critique,
            context=[],
        )
    )

    prompts = [call["messages"][0]["content"] for call in completions.calls]
    assert len(prompts) == 4
    assert all(
        not any("\u0400" <= character <= "\u04ff" for character in prompt)
        for prompt in prompts
    )

    planner, drafter, critic, reviser = map(_normalise, prompts)
    assert "objective and every search_queries item must be written in english" in planner
    assert "every issues item in english" in critic
    for synthesis_prompt in (drafter, reviser):
        assert "reader-facing fields in russian" in synthesis_prompt
        for field in (
            "title",
            "paragraphs",
            "telegram_caption",
            "reflection_question",
            "safety_note",
        ):
            assert field in synthesis_prompt
        assert "weekly_trajectory" not in synthesis_prompt
        assert "visual_brief in english" in synthesis_prompt


def test_monthly_trajectory_is_required_inside_the_essay_without_a_separate_field():
    synthesis = _synthesis()
    completions = _RecordingCompletions([synthesis.model_dump(mode="json")])
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="EN",
    )

    asyncio.run(
        llm.draft(
            kind="month",
            review_end=date(2026, 8, 31),
            documents=[],
            stats={"entry_count": 1},
            context=[],
        )
    )

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    assert "movement and meaningful differences across the month's weeks" in prompt
    assert "inside the cohesive essay" in prompt
    assert "weekly_trajectory" not in prompt


def test_AC_6_planner_makes_diary_search_mandatory_and_explains_its_value():
    completions = _RecordingCompletions([{"tool_calls": []}])
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )

    asyncio.run(
        llm.plan(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[],
            stats={"entry_count": 1},
        )
    )

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    assert "always call search_diary at least once" in prompt
    assert "cross-note" in prompt
    assert "cross-period" in prompt
    assert "earlier diary entries" in prompt
    assert "dated vault notes" not in prompt
    assert "prior reviews" in prompt


def test_AC_PROMPT_3_planner_teaches_parallel_query_construction_with_few_shots():
    completions = _RecordingCompletions([{"tool_calls": []}])
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )

    asyncio.run(
        llm.plan(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[],
            stats={"entry_count": 1},
        )
    )

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    assert "objective and every search_queries item must be written in english" in prompt
    assert "precise, self-contained natural-language research objective" in prompt
    assert "2-3 complementary and distinct" in prompt
    assert "3-6 words" in prompt
    assert "full sentences" in prompt
    assert "site:" in prompt
    assert "boolean operators" in prompt
    assert "perfectionistic concerns procrastination meta-analysis" in prompt
    assert "intolerance of uncertainty avoidance systematic review" in prompt


def test_AC_PROMPT_4_planner_prioritises_reliable_primary_research_sources():
    completions = _RecordingCompletions([{"tool_calls": []}])
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )

    asyncio.run(
        llm.plan(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[],
            stats={"entry_count": 1},
        )
    )

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    for required_guidance in (
        "systematic reviews",
        "meta-analyses",
        "peer-reviewed primary research",
        "official primary sources",
        "seo",
        "popular psychology",
    ):
        assert required_guidance in prompt


def test_AC_PROMPT_5_compact_length_is_guidance_not_a_validation_rule():
    synthesis = _synthesis()
    completions = _RecordingCompletions(
        [
            synthesis.model_dump(mode="json"),
            {"approved": False, "issues": ["Fix one evidence reference."]},
            synthesis.model_dump(mode="json"),
            synthesis.model_dump(mode="json"),
        ]
    )
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )
    common = {
        "kind": "week",
        "review_end": date(2026, 8, 1),
        "documents": [],
        "stats": {"entry_count": 1},
        "context": [],
    }

    draft = asyncio.run(llm.draft(**common))
    critique = asyncio.run(llm.critique(kind="week", synthesis=draft, context=[]))
    asyncio.run(
        llm.revise(
            kind="week",
            synthesis=draft,
            critique=critique,
            context=[],
        )
    )
    asyncio.run(
        llm.draft(
            kind="month",
            review_end=date(2026, 8, 31),
            documents=[],
            stats={"entry_count": 1},
            context=[],
        )
    )

    drafter, critic, reviser, monthly_drafter = map(
        _normalise,
        [call["messages"][0]["content"] for call in completions.calls],
    )
    for prompt in (drafter, monthly_drafter):
        assert "aim for roughly 250-300 words" in prompt
        assert "guidance, not a validation rule" in prompt
    assert "do not count or flag essay length" in critic
    assert "250-300 words" not in critic
    assert "250-300 words" not in reviser

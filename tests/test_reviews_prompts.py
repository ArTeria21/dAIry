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


def test_AC_PROMPT_1_review_prompts_keep_instructions_english_with_russian_few_shots():
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
    planner_raw, drafter_raw, critic_raw, reviser_raw = prompts
    assert any("\u0400" <= character <= "\u04ff" for character in drafter_raw)
    assert all(
        not any("\u0400" <= character <= "\u04ff" for character in prompt)
        for prompt in (planner_raw, critic_raw, reviser_raw)
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
    assert "explain or challenge the central claim inside the cohesive essay" in prompt
    assert "do not recap the weeks" in prompt
    assert "weekly_trajectory" not in prompt


def test_synthesis_and_revision_prioritise_interpretation_over_recap_in_plain_language():
    synthesis = _synthesis()
    completions = _RecordingCompletions(
        [
            synthesis.model_dump(mode="json"),
            {"approved": False, "issues": ["Compress the recap."]},
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

    drafter, _, reviser = map(
        _normalise,
        [call["messages"][0]["content"] for call in completions.calls],
    )
    for prompt in (drafter, reviser):
        assert "open with a direct central claim that interprets the period" in prompt
        assert "two or three distinct diary situations in total" in prompt
        assert "a situation still counts if it appears in only one clause" in prompt
        assert "total includes counterexamples and earlier-period evidence" in prompt
        assert "give only the facts needed to test the claim" in prompt
        assert (
            "explanation of why those situations support or challenge the claim"
            in prompt
        )
        assert "take more space than their recap" in prompt
        assert "do not try to cover every topic or event" in prompt
        assert "tension, trade-off, feedback loop, shift in meaning" in prompt
        assert "gap between intention and action" in prompt
        assert "plausible alternative reading or counterexample" in prompt
        assert "short or medium-length sentences, common words" in prompt
        assert "start with the claim, not an ornate opening" in prompt
        assert "stacked abstractions" in prompt
        assert "recurring stock labels" in prompt
        assert "at most one plain metaphor or comparison" in prompt
        assert "explain why x resembles y" in prompt
        assert "decorative metaphor" in prompt
        assert (
            "hidden motives, diagnoses, or unsupported psychological causes" in prompt
        )
        assert "describe concrete observations before naming" not in prompt
    assert "lead with the most explanatory grounded interpretation" in drafter
    assert "topic inventory, or chronology" in drafter
    assert "at most three distinct diary situations in the essay" in reviser
    assert "no question marks in paragraph text" in reviser
    assert "no more than two tightly linked questions" in reviser


def test_reflection_question_contract_is_specific_comparative_and_actionable():
    synthesis = _synthesis()
    completions = _RecordingCompletions([synthesis.model_dump(mode="json")])
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )

    asyncio.run(
        llm.draft(
            kind="week",
            review_end=date(2026, 8, 1),
            documents=[],
            stats={"entry_count": 1},
            context=[],
        )
    )

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    assert "one compact reflection prompt" in prompt
    assert "no preamble and no yes/no framing" in prompt
    assert "allow two tightly connected questions" in prompt
    assert "concrete criterion, threshold, or result" in prompt
    assert "do not chain unrelated questions" in prompt
    assert "embed a quoted self-question" in prompt
    assert "specific to this review's central tension or pattern" in prompt
    assert "compare two concrete interpretations or options" in prompt
    assert "one observable sign that would distinguish them" in prompt
    assert "one small next experiment, action, or response" in prompt
    assert "with a concrete cue when natural" in prompt
    assert "what the diarist would do differently next time" in prompt
    assert "generic wording that could fit any week or month" in prompt
    assert "do not force a productivity action" in prompt
    assert (
        "do not put a question or question mark in any paragraphs[].text" in prompt
    )
    assert "если программирование руками для тебя ближе к шахматам" in prompt
    assert "какого результата тебе было бы достаточно?" in prompt
    assert "какой результат хакатона останется для тебя ценным даже без приза?" in prompt
    assert "какой один опыт в роли игрока у более опытного мастера" in prompt
    assert "по какому признаку поймёшь, что он повлиял" in prompt
    assert "bad: \"что ты думаешь об этом?\"" in prompt


def test_critique_explicitly_checks_interpretation_recap_metaphor_and_question_failures():
    synthesis = _synthesis()
    completions = _RecordingCompletions(
        [{"approved": False, "issues": ["Replace the generic question."]}]
    )
    llm = reviews.OpenRouterReviewLLM(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="openai/gpt-5.6-terra",
        language="RU",
    )

    asyncio.run(llm.critique(kind="month", synthesis=synthesis, context=[]))

    prompt = _normalise(completions.calls[0]["messages"][0]["content"])
    assert "opening makes a direct interpretation" in prompt
    assert (
        "entire essay uses no more than three distinct diary situations" in prompt
    )
    assert "counting even one-clause mentions and counterexamples" in prompt
    assert "each recap includes only facts needed to test the claim" in prompt
    assert "explanation of their connection is fuller than the recap" in prompt
    assert "plausible alternative or counterexample is considered" in prompt
    assert "language is plain and concrete" in prompt
    assert "decorative, unexplained, extended, or multiple metaphors" in prompt
    assert "reflection-question contract" in prompt
    assert "no more than two tightly linked questions" in prompt
    assert "cited support" in prompt
    assert "telegram privacy and caption constraints" in prompt
    assert "safety handling" in prompt


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

from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from dairy_bot.services import reviews
from dairy_bot.services.reviews import runtime as review_runtime


def _document() -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id="diary:2026-07-31T09:00",
        source_type="diary",
        path="2026/07/2026-07-31.md",
        heading="09:00",
        text="A grounded reflection.",
        content_hash="entry-v1",
        document_date=date(2026, 7, 31),
        first_seen=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )


def _policy_violating_synthesis(title: str) -> reviews.ReviewSynthesis:
    return reviews.ReviewSynthesis(
        title=title,
        paragraphs=[
            reviews.ReviewParagraph(
                text="A diagnosed bipolar disorder proves the cause.",
                evidence_refs=[],
            )
        ],
        telegram_caption=(
            "2026-07-31 https://example.test diary:2026-07-31T09:00"
        ),
        reflection_question="A statement without a question mark",
        safety_note=None,
        visual_brief="x",
    )


class _NoParallel:
    async def search(self, *, objective, search_queries):
        return []


def _tools() -> reviews.ReviewPlannerTools:
    async def diary_search(query: str, cutoff: date):
        return []

    return reviews.ReviewPlannerTools(
        cutoff=date(2026, 8, 1),
        diary_search=diary_search,
        parallel_run=_NoParallel(),
    )


def test_AC_R1_critic_approved_synthesis_is_returned_verbatim_without_local_policy_gate():
    draft = _policy_violating_synthesis("Unfiltered draft")

    class LLM:
        def __init__(self) -> None:
            self.revise_calls = 0

        async def plan(self, **kwargs):
            return reviews.ReviewPlan(tool_calls=[])

        async def draft(self, **kwargs):
            return draft

        async def critique(self, **kwargs):
            return reviews.ReviewCritique(approved=True)

        async def revise(self, **kwargs):
            self.revise_calls += 1
            raise AssertionError("an approved draft must not be revised")

    llm = LLM()
    result = asyncio.run(
        reviews.ReviewGenerationPipeline(llm=llm, tools=_tools()).generate(
            kind="month",
            review_end=date(2026, 8, 1),
            documents=[_document()],
            deterministic_stats={"entry_count": 1, "active_days": 1},
        )
    )

    assert result.synthesis is draft
    assert llm.revise_calls == 0


def test_AC_R2_rejected_draft_gets_exactly_one_llm_revision_returned_verbatim():
    draft = _policy_violating_synthesis("Draft")
    revision = _policy_violating_synthesis("Revision returned as-is")

    class LLM:
        def __init__(self) -> None:
            self.critique_calls = 0
            self.revise_calls = 0

        async def plan(self, **kwargs):
            return reviews.ReviewPlan(tool_calls=[])

        async def draft(self, **kwargs):
            return draft

        async def critique(self, **kwargs):
            self.critique_calls += 1
            return reviews.ReviewCritique(
                approved=False,
                issues=["Rewrite this once"],
            )

        async def revise(self, **kwargs):
            self.revise_calls += 1
            assert kwargs["critique"] == reviews.ReviewCritique(
                approved=False,
                issues=["Rewrite this once"],
            )
            return revision

    llm = LLM()
    result = asyncio.run(
        reviews.ReviewGenerationPipeline(llm=llm, tools=_tools()).generate(
            kind="month",
            review_end=date(2026, 8, 1),
            documents=[_document()],
            deterministic_stats={"entry_count": 1, "active_days": 1},
        )
    )

    assert result.synthesis is revision
    assert llm.critique_calls == 1
    assert llm.revise_calls == 1


def test_AC_R3_synthesis_schema_keeps_types_without_local_content_minima():
    paragraph = reviews.ReviewParagraph(text="", evidence_refs=[])
    synthesis = reviews.ReviewSynthesis(
        title="",
        paragraphs=[],
        telegram_caption="",
        reflection_question="",
        safety_note=None,
        visual_brief="",
    )

    assert paragraph.text == "" and paragraph.evidence_refs == []
    assert synthesis.title == "" and synthesis.paragraphs == []
    assert "sparse_evidence_acknowledged" not in reviews.ReviewSynthesis.model_fields
    assert "weekly_trajectory" not in reviews.ReviewSynthesis.model_fields
    with pytest.raises(ValidationError):
        reviews.ReviewParagraph(text=123, evidence_refs=[])


def test_AC_R4_pipeline_and_runtime_have_no_local_sparse_or_self_harm_policy_inputs():
    parameters = inspect.signature(reviews.ReviewGenerationPipeline.generate).parameters
    runtime_source = inspect.getsource(review_runtime)

    assert "sparse_evidence" not in parameters
    assert "self_harm_risk" not in parameters
    assert "sparse_evidence=" not in runtime_source
    assert "self_harm_risk=" not in runtime_source
    assert not hasattr(review_runtime, "_has_explicit_self_harm_risk")
    assert "kill myself" not in inspect.getsource(review_runtime)

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dairy_bot.prompts import load_prompt

from .models import CorpusDocument, ReviewKind
from .parallel_search import ParallelSource, ReviewPlannerTools
from .synthesis import (
    ReviewCritique,
    ReviewSynthesis,
)


class ReviewToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Literal["search_diary", "parallel_search"]
    query: str | None = None
    objective: str | None = None
    search_queries: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_arguments(self) -> ReviewToolCall:
        if self.tool == "search_diary":
            if not (self.query or "").strip():
                raise ValueError("search_diary requires query")
            if self.objective is not None or self.search_queries:
                raise ValueError("search_diary accepts only query")
        else:
            if not (self.objective or "").strip():
                raise ValueError("parallel_search requires objective")
            if not self.search_queries:
                raise ValueError("parallel_search requires 1-5 search queries")
            if self.query is not None:
                raise ValueError("parallel_search does not accept query")
        return self


class ReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_calls: list[ReviewToolCall] = Field(default_factory=list, max_length=6)


@dataclass(frozen=True, slots=True)
class ReviewContextItem:
    evidence_id: str
    source_type: str
    label: str
    text: str
    internal_only: bool
    document_date: date | None = None
    source_hash: str | None = None

    @classmethod
    def from_document(cls, document: CorpusDocument) -> ReviewContextItem:
        label = document.path
        if document.heading:
            label = f"{label} · {document.heading}"
        return cls(
            evidence_id=document.document_id,
            source_type=document.source_type,
            label=label,
            text=document.text,
            internal_only=False,
            document_date=document.document_date,
            source_hash=document.content_hash,
        )

    @classmethod
    def from_parallel(cls, source: ParallelSource) -> ReviewContextItem:
        digest = hashlib.sha256(source.url.encode("utf-8")).hexdigest()[:16]
        return cls(
            evidence_id=f"parallel:{digest}",
            source_type="external",
            label=source.title,
            text="\n".join(source.excerpts),
            internal_only=True,
        )


@dataclass(frozen=True, slots=True)
class ReviewGenerationResult:
    synthesis: ReviewSynthesis
    used_evidence: tuple[ReviewContextItem, ...]


class ReviewLLM(Protocol):
    async def plan(
        self,
        *,
        kind: ReviewKind,
        review_end: date,
        documents: Sequence[CorpusDocument],
        stats: Mapping[str, Any],
        parallel_budget: int = 0,
    ) -> ReviewPlan: ...

    async def draft(self, **kwargs: Any) -> ReviewSynthesis: ...

    async def critique(self, **kwargs: Any) -> ReviewCritique: ...

    async def revise(self, **kwargs: Any) -> ReviewSynthesis: ...


class ReviewGenerationPipeline:
    """Bounded planner/tool/synthesis pipeline for one historical snapshot."""

    def __init__(self, *, llm: ReviewLLM, tools: ReviewPlannerTools) -> None:
        self.llm = llm
        self.tools = tools

    async def generate(
        self,
        *,
        kind: ReviewKind,
        review_end: date,
        documents: Sequence[CorpusDocument],
        deterministic_stats: Mapping[str, Any],
        initial_context: Sequence[ReviewContextItem] = (),
    ) -> ReviewGenerationResult:
        snapshot = tuple(
            sorted(
                (document for document in documents if document.eligible_on(review_end)),
                key=lambda document: document.document_id,
            )
        )
        stats = dict(deterministic_stats)
        plan_arguments = {
            "kind": kind,
            "review_end": review_end,
            "documents": snapshot,
            "stats": stats,
        }
        plan_signature = inspect.signature(self.llm.plan)
        if "parallel_budget" in plan_signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in plan_signature.parameters.values()
        ):
            plan_arguments["parallel_budget"] = self.tools.parallel_budget
        plan = await self.llm.plan(
            **plan_arguments,
        )
        context = await self._execute_plan(
            plan,
            review_end,
            initial_context=initial_context,
        )
        draft = await self.llm.draft(
            kind=kind,
            review_end=review_end,
            documents=snapshot,
            stats=stats,
            context=context,
        )
        critique_context = _merge_context(
            [ReviewContextItem.from_document(document) for document in snapshot],
            context,
        )
        critique = await self.llm.critique(
            kind=kind,
            synthesis=draft,
            context=critique_context,
        )
        final = (
            draft
            if critique.approved
            else await self.llm.revise(
                kind=kind,
                synthesis=draft,
                critique=critique,
                context=critique_context,
            )
        )
        return ReviewGenerationResult(
            synthesis=final,
            used_evidence=tuple(context),
        )

    async def _execute_plan(
        self,
        plan: ReviewPlan,
        review_end: date,
        *,
        initial_context: Sequence[ReviewContextItem] = (),
    ) -> list[ReviewContextItem]:
        context: list[ReviewContextItem] = []
        seen: set[str] = set()
        for item in initial_context:
            if item.evidence_id in seen:
                continue
            if item.document_date is not None and item.document_date > review_end:
                continue
            seen.add(item.evidence_id)
            context.append(item)
        for call in plan.tool_calls:
            if call.tool == "search_diary":
                raw_items = await self.tools.search_diary(call.query or "")
                items = [
                    item
                    for item in raw_items
                    if isinstance(item, ReviewContextItem)
                    and (
                        item.document_date is None
                        or item.document_date <= review_end
                    )
                ]
            else:
                sources = await self.tools.parallel_search(
                    objective=call.objective or "",
                    search_queries=call.search_queries,
                )
                items = [ReviewContextItem.from_parallel(source) for source in sources]
            for item in items:
                if item.evidence_id in seen:
                    continue
                seen.add(item.evidence_id)
                context.append(item)
        return context


class OpenRouterReviewLLM:
    """Strict structured-output adapter used by the review pipeline."""

    def __init__(self, *, client: Any, model: str, language: str) -> None:
        self.client = client
        self.model = model
        self.language = language

    async def plan(
        self,
        *,
        kind: ReviewKind,
        review_end: date,
        documents: Sequence[CorpusDocument],
        stats: Mapping[str, Any],
        parallel_budget: int = 0,
    ) -> ReviewPlan:
        budget_label = "call" if parallel_budget == 1 else "calls"
        return await self._complete(
            schema=ReviewPlan,
            schema_name="review_plan",
            system_prompt=load_prompt(
                "review/planner",
                parallel_budget=parallel_budget,
                budget_label=budget_label,
            ),
            payload={
                "kind": kind,
                "review_end": review_end.isoformat(),
                "stats": dict(stats),
                "parallel_search_budget": parallel_budget,
                "documents": [_document_payload(item) for item in documents],
            },
        )

    async def draft(
        self,
        *,
        kind: ReviewKind,
        review_end: date,
        documents: Sequence[CorpusDocument],
        stats: Mapping[str, Any],
        context: Sequence[ReviewContextItem],
    ) -> ReviewSynthesis:
        return await self._complete(
            schema=ReviewSynthesis,
            schema_name="review_synthesis",
            system_prompt=_review_system_prompt(
                phase="draft",
                language=self.language,
            ),
            payload={
                "kind": kind,
                "review_end": review_end.isoformat(),
                "stats": dict(stats),
                "documents": [_document_payload(item) for item in documents],
                "context": [_context_payload(item) for item in context],
            },
        )

    async def critique(
        self,
        *,
        kind: ReviewKind,
        synthesis: ReviewSynthesis,
        context: Sequence[ReviewContextItem],
    ) -> ReviewCritique:
        return await self._complete(
            schema=ReviewCritique,
            schema_name="review_critique",
            system_prompt=_review_system_prompt(
                phase="critique",
                language=self.language,
            ),
            payload={
                "kind": kind,
                "synthesis": synthesis.model_dump(mode="json"),
                "context": [_context_payload(item) for item in context],
            },
        )

    async def revise(
        self,
        *,
        kind: ReviewKind,
        synthesis: ReviewSynthesis,
        critique: ReviewCritique,
        context: Sequence[ReviewContextItem],
    ) -> ReviewSynthesis:
        return await self._complete(
            schema=ReviewSynthesis,
            schema_name="review_revision",
            system_prompt=_review_system_prompt(
                phase="revision",
                language=self.language,
            ),
            payload={
                "kind": kind,
                "synthesis": synthesis.model_dump(mode="json"),
                "critique": critique.model_dump(mode="json"),
                "context": [_context_payload(item) for item in context],
            },
        )

    async def _complete(
        self,
        *,
        schema: type[BaseModel],
        schema_name: str,
        system_prompt: str,
        payload: Mapping[str, Any],
    ) -> Any:
        completion = await self.client.chat.completions.create(
            model=self.model,
            reasoning_effort="low",
            max_completion_tokens=16_000,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": _strict_output_schema(schema),
                },
            },
            extra_body={"provider": {"require_parameters": True}},
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
        )
        raw = completion.choices[0].message.content or ""
        return schema.model_validate_json(raw)


def _review_system_prompt(
    *,
    phase: Literal["draft", "critique", "revision"],
    language: str,
) -> str:
    output_language = "Russian" if language.upper() == "RU" else "English"
    return load_prompt(
        f"review/{phase}",
        output_language=output_language,
    )


def _merge_context(
    primary: Sequence[ReviewContextItem],
    secondary: Sequence[ReviewContextItem],
) -> list[ReviewContextItem]:
    merged: list[ReviewContextItem] = []
    seen: set[str] = set()
    for item in (*primary, *secondary):
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        merged.append(item)
    return merged


def _strict_output_schema(schema: type[BaseModel]) -> dict[str, Any]:
    if schema is not ReviewPlan:
        return to_strict_json_schema(schema)
    return {
        "title": "ReviewPlan",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tool_calls": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "anyOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "tool": {
                                    "type": "string",
                                    "enum": ["search_diary"],
                                },
                                "query": {"type": "string", "minLength": 1},
                            },
                            "required": ["tool", "query"],
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "tool": {
                                    "type": "string",
                                    "enum": ["parallel_search"],
                                },
                                "objective": {"type": "string", "minLength": 1},
                                "search_queries": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "maxItems": 5,
                                },
                            },
                            "required": ["tool", "objective", "search_queries"],
                        },
                    ]
                },
            }
        },
        "required": ["tool_calls"],
    }


def _document_payload(document: CorpusDocument) -> dict[str, Any]:
    return {
        "evidence_id": document.document_id,
        "source_type": document.source_type,
        "path": document.path,
        "heading": document.heading,
        "date": document.document_date.isoformat()
        if document.document_date
        else None,
        "text": document.text,
    }


def _context_payload(item: ReviewContextItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "label": item.label,
        "text": item.text,
        "internal_only": item.internal_only,
    }

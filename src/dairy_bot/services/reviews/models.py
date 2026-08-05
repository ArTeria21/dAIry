from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from dairy_bot.services.diary_corpus import CorpusDocument

ReviewKind = Literal["week", "month"]


@dataclass(frozen=True, slots=True)
class ReviewPeriod:
    kind: ReviewKind
    period: str
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    kind: ReviewKind
    period: str
    start_date: date
    end_date: date
    status: str
    title: str
    payload: dict[str, Any]
    telegram_caption: str
    reflection_question: str
    safety_note: str | None
    image_path: str | None
    image_alt: str | None
    language: str
    model: str
    source_hash: str
    retrieval_model: str | None = None
    retrieval_recipe: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewSource:
    source_id: str
    source_type: str
    source_hash: str
    label: str
    position: int


@dataclass(frozen=True, slots=True)
class GenerationJob:
    job_id: int
    kind: ReviewKind
    period: str
    source_hash: str
    reason: str
    status: str
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramDelivery:
    kind: ReviewKind
    period: str
    chat_id: int
    status: str

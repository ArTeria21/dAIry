from __future__ import annotations

import asyncio
import html
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .models import GenerationJob, ReviewPeriod, ReviewRecord, ReviewSource
from .periods import period_for
from .store import ReviewStore


def due_delivery_periods(
    now: datetime,
    *,
    timezone: ZoneInfo,
    weekly_time: time,
    monthly_time: time,
) -> list[ReviewPeriod]:
    local = now.replace(tzinfo=timezone) if now.tzinfo is None else now.astimezone(timezone)
    due: list[ReviewPeriod] = []
    if local.weekday() == 6 and local.timetz().replace(tzinfo=None) >= weekly_time:
        due.append(period_for(local.date() - timedelta(days=1), kind="week", timezone=timezone))
    if local.day == 1 and local.timetz().replace(tzinfo=None) >= monthly_time:
        due.append(period_for(local.date() - timedelta(days=1), kind="month", timezone=timezone))
    return due


def order_backfill(periods: Sequence[ReviewPeriod]) -> list[ReviewPeriod]:
    return sorted(
        periods,
        key=lambda period: (0 if period.kind == "week" else 1, period.start_date),
    )


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResult:
    status: str
    attempts: int
    used_image: bool


class ReviewTelegramSender:
    def __init__(
        self,
        *,
        bot: Any,
        public_base_url: str,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.public_base_url = public_base_url.rstrip("/")
        self.max_attempts = max(1, max_attempts)
        self.sleep = sleep

    async def send(self, review: ReviewRecord, *, chat_id: int) -> TelegramDeliveryResult:
        used_image = bool(review.image_path and Path(review.image_path).is_file())
        caption = _render_caption(review)
        keyboard = _review_keyboard(self.public_base_url, review)
        for attempt in range(1, self.max_attempts + 1):
            try:
                if used_image:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=FSInputFile(review.image_path),
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                else:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                return TelegramDeliveryResult("sent", attempt, used_image)
            except (asyncio.TimeoutError, TimeoutError):
                return TelegramDeliveryResult("delivery_unknown", attempt, used_image)
            except TelegramNetworkError as error:
                if _caused_by_timeout(error):
                    return TelegramDeliveryResult("delivery_unknown", attempt, used_image)
                if attempt == self.max_attempts:
                    return TelegramDeliveryResult("failed", attempt, used_image)
                await self.sleep(float(2 ** (attempt - 1)))
            except (TelegramRetryAfter, TelegramServerError) as error:
                if attempt == self.max_attempts:
                    return TelegramDeliveryResult("failed", attempt, used_image)
                retry_after = getattr(error, "retry_after", None)
                await self.sleep(float(retry_after or 2 ** (attempt - 1)))
            except (
                TelegramForbiddenError,
                TelegramUnauthorizedError,
                TelegramBadRequest,
            ):
                return TelegramDeliveryResult("failed", attempt, used_image)
        return TelegramDeliveryResult("failed", self.max_attempts, used_image)


def _render_caption(review: ReviewRecord) -> str:
    body = html.escape(review.telegram_caption)
    if review.safety_note is None:
        return body
    return f"{body}\n\n{html.escape(review.safety_note)}"


def _review_keyboard(base_url: str, review: ReviewRecord) -> InlineKeyboardMarkup:
    url = f"{base_url}/#reviews/{review.kind}/{review.period}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="OPEN FULL REVIEW", url=url)]
        ]
    )


def _caused_by_timeout(error: BaseException) -> bool:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, (asyncio.TimeoutError, TimeoutError)):
            return True
        cause = cause.__cause__
    return False


@dataclass(frozen=True, slots=True)
class GeneratedReview:
    record: ReviewRecord
    sources: Sequence[ReviewSource]


GenerateReview = Callable[[GenerationJob], Awaitable[GeneratedReview]]
GenerateImage = Callable[..., Awaitable[str | Path | None]]
DeliverReview = Callable[[ReviewRecord], Awaitable[Any]]
CurrentSourceHash = Callable[[str, str], str | Awaitable[str]]


class ReviewJobRunner:
    """Run one durable job; a completed null image is a terminal result."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        generate_review: GenerateReview,
        generate_image: GenerateImage,
        deliver: DeliverReview,
        current_source_hash: CurrentSourceHash | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self.store = store
        self.generate_review = generate_review
        self.generate_image = generate_image
        self.deliver = deliver
        self.current_source_hash = current_source_hash
        self.now = now

    async def deliver_ready(self, record: ReviewRecord) -> Any:
        return await self.deliver(record)

    async def run_next(self) -> bool:
        job = self.store.claim_next_job(now=self.now())
        if job is None:
            return False
        try:
            if job.reason == "stale" and await self._supersede_if_changed(job):
                return True
            existing = self.store.get_review(job.kind, job.period)
            if (
                job.reason == "scheduled"
                and existing is not None
                and existing.status == "ready"
                and existing.source_hash == job.source_hash
            ):
                if await self._supersede_if_changed(job):
                    return True
                await self.deliver_ready(existing)
                self.store.set_job_status(job.job_id, "complete")
                return True
            generated = await self.generate_review(job)
            image_path = await self._generate_image(generated.record, job)
            if await self._supersede_if_changed(job):
                return True
            record = replace(
                generated.record,
                status="ready",
                image_path=str(image_path) if image_path is not None else None,
            )
            self.store.upsert_review(record, sources=generated.sources)
            self.store.set_job_status(job.job_id, "complete")
            if job.reason == "scheduled":
                await self.deliver(record)
            return True
        except Exception as error:
            self.store.record_job_failure(job.job_id, error, now=self.now())
            return True

    async def _generate_image(
        self,
        record: ReviewRecord,
        job: GenerationJob,
    ) -> str | Path | None:
        signature = inspect.signature(self.generate_image)
        parameters = tuple(signature.parameters.values())
        accepts_job = any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL
            for parameter in parameters
        ) or len(
            [
                parameter
                for parameter in parameters
                if parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
            ]
        ) >= 2
        if accepts_job:
            return await self.generate_image(record, job)
        return await self.generate_image(record)

    async def _supersede_if_changed(self, job: GenerationJob) -> bool:
        if self.current_source_hash is None:
            return False
        latest = self.current_source_hash(job.kind, job.period)
        if inspect.isawaitable(latest):
            latest = await latest
        if latest == job.source_hash:
            return False
        self.store.set_job_status(job.job_id, "superseded")
        if latest:
            self.store.enqueue_job(
                job.kind,
                job.period,
                latest,
                reason="stale",
            )
        return True

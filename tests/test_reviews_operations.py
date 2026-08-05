from __future__ import annotations

import asyncio
import html
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramServerError

from dairy_bot.services import reviews

TZ = ZoneInfo("Europe/Vienna")


def _record(*, image_path: str | None = None) -> reviews.ReviewRecord:
    return reviews.ReviewRecord(
        kind="week",
        period="2026-07-26",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 8, 1),
        status="ready",
        title="Pressure < agency",
        payload={"paragraphs": [{"text": "Web essay", "evidence_refs": ["diary:x"]}]},
        telegram_caption="A <calmer> grounded observation. " + "x" * 620,
        reflection_question="What remains open?",
        safety_note=None,
        image_path=image_path,
        image_alt="Abstract weekly symbol" if image_path else None,
        language="EN",
        model="test/model",
        source_hash="source-v1",
    )


def test_AC_3_5_due_slots_use_previous_closed_period_and_collision_order():
    before = reviews.due_delivery_periods(
        datetime(2026, 11, 1, 8, 59, tzinfo=TZ),
        timezone=TZ,
        weekly_time=time(9, 0),
        monthly_time=time(10, 0),
    )
    weekly = reviews.due_delivery_periods(
        datetime(2026, 11, 1, 9, 0, tzinfo=TZ),
        timezone=TZ,
        weekly_time=time(9, 0),
        monthly_time=time(10, 0),
    )
    both = reviews.due_delivery_periods(
        datetime(2026, 11, 1, 10, 0, tzinfo=TZ),
        timezone=TZ,
        weekly_time=time(9, 0),
        monthly_time=time(10, 0),
    )

    assert before == []
    assert [(item.kind, item.period) for item in weekly] == [("week", "2026-10-25")]
    assert [(item.kind, item.period) for item in both] == [
        ("week", "2026-10-25"),
        ("month", "2026-10"),
    ]


def test_AC_3_6_backfill_is_weeks_then_months_and_scheduled_jobs_claim_first(tmp_path):
    periods = [
        reviews.ReviewPeriod("month", "2026-07", date(2026, 7, 1), date(2026, 7, 31)),
        reviews.ReviewPeriod("week", "2026-07-26", date(2026, 7, 26), date(2026, 8, 1)),
        reviews.ReviewPeriod("week", "2026-07-19", date(2026, 7, 19), date(2026, 7, 25)),
    ]
    assert [(item.kind, item.period) for item in reviews.order_backfill(periods)] == [
        ("week", "2026-07-19"),
        ("week", "2026-07-26"),
        ("month", "2026-07"),
    ]

    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    backfill = store.enqueue_job("week", "2026-07-19", "one", reason="backfill")
    scheduled = store.enqueue_job("week", "2026-07-26", "two", reason="scheduled")
    assert store.claim_next_job().job_id == scheduled.job_id
    store.set_job_status(backfill.job_id, "running")
    assert store.reset_running_jobs() == 2
    assert store.claim_next_job().job_id == scheduled.job_id


class _Bot:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [object()])
        self.photo_calls: list[dict] = []
        self.message_calls: list[dict] = []

    async def send_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return self._outcome()

    async def send_message(self, **kwargs):
        self.message_calls.append(kwargs)
        return self._outcome()

    def _outcome(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_AC_3_7_telegram_photo_and_text_are_private_escaped_and_linked(tmp_path):
    image = tmp_path / "poster.jpg"
    image.write_bytes(b"\xff\xd8poster\xff\xd9")
    photo_bot = _Bot()
    photo_sender = reviews.ReviewTelegramSender(
        bot=photo_bot, public_base_url="https://diary.example"
    )
    photo = asyncio.run(photo_sender.send(_record(image_path=str(image)), chat_id=42))

    assert photo.status == "sent" and photo.used_image is True
    assert not photo_bot.message_calls and len(photo_bot.photo_calls) == 1
    photo_call = photo_bot.photo_calls[0]
    assert photo_call["caption"] == html.escape(
        _record(image_path=str(image)).telegram_caption
    )
    button = photo_call["reply_markup"].inline_keyboard[0][0]
    assert button.text == "OPEN FULL REVIEW"
    assert button.url == "https://diary.example/#reviews/week/2026-07-26"
    assert "diary:x" not in photo_call["caption"]

    text_bot = _Bot()
    text_sender = reviews.ReviewTelegramSender(
        bot=text_bot, public_base_url="https://diary.example/"
    )
    text = asyncio.run(text_sender.send(_record(), chat_id=42))
    assert text.status == "sent" and text.used_image is False
    assert not text_bot.photo_calls and len(text_bot.message_calls) == 1
    assert text_bot.message_calls[0]["reply_markup"].inline_keyboard[0][0].url.endswith(
        "#reviews/week/2026-07-26"
    )


def test_AC_3_8_telegram_timeout_is_unknown_but_server_failure_has_bounded_retry():
    timeout_bot = _Bot([asyncio.TimeoutError("ambiguous")])
    timeout_sender = reviews.ReviewTelegramSender(
        bot=timeout_bot, public_base_url="https://diary.example", max_attempts=3
    )
    unknown = asyncio.run(timeout_sender.send(_record(), chat_id=42))
    assert unknown.status == "delivery_unknown" and unknown.attempts == 1
    assert len(timeout_bot.message_calls) == 1

    server_bot = _Bot(
        [
            TelegramServerError(method=None, message="definitive failure"),
            TelegramServerError(method=None, message="definitive failure"),
            object(),
        ]
    )
    sleeps: list[float] = []

    async def sleep(delay: float):
        sleeps.append(delay)

    retry_sender = reviews.ReviewTelegramSender(
        bot=server_bot,
        public_base_url="https://diary.example",
        max_attempts=3,
        sleep=sleep,
    )
    sent = asyncio.run(retry_sender.send(_record(), chat_id=42))
    assert sent.status == "sent" and sent.attempts == 3
    assert sleeps == [1.0, 2.0]


def test_AC_3_9_finished_text_only_job_is_not_retried_or_sent_for_backfill(tmp_path):
    store = reviews.ReviewStore(tmp_path / "reviews.sqlite3")
    store.enqueue_job("week", "2026-07-26", "source-v1", reason="backfill")
    image_calls: list[str] = []
    delivery_calls: list[str] = []

    async def generate(job):
        return reviews.GeneratedReview(record=_record(), sources=[])

    async def image(record):
        image_calls.append(record.period)
        return None

    async def deliver(record):
        delivery_calls.append(record.period)

    runner = reviews.ReviewJobRunner(
        store=store,
        generate_review=generate,
        generate_image=image,
        deliver=deliver,
    )
    assert asyncio.run(runner.run_next()) is True
    assert asyncio.run(runner.run_next()) is False
    assert store.get_review("week", "2026-07-26").image_path is None
    assert image_calls == ["2026-07-26"]
    assert delivery_calls == []

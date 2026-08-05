from __future__ import annotations

import asyncio
import html
from datetime import date
from typing import Any

from dairy_bot.services import reviews


class _CapturingBot:
    def __init__(self) -> None:
        self.message_calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> object:
        self.message_calls.append(kwargs)
        return object()

    async def send_photo(self, **kwargs: Any) -> object:
        raise AssertionError("a review without an image must use a text message")


def test_AC_N1_telegram_caption_is_only_escaped_and_safety_note_concatenated():
    body = "  <body> " + "B" * 950 + "  "
    safety_note = "  Contact trusted support & <emergency help>  "
    record = reviews.ReviewRecord(
        kind="week",
        period="2026-07-26",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 8, 1),
        status="ready",
        title="A difficult week",
        payload={"paragraphs": []},
        telegram_caption=body,
        reflection_question="What support feels reachable?",
        safety_note=safety_note,
        image_path=None,
        image_alt=None,
        language="EN",
        model="test/model",
        source_hash="source-v1",
    )
    bot = _CapturingBot()
    sender = reviews.ReviewTelegramSender(
        bot=bot,
        public_base_url="https://diary.example",
    )

    result = asyncio.run(sender.send(record, chat_id=42))

    assert result.status == "sent"
    assert len(bot.message_calls) == 1
    caption = bot.message_calls[0]["text"]
    expected = f"{html.escape(body)}\n\n{html.escape(safety_note)}"
    assert caption == expected
    assert len(caption) > 900

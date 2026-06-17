import asyncio
from types import SimpleNamespace

import pytest

from dairy_bot.services.toc_service import (
    TocLLMResponseError,
    _parse_llm_json,
    _summarize_note,
)


class FakeCompletionsResource:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish_reason = self.responses.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )


def run(coro):
    return asyncio.run(coro)


def test_parse_llm_json_raises_toc_error_without_leaking_raw_output():
    with pytest.raises(TocLLMResponseError) as exc_info:
        _parse_llm_json('{"summary":"unfinished')

    message = str(exc_info.value)
    assert "invalid JSON" in message
    assert "unfinished" not in message


def test_summarize_note_retries_once_after_invalid_json():
    completions = FakeCompletionsResource(
        [
            ('{"summary":"unfinished', "length"),
            ('{"summary":"A reflective note.","tags":["reflection","bogus"]}', "stop"),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = run(
        _summarize_note(
            client,
            cleaned_text="Сегодня была заметка про рефлексию.",
            rel_path="2026/06/2026-06-17.md",
            model_name="test/model",
            max_tags=5,
        )
    )

    assert result == {"summary": "A reflective note.", "tags": ["reflection"]}
    assert len(completions.calls) == 2
    assert completions.calls[0]["max_tokens"] == 500
    assert "previous response could not be parsed" in (
        completions.calls[1]["messages"][0]["content"]
    )

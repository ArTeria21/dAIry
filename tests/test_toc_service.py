import asyncio
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from dairy_bot.services import toc_service
from dairy_bot.services.toc_service import (
    TocLLMResponseError,
    _parse_llm_json,
    reconcile_toc,
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


def test_AC_5_1_toc_retries_schema_invalid_output_without_manual_cleanup():
    completions = FakeCompletionsResource(
        [
            (
                '{"summary":"  A reflective note.  ",'
                '"tags":["reflection","bogus","work"]}',
                "stop",
            ),
            (
                '{"summary":"  A factual second response.  ",'
                '"tags":["reflection","work"]}',
                "stop",
            ),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = run(
        _summarize_note(
            client,
            raw_text="Сегодня была заметка про рефлексию.",
            rel_path="2026/06/2026-06-17.md",
            model_name="test/model",
            max_tags=2,
        )
    )

    assert result == {
        "summary": "  A factual second response.  ",
        "tags": ["reflection", "work"],
    }
    assert len(completions.calls) == 2
    assert all(call["max_tokens"] == 500 for call in completions.calls)
    assert all(
        call["extra_body"] == {"provider": {"require_parameters": True}}
        for call in completions.calls
    )
    response_format = completions.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    tags_schema = response_format["json_schema"]["schema"]["properties"]["tags"]
    assert tags_schema["maxItems"] == 2
    assert tags_schema["items"]["enum"]


def test_AC_5_2_repeated_schema_invalid_output_is_rejected_without_healing():
    completions = FakeCompletionsResource(
        [
            ('{"summary":"A note.","tags":["bogus"]}', "stop"),
            (
                '{"summary":"Another note.",'
                '"tags":["reflection","work","stress"]}',
                "stop",
            ),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(TocLLMResponseError, match="declared schema"):
        run(
            _summarize_note(
                client,
                raw_text="A note.",
                rel_path="2026/06/2026-06-17.md",
                model_name="test/model",
                max_tags=2,
            )
        )

    assert len(completions.calls) == 2


def test_AC_N3_toc_sends_full_raw_markdown_without_truncation(tmp_path, monkeypatch):
    note = tmp_path / "2026" / "06" / "2026-06-17.md"
    note.parent.mkdir(parents=True)
    raw_markdown = (
        "---\n"
        "date: 2026-06-17\n"
        "type: daily\n"
        "---\n"
        "# 2026-06-17\n"
        "[[2026-06-16|Prev day]] · [[2026-06-18|Next day]]\n\n"
        + "A" * 8100
        + "\nTAIL_SENTINEL\n"
    )
    note.write_text(raw_markdown, encoding="utf-8")
    completions = FakeCompletionsResource(
        [('{"summary":"A long note.","tags":["reflection"]}', "stop")]
    )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=completions)

        async def close(self):
            return None

    monkeypatch.setattr(toc_service, "AsyncOpenAI", FakeAsyncOpenAI)
    settings = SimpleNamespace(
        toc_enabled=True,
        toc_filename="table_of_contents.md",
        toc_extra_dirs=[],
        toc_model="test/model",
        toc_max_tags=5,
        openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_api_key=SecretStr("secret"),
        timezone=ZoneInfo("Europe/Vienna"),
        language="EN",
    )

    run(reconcile_toc(tmp_path, settings))

    assert len(completions.calls) == 1
    user_message = completions.calls[0]["messages"][1]
    assert user_message == {
        "role": "user",
        "content": (
            "Note path: 2026/06/2026-06-17.md\n\n"
            f"Note content:\n{raw_markdown}"
        ),
    }
    assert "date: 2026-06-17" in user_message["content"]
    assert "Prev day" in user_message["content"]
    assert "TAIL_SENTINEL" in user_message["content"]


def test_AC_N3_toc_does_not_heal_markdown_fenced_json():
    fenced = '```json\n{"summary":"A note.","tags":[]}\n```'

    with pytest.raises(TocLLMResponseError, match="invalid JSON"):
        _parse_llm_json(fenced)


def test_AC_5_3_failed_schema_validation_does_not_rewrite_saved_toc_state(
    tmp_path,
    monkeypatch,
):
    note = tmp_path / "2026" / "06" / "2026-06-17.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 2026-06-17\n\nChanged diary content.\n", encoding="utf-8")
    state_path = tmp_path / toc_service.TOC_STATE_FILENAME
    saved_state = (
        '{\n  "2026/06/2026-06-17.md": {\n'
        '    "content_hash": "old",\n'
        '    "mtime": 0,\n'
        '    "summary": "Saved summary.",\n'
        '    "tags": ["reflection"]\n'
        "  }\n}"
    )
    state_path.write_text(saved_state, encoding="utf-8")
    toc_path = tmp_path / "table_of_contents.md"
    saved_toc = "# Existing TOC\n\nDo not replace this on invalid output.\n"
    toc_path.write_text(saved_toc, encoding="utf-8")
    completions = FakeCompletionsResource(
        [
            ('{"summary":"Bad.","tags":["bogus"]}', "stop"),
            ('{"summary":"Still bad.","tags":["also_bogus"]}', "stop"),
        ]
    )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=completions)

        async def close(self):
            return None

    monkeypatch.setattr(toc_service, "AsyncOpenAI", FakeAsyncOpenAI)
    settings = SimpleNamespace(
        toc_enabled=True,
        toc_filename="table_of_contents.md",
        toc_extra_dirs=[],
        toc_model="test/model",
        toc_max_tags=2,
        openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_api_key=SecretStr("secret"),
        timezone=ZoneInfo("Europe/Vienna"),
        language="EN",
    )

    changed = run(reconcile_toc(tmp_path, settings))

    assert changed == []
    assert state_path.read_text(encoding="utf-8") == saved_state
    assert toc_path.read_text(encoding="utf-8") == saved_toc

import asyncio
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

from dairy_bot.config import Settings
from dairy_bot.services import toc_service
from dairy_bot.services.enrichment_client import OpenRouterEnrichmentClient
from dairy_bot.services.enrichment_schemas import Mood, Topic


def run(coro):
    return asyncio.run(coro)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "BOT_TOKEN": "123:telegram-token",
        "ALLOWED_USER_ID": 123,
        "OPENROUTER_API_KEY": "sk-or-test",
        "JOURNAL_DIR": tmp_path,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_AC_1_settings_reads_ru_language_from_env(tmp_path):
    settings = make_settings(tmp_path, LANGUAGE="RU")

    assert settings.language == "RU"


def test_AC_2_settings_reads_en_language_from_env(tmp_path):
    settings = make_settings(tmp_path, LANGUAGE="EN")

    assert settings.language == "EN"


def test_EC_1_settings_defaults_language_to_en_when_env_is_absent(tmp_path):
    settings = make_settings(tmp_path)

    assert settings.language == "EN"


def test_ERR_1_settings_rejects_language_values_outside_ru_en(tmp_path):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, LANGUAGE="DE")


class FakeCompletionsResource:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=self.content,
                        refusal=None,
                    ),
                )
            ]
        )


def make_enrichment_client(language: str, content: str):
    client = object.__new__(OpenRouterEnrichmentClient)
    client.settings = SimpleNamespace(
        enrichment_model_name="test/model",
        language=language,
    )
    completions = FakeCompletionsResource(content)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_AC_3_note_enrichment_prompt_requests_russian_gist_and_mood_evidence():
    client, completions = make_enrichment_client(
        "RU",
        (
            '{"gist":"Короткое резюме.","mood_evidence":"Спокойный тон.",'
            '"mood":"calm","mood_confidence":0.7,"topics":["reflection"]}'
        ),
    )

    result = run(client.enrich_note("Сегодня было спокойно."))

    system_prompt = completions.calls[0]["messages"][0]["content"]
    assert result.mood == Mood.calm
    assert "Write gist and mood_evidence in Russian." in system_prompt


def test_AC_4_note_enrichment_prompt_requests_english_gist_and_mood_evidence():
    client, completions = make_enrichment_client(
        "EN",
        (
            '{"gist":"A short summary.","mood_evidence":"The tone is calm.",'
            '"mood":"calm","mood_confidence":0.7,"topics":["reflection"]}'
        ),
    )

    result = run(client.enrich_note("Today was calm."))

    system_prompt = completions.calls[0]["messages"][0]["content"]
    assert result.mood == Mood.calm
    assert "Write gist and mood_evidence in English." in system_prompt


def test_AC_5_day_enrichment_prompt_requests_russian_summary_and_evidence_fields():
    client, completions = make_enrichment_client(
        "RU",
        (
            '{"summary":"День про спокойствие.","mood":"calm",'
            '"mood_confidence":0.7,"key_topics":["reflection"],'
            '"sport_evidence":null,"sport":null,'
            '"reading_evidence":null,"reading":null,'
            '"purchases_evidence":null,"purchases":null,'
            '"eating_outside_evidence":null,"eating_outside":null,'
            '"deep_focus_evidence":null,"deep_focus":null,'
            '"sleep_quality_evidence":null,"sleep_quality":null}'
        ),
    )

    result = run(client.enrich_day("Daily note content"))

    system_prompt = completions.calls[0]["messages"][0]["content"]
    assert result.summary == "День про спокойствие."
    assert "Write summary and all evidence fields in Russian." in system_prompt


class FakeAsyncOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.completions = FakeCompletionsResource(
            '{"summary":"Русское резюме.","tags":["reflection"]}'
        )
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False
        self.instances.append(self)

    async def close(self):
        self.closed = True


def make_toc_settings(language: str):
    return SimpleNamespace(
        toc_enabled=True,
        toc_filename="table_of_contents.md",
        toc_extra_dirs=[],
        toc_model="test/model",
        toc_max_tags=5,
        openrouter_base_url="https://openrouter.example.test",
        openrouter_api_key=SecretStr("sk-or-test"),
        timezone=ZoneInfo("Europe/Vienna"),
        language=language,
    )


def write_daily_note(root: Path) -> Path:
    path = root / "2026" / "06" / "2026-06-17.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# 2026-06-17\n\nСегодня была заметка про рефлексию.",
        encoding="utf-8",
    )
    return path


def test_AC_6_toc_prompt_requests_russian_json_summary(tmp_path, monkeypatch):
    FakeAsyncOpenAI.instances.clear()
    write_daily_note(tmp_path)
    monkeypatch.setattr(toc_service, "AsyncOpenAI", FakeAsyncOpenAI)

    changed = run(toc_service.reconcile_toc(tmp_path, make_toc_settings("RU")))

    system_prompt = FakeAsyncOpenAI.instances[0].completions.calls[0]["messages"][0][
        "content"
    ]
    assert tmp_path / "table_of_contents.md" in changed
    assert "Always write the summary in Russian" in system_prompt


def test_EC_2_ru_language_keeps_mood_topics_key_topics_and_toc_tags_in_english(
    tmp_path, monkeypatch
):
    client, completions = make_enrichment_client(
        "RU",
        (
            '{"gist":"Короткое резюме.","mood_evidence":"Спокойный тон.",'
            '"mood":"calm","mood_confidence":0.7,"topics":["reflection"]}'
        ),
    )

    run(client.enrich_note("Сегодня было спокойно."))

    note_schema = completions.calls[0]["response_format"]["json_schema"][
        "schema"
    ]
    assert "calm" in note_schema["properties"]["mood"]["enum"]
    assert "спокойствие" not in note_schema["properties"]["mood"]["enum"]
    assert "reflection" in note_schema["$defs"]["Topic"]["enum"]
    assert "рефлексия" not in note_schema["$defs"]["Topic"]["enum"]

    FakeAsyncOpenAI.instances.clear()
    write_daily_note(tmp_path)
    monkeypatch.setattr(toc_service, "AsyncOpenAI", FakeAsyncOpenAI)
    run(toc_service.reconcile_toc(tmp_path, make_toc_settings("RU")))

    toc_schema = FakeAsyncOpenAI.instances[0].completions.calls[0]["response_format"][
        "json_schema"
    ]["schema"]
    assert "reflection" in toc_schema["properties"]["tags"]["items"]["enum"]
    assert "рефлексия" not in toc_schema["properties"]["tags"]["items"]["enum"]


def test_AC_7_language_flag_is_documented_in_env_example_and_readme():
    env_example = Path(".env.example").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "LANGUAGE=EN" in env_example
    assert "LANGUAGE=EN" in readme
    assert "RU" in readme
    assert "EN" in readme

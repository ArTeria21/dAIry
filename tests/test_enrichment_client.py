import asyncio
from types import SimpleNamespace

from dairy_bot.services.enrichment_client import (
    OpenRouterEnrichmentClient,
    OPENROUTER_STRUCTURED_EXTRA_BODY,
    STRUCTURED_OUTPUT_MAX_TOKENS,
    _response_format,
)
from dairy_bot.services.enrichment_schemas import DayEnrichment, NoteEnrichment


class FakeEmbeddingsResource:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
        )


class FakeSettings:
    embedding_model_name = "openai/text-embedding-3-small"
    enrichment_model_name = "test/model"
    language = "EN"


class FakeCompletionsResource:
    def __init__(self, content: str):
        self.content = content
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
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


def run(coro):
    return asyncio.run(coro)


def test_AC_1_embedding_request_uses_configured_model_and_float_encoding():
    client = object.__new__(OpenRouterEnrichmentClient)
    client.settings = FakeSettings()
    fake_embeddings = FakeEmbeddingsResource()
    client.client = SimpleNamespace(embeddings=fake_embeddings)

    result = run(client.embed_note("journal text"))

    assert result == [0.1, 0.2, 0.3]
    assert fake_embeddings.kwargs == {
        "model": "openai/text-embedding-3-small",
        "input": "journal text",
        "encoding_format": "float",
    }


def test_structured_completion_requires_schema_capable_openrouter_provider():
    client = object.__new__(OpenRouterEnrichmentClient)
    client.settings = FakeSettings()
    completions = FakeCompletionsResource(
        (
            '{"gist":"A short summary.","mood_evidence":"The tone is calm.",'
            '"mood":"calm","mood_confidence":0.7,"topics":["reflection"]}'
        )
    )
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    run(client.enrich_note("Today was calm."))

    assert completions.kwargs["max_tokens"] == STRUCTURED_OUTPUT_MAX_TOKENS
    assert completions.kwargs["extra_body"] == OPENROUTER_STRUCTURED_EXTRA_BODY
    assert completions.kwargs["response_format"] == _response_format(
        NoteEnrichment, "note_enrichment"
    )


def test_note_enrichment_response_format_uses_openai_strict_schema():
    response_format = _response_format(NoteEnrichment, "note_enrichment")

    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["name"] == "note_enrichment"
    assert json_schema["strict"] is True

    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "gist",
        "mood_evidence",
        "mood",
        "mood_confidence",
        "topics",
    ]
    assert schema["properties"]["mood"]["type"] == "string"
    assert "enum" in schema["properties"]["mood"]
    assert schema["properties"]["topics"]["items"] == {"$ref": "#/$defs/Topic"}
    assert "Topic" in schema["$defs"]


def test_day_enrichment_response_format_requires_nullable_sparse_fields():
    schema = _response_format(DayEnrichment, "day_enrichment")["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert "sport_evidence" in schema["required"]
    assert "sport" in schema["required"]
    assert schema["properties"]["sport_evidence"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert schema["properties"]["sport"]["anyOf"] == [
        {"type": "boolean"},
        {"type": "null"},
    ]
    _assert_openai_strict_schema_subset(schema)


def _assert_openai_strict_schema_subset(value):
    if isinstance(value, list):
        for item in value:
            _assert_openai_strict_schema_subset(item)
        return
    if not isinstance(value, dict):
        return

    assert "default" not in value
    if value.get("type") == "object":
        assert value.get("additionalProperties") is False
        assert value.get("required") == list(value.get("properties", {}))

    for child in value.values():
        _assert_openai_strict_schema_subset(child)

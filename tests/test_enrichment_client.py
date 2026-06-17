import asyncio
from types import SimpleNamespace

from dairy_bot.services.enrichment_client import (
    OpenRouterEnrichmentClient,
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


def test_note_enrichment_response_format_inlines_ref_sibling_keywords():
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
    assert "$ref" not in schema["properties"]["mood"]
    assert "$ref" not in schema["properties"]["topics"]["items"]
    assert "$defs" not in schema


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
    _assert_strict_schema_subset(schema)


def _assert_strict_schema_subset(value):
    if isinstance(value, list):
        for item in value:
            _assert_strict_schema_subset(item)
        return
    if not isinstance(value, dict):
        return

    assert "default" not in value
    assert "title" not in value
    assert "$defs" not in value
    assert "definitions" not in value
    assert "$ref" not in value
    if value.get("type") == "object":
        assert value.get("additionalProperties") is False
        assert value.get("required") == list(value.get("properties", {}))

    for child in value.values():
        _assert_strict_schema_subset(child)

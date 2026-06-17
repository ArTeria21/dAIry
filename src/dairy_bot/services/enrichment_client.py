from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from dairy_bot.config import Settings, language_display_name
from dairy_bot.services.enrichment_schemas import DayEnrichment, NoteEnrichment, Topic


def _note_system_prompt(language: str) -> str:
    output_language = language_display_name(language)
    return (
        "You enrich one personal journal note. Follow the schema exactly. "
        f"Write gist and mood_evidence in {output_language}. "
        "Select topics only from the schema enum. "
        "Do not infer facts beyond the note text."
    )


def _day_system_prompt(language: str) -> str:
    output_language = language_display_name(language)
    return (
        "You enrich one whole day of a personal journal. Follow the schema exactly. "
        "Sparse facts must be null unless explicitly mentioned in the day's notes. "
        f"Write summary and all evidence fields in {output_language}."
    )


class OpenRouterEnrichmentClient:
    """OpenRouter-backed enrichment and embedding client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
        )

    async def enrich_note(self, text: str) -> NoteEnrichment:
        raw = await self._structured_completion(
            model=self.settings.enrichment_model_name,
            schema_model=NoteEnrichment,
            schema_name="note_enrichment",
            system_prompt=_note_system_prompt(getattr(self.settings, "language", "EN")),
            user_prompt=f"Journal note:\n{text}",
        )
        return NoteEnrichment.model_validate(raw)

    async def enrich_day(self, text: str) -> DayEnrichment:
        raw = await self._structured_completion(
            model=self.settings.enrichment_model_name,
            schema_model=DayEnrichment,
            schema_name="day_enrichment",
            system_prompt=_day_system_prompt(getattr(self.settings, "language", "EN")),
            user_prompt=f"Daily journal note with note-level labels:\n{text}",
        )
        return DayEnrichment.model_validate(raw)

    async def embed_note(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.settings.embedding_model_name,
            input=text,
            encoding_format="float",
        )
        return list(response.data[0].embedding)

    async def close(self) -> None:
        await self.client.close()

    async def _structured_completion(
        self,
        *,
        model: str,
        schema_model: type[BaseModel],
        schema_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        completion = await self.client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format=_response_format(schema_model, schema_name),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = completion.choices[0].message.content or "{}"
        return _parse_json(raw)


def build_enrichment_client(settings: Settings) -> OpenRouterEnrichmentClient:
    return OpenRouterEnrichmentClient(settings)


def _response_format(schema_model: type[BaseModel], name: str) -> dict[str, Any]:
    schema = _strict_json_schema(schema_model)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def _strict_json_schema(schema_model: type[BaseModel]) -> dict[str, Any]:
    """Normalize Pydantic output for OpenAI strict JSON schema mode.

    Invariants: inline refs, drop defaults/titles, require every declared
    property, and close all object schemas with additionalProperties=false.
    """
    raw_schema = schema_model.model_json_schema()
    schema = _normalize_json_schema(raw_schema, root=raw_schema)
    schema.pop("$defs", None)
    schema.pop("definitions", None)
    return schema


def _normalize_json_schema(value: Any, *, root: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_normalize_json_schema(item, root=root) for item in value]
    if not isinstance(value, dict):
        return value

    schema = _resolve_schema_ref(value, root=root)
    schema.pop("default", None)
    schema.pop("title", None)

    if schema.get("type") == "object":
        schema["additionalProperties"] = False

    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["required"] = list(properties)
        schema["properties"] = {
            key: _normalize_json_schema(prop_schema, root=root)
            for key, prop_schema in properties.items()
        }

    items = schema.get("items")
    if isinstance(items, dict):
        schema["items"] = _normalize_json_schema(items, root=root)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        schema["anyOf"] = _normalize_json_schema(any_of, root=root)

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        normalized_all_of = _normalize_json_schema(all_of, root=root)
        if len(normalized_all_of) == 1:
            schema.pop("allOf")
            schema.update(normalized_all_of[0])
        else:
            schema["allOf"] = normalized_all_of

    return schema


def _resolve_schema_ref(schema: dict[str, Any], *, root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return deepcopy(schema)

    resolved = _resolve_json_pointer(root, ref)
    sibling_schema = {key: value for key, value in schema.items() if key != "$ref"}
    return {**deepcopy(resolved), **deepcopy(sibling_schema)}


def _resolve_json_pointer(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Unsupported JSON Schema ref: {ref}")

    current: Any = schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    if not isinstance(current, dict):
        raise ValueError(f"JSON Schema ref does not point to an object: {ref}")
    return current


def _parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = [
            line
            for line in text.splitlines()
            if not line.strip().startswith("```")
        ]
        text = "\n".join(lines).strip()
    return json.loads(text)


def allowed_topic_values() -> list[str]:
    return [topic.value for topic in Topic]

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from dairy_bot.config import Settings, language_display_name
from dairy_bot.services.enrichment_schemas import DayEnrichment, NoteEnrichment, Topic


STRUCTURED_OUTPUT_MAX_TOKENS = 2_000
OPENROUTER_STRUCTURED_EXTRA_BODY = {
    "provider": {"require_parameters": True},
    "plugins": [{"id": "response-healing"}],
}


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
            max_tokens=STRUCTURED_OUTPUT_MAX_TOKENS,
            response_format=_response_format(schema_model, schema_name),
            extra_body=OPENROUTER_STRUCTURED_EXTRA_BODY,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        choice = completion.choices[0]
        message = choice.message
        raw = message.content or ""
        if raw.strip():
            return schema_model.model_validate_json(raw).model_dump(mode="json")

        refusal = getattr(message, "refusal", None)
        raise ValueError(
            "Structured completion returned no parseable content "
            f"(schema={schema_name}, finish_reason={choice.finish_reason!r}, "
            f"refusal={refusal!r})"
        )


def build_enrichment_client(settings: Settings) -> OpenRouterEnrichmentClient:
    return OpenRouterEnrichmentClient(settings)


def _response_format(schema_model: type[BaseModel], name: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": to_strict_json_schema(schema_model),
        },
    }


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

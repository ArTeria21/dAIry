import base64
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from dairy_bot.config import Settings

PROMPT_TEXT = (
    "Role: You are an expert personal stenographer creating clean, readable notes for a diary.\n"
    "Context: The speaker is a Russian native speaker living in Austria who works in Tech/ML.\n"
    "Instructions:\n"
    "1. Primary Language: Transcribe mainly in Russian.\n"
    "2. Multilingual Handling: The audio contains mixed languages. \n"
    "   - English: Technical terms (Machine Learning, Python, coding, Large Language Models (LLMs)).\
    Write them in English (e.g., 'backend', 'deployment', 'llm', 'ChatGPT', 'Claude Code'), NEVER transliterate to Cyrillic.\n"
    "   - German: Locations, street names, and daily life terms specific to Austria. Write them in correct German (e.g., 'Meldezettel', 'Hauptbahnhof'), even if pronounced with an accent.\n"
    "3. Formatting: Output clean, grammatically correct text. Remove stuttering, filler words (e.g., 'э-э', 'ну'), and self-corrections. Structure the text into logical paragraphs.\n"
    "4. Output: Return ONLY the text, no introductory phrases."
)


def _decode_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
        return " ".join(part for part in parts if part).strip()
    return ""


async def transcribe_audio(path: Path, settings: Settings) -> str:
    audio_bytes = path.read_bytes()
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    client = AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )

    try:
        completion = await client.chat.completions.create(
            model=settings.voice_model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT_TEXT},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_base64, "format": "wav"},
                        },
                    ],
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - best-effort guard
        raise RuntimeError("Transcription failed") from exc
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    choice = completion.choices[0].message.content
    return _decode_message_content(choice)

import base64
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from dairy_bot.config import Settings

PROMPT_TEXT = (
    "Transcribe the audio in the primary spoken language without translating. "
    "Keep any mixed-language words (brands, names, quoted phrases) exactly as spoken. "
    "Return only the transcription text with natural punctuation."
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
            model=settings.voxtrail_model_name,
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

    choice = completion.choices[0].message.content
    return _decode_message_content(choice)

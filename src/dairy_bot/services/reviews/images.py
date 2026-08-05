from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Any, Protocol

from dairy_bot.prompts import load_prompt, load_prompt_bytes

IMAGE_API_URL = "https://openrouter.ai/api/v1/images"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_SAFE_PERIOD_RE = re.compile(r"^[0-9]{4}-(?:[0-9]{2}|[0-9]{2}-[0-9]{2})$")


class ImageResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]: ...


class ImageHTTPClient(Protocol):
    async def post(self, url: str, **kwargs: Any) -> ImageResponse: ...


def load_style_prompt_bytes() -> bytes:
    return load_prompt_bytes("review/visual_style")


def load_style_prompt() -> str:
    return load_style_prompt_bytes().decode("utf-8")


def build_visual_prompt(kind: str, visual_brief: str) -> str:
    if kind not in {"week", "month"}:
        raise ValueError(f"Unsupported review kind: {kind}")
    return load_prompt(f"review/image_{kind}", visual_brief=visual_brief)


class OpenRouterImageGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        http_client: ImageHTTPClient,
        output_dir: Path,
        primary_model: str,
        fallback_model: str,
        primary_attempts: int = 2,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.http_client = http_client
        self.output_dir = Path(output_dir)
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.primary_attempts = max(1, primary_attempts)
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        *,
        kind: str,
        period: str,
        visual_brief: str,
        job_id: int | None = None,
    ) -> Path | None:
        if not _SAFE_PERIOD_RE.fullmatch(period):
            raise ValueError("Invalid review period for image filename")
        if job_id is not None and job_id <= 0:
            raise ValueError("Invalid review job id for image filename")
        models = [self.primary_model] * self.primary_attempts + [self.fallback_model]
        for model in models:
            try:
                image = await self._request(
                    model,
                    build_visual_prompt(kind, visual_brief),
                )
                return await self._save(kind, period, image, job_id=job_id)
            except Exception:
                continue
        return None

    async def _request(self, model: str, prompt: str) -> bytes:
        response = await self.http_client.post(
            IMAGE_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "prompt": prompt,
                "n": 1,
                "aspect_ratio": "1:1",
                "quality": "medium",
                "output_format": "jpeg",
                "output_compression": 85,
            },
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except Exception as error:
            raise RuntimeError("Image provider request failed") from error
        data = response.json().get("data", [])
        if not data or not isinstance(data[0], dict):
            raise ValueError("Image response contains no data")
        if data[0].get("media_type") != "image/jpeg":
            raise ValueError("Image response is not JPEG")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str):
            raise ValueError("Image response has no base64 payload")
        image = base64.b64decode(encoded, validate=True)
        if not 4 <= len(image) <= MAX_IMAGE_BYTES:
            raise ValueError("Image response size is invalid")
        if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
            raise ValueError("Image response has invalid JPEG markers")
        return image

    async def _save(
        self,
        kind: str,
        period: str,
        image: bytes,
        *,
        job_id: int | None,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if job_id is None else f"-job-{job_id}"
        target = self.output_dir / f"{kind}-{period}{suffix}.jpg"
        temporary = target.with_suffix(".jpg.tmp")
        try:
            await asyncio.to_thread(temporary.write_bytes, image)
            await asyncio.to_thread(os.replace, temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

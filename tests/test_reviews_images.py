from __future__ import annotations

import asyncio
import base64

from dairy_bot.services import reviews
from dairy_bot.services.reviews import images as image_module

STYLE_PROMPT = "Render it as an archival retro-futurist technical collage / experimental graphic poster: layered scanned engineering blueprints and schematic linework over a faded paper base; precise thin drafting lines, measurement marks, diagram overlays, semi-transparent layers with slight misregistration.  Monochrome foundation (ink black / charcoal / warm off-white paper) with a single dominant accent ink treatment: thin-film iridescence like an oil/gasoline slick on water (interference rainbow gradient sheen), used on a few flat geometric overlays (circles, squares, rectangles) and small signal markers; the iridescent accent should feel like printed/overlaid ink, not glossy CGI.  Photocopy/offset/risograph print character: heavy film grain, halftone dots and dithering, xerox noise, dust specks, scratches, subtle smudges, uneven inking, worn scan artifacts; matte print finish, crisp-but-imperfect edges.  High-contrast photo fragments (if present) treated as halftone/dither cutouts integrated into the schematic layers; micro-annotations and interface-like marks should feel technical and mostly illegible. No text in the image. Keep composition modular and adaptable to the canvas (do not lock a single framing); balanced negative space, information-dense layering, graphic—not glossy—look."


def test_AC_3_1_style_prompt_resource_is_byte_for_byte_immutable():
    assert reviews.load_style_prompt_bytes() == STYLE_PROMPT.encode("utf-8")
    assert reviews.load_style_prompt() == STYLE_PROMPT


def test_AC_3_2_visual_prompts_keep_style_separate_and_distinguish_periods():
    weekly = reviews.build_visual_prompt("week", "A compass becoming balanced.")
    monthly = reviews.build_visual_prompt("month", "Four weekly rhythms converging.")

    assert weekly.startswith(STYLE_PROMPT + "\n\nDynamic visual brief:\n")
    assert monthly.startswith(STYLE_PROMPT + "\n\nDynamic visual brief:\n")
    assert "one central symbol" in weekly.lower()
    assert "layered synthesis of weekly motifs" in monthly.lower()
    assert weekly.endswith("A compass becoming balanced.")
    assert monthly.endswith("Four weekly rhythms converging.")


class _Response:
    def __init__(self, *, image: bytes | None = None, media_type: str = "image/jpeg"):
        self.image = image
        self.media_type = media_type

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self.image is None:
            return {"data": []}
        return {
            "data": [
                {
                    "b64_json": base64.b64encode(self.image).decode("ascii"),
                    "media_type": self.media_type,
                }
            ]
        }


class _FailureResponse:
    def __init__(self, message: str):
        self.message = message

    def raise_for_status(self) -> None:
        raise RuntimeError("http failure")

    def json(self):
        return {"error": {"message": self.message}}


class _HTTP:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_AC_3_3_image_generation_retries_primary_then_falls_back_atomically(tmp_path):
    image = b"\xff\xd8generated-jpeg\xff\xd9"
    http = _HTTP([RuntimeError("primary-1"), RuntimeError("primary-2"), _Response(image=image)])
    generator = reviews.OpenRouterImageGenerator(
        api_key="secret",
        http_client=http,
        output_dir=tmp_path,
        primary_model="openai/gpt-image-2",
        fallback_model="recraft/recraft-v4.1-pro",
        primary_attempts=2,
    )

    path = asyncio.run(
        generator.generate(
            kind="week",
            period="2026-07-26",
            visual_brief="A single gyroscope.",
        )
    )

    assert [call["json"]["model"] for call in http.calls] == [
        "openai/gpt-image-2",
        "openai/gpt-image-2",
        "recraft/recraft-v4.1-pro",
    ]
    assert all(call["url"] == "https://openrouter.ai/api/v1/images" for call in http.calls)
    assert all(call["json"]["aspect_ratio"] == "1:1" for call in http.calls)
    assert all(call["json"]["output_format"] == "jpeg" for call in http.calls)
    expected_prompt = reviews.build_visual_prompt("week", "A single gyroscope.")
    assert [call["json"]["prompt"] for call in http.calls] == [expected_prompt] * 3
    assert path == tmp_path / "week-2026-07-26.jpg"
    assert path.read_bytes() == image
    assert not list(tmp_path.glob("*.tmp"))


def test_AC_N2_safety_failure_reuses_exact_brief_and_final_failure_is_null(tmp_path):
    original_brief = "A named person holding a dangerous object."
    http = _HTTP(
        [
            _FailureResponse("safety policy refusal"),
            RuntimeError("primary failed"),
            RuntimeError("fallback failed"),
        ]
    )
    generator = reviews.OpenRouterImageGenerator(
        api_key="secret",
        http_client=http,
        output_dir=tmp_path,
        primary_model="test/primary-image",
        fallback_model="test/fallback-image",
        primary_attempts=2,
    )

    result = asyncio.run(
        generator.generate(
            kind="month",
            period="2026-07",
            visual_brief=original_brief,
        )
    )

    assert result is None
    assert len(http.calls) == 3
    prompts = [call["json"]["prompt"] for call in http.calls]
    expected_prompt = reviews.build_visual_prompt("month", original_brief)
    assert prompts == [expected_prompt] * 3
    assert all(original_brief in prompt for prompt in prompts)
    assert not hasattr(image_module, "ImageSafetyRefusal")
    assert not hasattr(image_module, "_looks_like_safety_refusal")
    assert list(tmp_path.iterdir()) == []

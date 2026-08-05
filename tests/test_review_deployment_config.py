from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dairy_bot.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "BOT_TOKEN": "123:test",
        "ALLOWED_USER_ID": 42,
        "OPENROUTER_API_KEY": "sk-test",
        "JOURNAL_DIR": tmp_path,
        "REVIEWS_ENABLED": True,
        "WEB_PUBLIC_BASE_URL": "https://diary.example.org",
        "REVIEW_IMAGE_MODEL_NAME": "test/primary-image",
        "REVIEW_IMAGE_FALLBACK_MODEL_NAME": "test/fallback-image",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_AC_4_config_accepts_public_https_without_edit_token(tmp_path):
    settings = _settings(tmp_path)

    assert settings.reviews_enabled is True
    assert settings.web_public_base_url == "https://diary.example.org"
    assert settings.edit_api_token is None


@pytest.mark.parametrize(
    "url",
    [
        "journal.example.org/reviews",
        "http://diary.example.org",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://journal.example.com",
    ],
)
def test_ERR_4_config_rejects_non_public_review_urls(tmp_path, url):
    with pytest.raises(ValidationError, match="WEB_PUBLIC_BASE_URL"):
        _settings(tmp_path, WEB_PUBLIC_BASE_URL=url)


def test_EC_4_disabled_reviews_keep_local_development_default(tmp_path):
    settings = _settings(
        tmp_path,
        REVIEWS_ENABLED=False,
        WEB_PUBLIC_BASE_URL="http://127.0.0.1:18080",
    )

    assert settings.reviews_enabled is False


def test_image_models_are_required_configuration_without_code_or_compose_defaults(
    tmp_path,
):
    with pytest.raises(
        ValidationError,
        match="REVIEW_IMAGE_MODEL_NAME|REVIEW_IMAGE_FALLBACK_MODEL_NAME",
    ):
        Settings(
            _env_file=None,
            BOT_TOKEN="123:test",
            ALLOWED_USER_ID=42,
            OPENROUTER_API_KEY="sk-test",
            JOURNAL_DIR=tmp_path,
        )

    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert (
        "REVIEW_IMAGE_MODEL_NAME: "
        "${REVIEW_IMAGE_MODEL_NAME:?Set REVIEW_IMAGE_MODEL_NAME in .env}"
    ) in compose
    assert (
        "REVIEW_IMAGE_FALLBACK_MODEL_NAME: "
        "${REVIEW_IMAGE_FALLBACK_MODEL_NAME:"
        "?Set REVIEW_IMAGE_FALLBACK_MODEL_NAME in .env}"
    ) in compose
    assert "openai/gpt-image-2" not in compose
    assert "recraft/recraft-v4.1-pro" not in compose

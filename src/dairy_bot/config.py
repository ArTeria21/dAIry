import logging
from datetime import time
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TZ_NAME = "Europe/Vienna"
DEFAULT_TZ = ZoneInfo(DEFAULT_TZ_NAME)
GenerativeLanguage = Literal["RU", "EN"]
LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "EN": "English",
    "RU": "Russian",
}

logger = logging.getLogger(__name__)


def language_display_name(language: str) -> str:
    return LANGUAGE_DISPLAY_NAMES.get(language, LANGUAGE_DISPLAY_NAMES["EN"])


class Settings(BaseSettings):
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    allowed_user_id: int = Field(..., alias="ALLOWED_USER_ID")
    openrouter_api_key: SecretStr = Field(..., alias="OPENROUTER_API_KEY")
    voice_model_name: str = Field(
        default="mistralai/voxtral-small-24b-2507",
        alias="VOICE_MODEL_NAME",
        validation_alias=AliasChoices("VOICE_MODEL_NAME"),
    )
    journal_dir: Path = Field(
        ...,
        alias="JOURNAL_DIR",
        validation_alias=AliasChoices("JOURNAL_DIR", "JOURNAL_PATH"),
    )
    git_enabled: bool = Field(default=True, alias="GIT_ENABLED")
    timezone: ZoneInfo = Field(
        default=DEFAULT_TZ,
        alias="TIMEZONE",
        validation_alias=AliasChoices("TIMEZONE", "PREFERRED_TIMEZONE"),
    )
    language: GenerativeLanguage = Field(default="EN", alias="LANGUAGE")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    # Table of contents indexing
    toc_enabled: bool = Field(default=False, alias="TOC_ENABLED")
    toc_filename: str = Field(default="table_of_contents.md", alias="TOC_FILENAME")
    toc_extra_include_dirs: str = Field(default="", alias="TOC_EXTRA_INCLUDE_DIRS")
    toc_scan_interval_minutes: int = Field(default=10, alias="TOC_SCAN_INTERVAL_MINUTES")
    toc_model_name: str = Field(default="openai/gpt-4.1-mini", alias="TOC_MODEL_NAME")
    toc_max_tags: int = Field(default=5, alias="TOC_MAX_TAGS")
    # LLM enrichment
    enrichment_enabled: bool = Field(default=False, alias="ENRICHMENT_ENABLED")
    enrichment_model_name: str = Field(
        default="openai/gpt-4.1-mini", alias="ENRICHMENT_MODEL_NAME"
    )
    embedding_model_name: str = Field(
        default="openai/text-embedding-3-small", alias="EMBEDDING_MODEL_NAME"
    )
    enrichment_db_path: Path = Field(
        default=Path("data/enrichment.sqlite3"), alias="ENRICHMENT_DB_PATH"
    )
    embeddings_db_path: Path = Field(
        default=Path("data/embeddings.sqlite3"), alias="EMBEDDINGS_DB_PATH"
    )
    # Weekly and monthly diary reviews
    reviews_enabled: bool = Field(default=False, alias="REVIEWS_ENABLED")
    reviews_db_path: Path = Field(
        default=Path("data/reviews.sqlite3"), alias="REVIEWS_DB_PATH"
    )
    review_assets_dir: Path = Field(
        default=Path("data/review_images"), alias="REVIEW_ASSETS_DIR"
    )
    review_model_name: str = Field(
        default="openai/gpt-5.6-terra", alias="REVIEW_MODEL_NAME"
    )
    review_image_model_name: str = Field(
        ..., alias="REVIEW_IMAGE_MODEL_NAME"
    )
    review_image_fallback_model_name: str = Field(
        ...,
        alias="REVIEW_IMAGE_FALLBACK_MODEL_NAME",
    )
    review_max_search_calls: int = Field(
        default=6, ge=0, le=6, alias="REVIEW_MAX_SEARCH_CALLS"
    )
    parallel_api_key: SecretStr | None = Field(default=None, alias="PARALLEL_API_KEY")
    review_weekly_send_time: time = Field(
        default=time(9, 0), alias="REVIEW_WEEKLY_SEND_TIME"
    )
    review_monthly_send_time: time = Field(
        default=time(10, 0), alias="REVIEW_MONTHLY_SEND_TIME"
    )
    web_public_base_url: str = Field(
        default="http://127.0.0.1:18080", alias="WEB_PUBLIC_BASE_URL"
    )
    edit_api_token: SecretStr | None = Field(default=None, alias="EDIT_API_TOKEN")
    edit_api_host: str = Field(default="0.0.0.0", alias="EDIT_API_HOST")
    edit_api_port: int = Field(default=8081, alias="EDIT_API_PORT")

    @field_validator("timezone", mode="before")
    @classmethod
    def _parse_timezone(cls, value: object) -> ZoneInfo:
        if value is None or value == "":
            return DEFAULT_TZ
        if isinstance(value, ZoneInfo):
            return value
        try:
            return ZoneInfo(str(value))
        except ZoneInfoNotFoundError:
            logger.warning(
                "Invalid timezone '%s', falling back to %s", value, DEFAULT_TZ_NAME
            )
        except Exception:
            logger.warning(
                "Unexpected timezone value '%s', falling back to %s",
                value,
                DEFAULT_TZ_NAME,
            )
        return DEFAULT_TZ

    @model_validator(mode="after")
    def _validate_reviews_public_url(self) -> "Settings":
        if not self.reviews_enabled:
            return self
        parsed = urlsplit(self.web_public_base_url)
        hostname = parsed.hostname
        if parsed.scheme != "https" or not hostname:
            raise ValueError(
                "WEB_PUBLIC_BASE_URL must be an absolute public HTTPS URL "
                "when REVIEWS_ENABLED=true"
            )
        normalized_host = hostname.rstrip(".").lower()
        if (
            normalized_host == "localhost"
            or normalized_host.endswith(".localhost")
            or normalized_host == "example.com"
            or normalized_host.endswith(".example.com")
        ):
            raise ValueError(
                "WEB_PUBLIC_BASE_URL must use a public non-placeholder host "
                "when REVIEWS_ENABLED=true"
            )
        try:
            address = ip_address(normalized_host)
        except ValueError:
            return self
        if not address.is_global:
            raise ValueError(
                "WEB_PUBLIC_BASE_URL must not use a loopback or private address "
                "when REVIEWS_ENABLED=true"
            )
        return self

    @property
    def toc_model(self) -> str:
        return self.toc_model_name

    @property
    def toc_extra_dirs(self) -> list[str]:
        if not self.toc_extra_include_dirs:
            return []
        return [d.strip() for d in self.toc_extra_include_dirs.split(",") if d.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

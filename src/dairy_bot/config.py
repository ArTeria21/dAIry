import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TZ_NAME = "Europe/Vienna"
DEFAULT_TZ = ZoneInfo(DEFAULT_TZ_NAME)

logger = logging.getLogger(__name__)


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

    @property
    def toc_model(self) -> str:
        return self.toc_model_name

    @property
    def toc_extra_dirs(self) -> list[str]:
        if not self.toc_extra_include_dirs:
            return []
        return [d.strip() for d in self.toc_extra_include_dirs.split(",") if d.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

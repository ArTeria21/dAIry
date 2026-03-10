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
    question_model_name: str = Field(
        default="openai/gpt-4.1-mini",
        alias="QUESTION_MODEL_NAME",
        validation_alias=AliasChoices("QUESTION_MODEL_NAME"),
    )
    question_language: str = Field(
        default="ru",
        alias="QUESTION_LANGUAGE",
        validation_alias=AliasChoices("QUESTION_LANGUAGE"),
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
    deep_question_start_hour: int = Field(default=11, alias="DEEP_QUESTION_START_HOUR")
    deep_question_end_hour: int = Field(default=20, alias="DEEP_QUESTION_END_HOUR")
    # Google Sheets integration
    google_sheets_enabled: bool = Field(default=False, alias="GOOGLE_SHEETS_ENABLED")
    google_sheets_id: str | None = Field(default=None, alias="GOOGLE_SHEETS_ID")
    google_creds_file: Path | None = Field(default=None, alias="GOOGLE_CREDS_FILE")

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

    @field_validator("deep_question_start_hour", "deep_question_end_hour")
    @classmethod
    def _validate_hour(cls, value: int) -> int:
        if value < 0 or value > 23:
            raise ValueError("Hour must be between 0 and 23")
        return value

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

VIENNA_TZ = ZoneInfo("Europe/Vienna")


class Settings(BaseSettings):
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    allowed_user_id: int = Field(..., alias="ALLOWED_USER_ID")
    openrouter_api_key: SecretStr = Field(..., alias="OPENROUTER_API_KEY")
    voxtrail_model_name: str = Field(
        default="mistralai/voxtral-small-24b-2507", alias="VOXTRAIL_MODEL_NAME"
    )
    journal_path: Path = Field(..., alias="JOURNAL_PATH")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

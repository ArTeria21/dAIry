from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dairy_web.auth import AuthSettings


@dataclass(frozen=True, slots=True)
class WebSettings:
    enrichment_db_path: Path
    vault_dir: Path
    analysis_cache_path: Path
    reviews_db_path: Path
    review_assets_dir: Path
    web_username: str
    web_password_argon2: str
    web_session_secret: str
    openrouter_api_key: str
    embeddings_db_path: Path = Path("/bot-data/embeddings.sqlite3")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    enrichment_model_name: str = "openai/gpt-4.1-mini"
    language: str = "EN"
    cookie_secure: bool = True
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 60
    bot_edit_api_url: str | None = None
    edit_api_token: str | None = None

    @classmethod
    def from_env(cls) -> WebSettings:
        return cls(
            enrichment_db_path=Path(
                os.getenv("ENRICHMENT_DB_PATH", "/app/data/enrichment.sqlite3")
            ),
            embeddings_db_path=Path(
                os.getenv("EMBEDDINGS_DB_PATH", "/bot-data/embeddings.sqlite3")
            ),
            vault_dir=Path(os.getenv("VAULT_DIR", "/vault")),
            analysis_cache_path=Path(
                os.getenv("ANALYSIS_CACHE_PATH", "/app/cache/analysis_cache.sqlite3")
            ),
            reviews_db_path=Path(
                os.getenv("REVIEWS_DB_PATH", "/bot-data/reviews.sqlite3")
            ),
            review_assets_dir=Path(
                os.getenv("REVIEW_ASSETS_DIR", "/bot-data/review_images")
            ),
            web_username=os.environ["WEB_USERNAME"],
            web_password_argon2=os.environ["WEB_PASSWORD_ARGON2"],
            web_session_secret=os.environ["WEB_SESSION_SECRET"],
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            openrouter_base_url=os.getenv(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            enrichment_model_name=os.getenv(
                "ENRICHMENT_MODEL_NAME", "openai/gpt-4.1-mini"
            ),
            language=os.getenv("LANGUAGE", "EN").upper(),
            cookie_secure=_env_bool("WEB_COOKIE_SECURE", default=True),
            login_rate_limit_attempts=int(
                os.getenv("WEB_LOGIN_RATE_LIMIT_ATTEMPTS", "5")
            ),
            login_rate_limit_window_seconds=int(
                os.getenv("WEB_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            bot_edit_api_url=os.getenv("BOT_EDIT_API_URL") or None,
            edit_api_token=os.getenv("EDIT_API_TOKEN") or None,
        )

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(
            username=self.web_username,
            password_argon2=self.web_password_argon2,
            session_secret=self.web_session_secret,
            rate_limit_attempts=self.login_rate_limit_attempts,
            rate_limit_window_seconds=self.login_rate_limit_window_seconds,
        )


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}

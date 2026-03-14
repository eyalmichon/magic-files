from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Secrets and static config — loaded from .env / environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
    )

    telegram_bot_token: str
    gemini_api_key: str
    google_client_id: str
    google_client_secret: str
    admin_telegram_id: int | None = None
    gemini_model: str = "gemini-2.5-flash"
    max_file_size_mb: int = 10
    conversation_timeout_sec: int = 600


@cache
def get_settings() -> Settings:
    return Settings()

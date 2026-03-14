"""Shared OAuth configuration for Google Drive access."""
from __future__ import annotations

from bot.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def get_client_config() -> dict:
    """Build the OAuth client config from settings."""
    s = get_settings()
    return {
        "installed": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

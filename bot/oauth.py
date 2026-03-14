"""Shared OAuth configuration for Google Drive access.

The client ID and secret are for a "Desktop app" OAuth client.
These are safe to embed in code — Google documents that the client secret
cannot be kept secret for installed apps.  The security boundary is the
user's explicit consent + the resulting token, not the client credentials.
"""
from __future__ import annotations

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

CLIENT_CONFIG = {
    "installed": {
        "client_id": "GOOGLE_CLIENT_ID_PLACEHOLDER",
        "client_secret": "GOOGLE_CLIENT_SECRET_PLACEHOLDER",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

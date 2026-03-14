"""Headless Google Drive OAuth — prints a URL, user pastes back the code."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def main() -> None:
    creds_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("credentials.json")
    token_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("token.json")

    if not creds_path.exists():
        print(f"Error: {creds_path} not found.", file=sys.stderr)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)

    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent")

    print()
    print("Open this URL in any browser and authorize:")
    print()
    print(f"  {auth_url}")
    print()
    code = input("Paste the authorization code here: ").strip()

    flow.fetch_token(code=code)
    token_path.write_text(flow.credentials.to_json())
    print(f"\nSaved to {token_path}")


if __name__ == "__main__":
    main()

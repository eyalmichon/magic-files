"""Google Drive OAuth — authorize and save a refresh token.

Two modes:
  1. Auto: starts a local server to catch the redirect (like gcloud).
     Works when the browser is on the same machine or with SSH port forwarding.
  2. Manual: user pastes the redirect URL from their browser address bar.
     Works from any terminal (Proxmox, SSH, etc.).
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import InstalledAppFlow

from bot.oauth import SCOPES, get_client_config

AUTH_PORT = 8080


def _auto_flow(flow: InstalledAppFlow) -> None:
    """Local server catches the redirect automatically."""
    print(f"\n  Listening on port {AUTH_PORT} for the OAuth callback...\n")
    flow.run_local_server(
        host="localhost",
        bind_addr="0.0.0.0",
        port=AUTH_PORT,
        open_browser=False,
        access_type="offline",
        prompt="consent",
    )


def _manual_flow(flow: InstalledAppFlow) -> None:
    """User pastes the redirect URL from their browser."""
    flow.redirect_uri = "http://localhost:1"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    print()
    print("  1. Open this URL in your browser:")
    print(f"     {auth_url}")
    print()
    print("  2. Sign in and click Allow.")
    print("  3. The browser will redirect to a page that won't load — that's fine.")
    print("  4. Copy the FULL URL from your browser's address bar.")
    print()

    try:
        response_url = input("  Paste the redirect URL here: ").strip()
    except EOFError:
        print("\n  Error: no interactive terminal. Re-run with: docker run --rm -it ...", file=sys.stderr)
        sys.exit(1)

    code = parse_qs(urlparse(response_url).query).get("code", [None])[0]
    if not code:
        print("  Error: could not find authorization code in the URL.", file=sys.stderr)
        sys.exit(1)

    flow.fetch_token(code=code)


def main() -> None:
    token_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("token.json")

    print()
    print("  Google Drive Authorization")
    print("  ─────────────────────────")
    print("  Choose how to authorize:")
    print()
    print("    1) Auto   — browser on this machine (or SSH port forwarding)")
    print("    2) Manual — remote terminal (Proxmox, SSH without forwarding)")
    print()

    choice = input("  Enter 1 or 2 [2]: ").strip() or "2"

    flow = InstalledAppFlow.from_client_config(get_client_config(), SCOPES)

    if choice == "1":
        _auto_flow(flow)
    else:
        _manual_flow(flow)

    token_path.write_text(flow.credentials.to_json())
    print(f"\n  Saved to {token_path}")


if __name__ == "__main__":
    main()

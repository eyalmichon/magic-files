#!/usr/bin/env bash
# post-install hook for homelab CLI
# Runs after .env creation and before docker compose up.
# Ensures state.json and token.json exist, then runs the
# Google Drive OAuth flow if the user hasn't authorized yet.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

SERVICE_NAME="magic-files"

GN="\033[1;32m"  YW="\033[33m"  BL="\033[36m"  RD="\033[01;31m"  CL="\033[m"
msg()  { echo -e " ${GN}✓${CL} $1"; }
info() { echo -e " ${YW}→${CL} $1"; }
err()  { echo -e " ${RD}✗ $1${CL}" >&2; exit 1; }

# ── Ensure data files exist ──────────────────────────────────────────────────
STATE_PATH="./state.json"
TOKEN_PATH="./token.json"

[[ -d "$STATE_PATH" ]] && rm -rf "$STATE_PATH"
[[ -s "$STATE_PATH" ]] || echo '{}' > "$STATE_PATH"
chmod 666 "$STATE_PATH"

[[ -d "$TOKEN_PATH" ]] && rm -rf "$TOKEN_PATH"
touch "$TOKEN_PATH"
chmod 666 "$TOKEN_PATH"

# ── Google Drive authorization ───────────────────────────────────────────────
if [[ -s "$TOKEN_PATH" ]]; then
  msg "Google Drive already authorized"
else
  echo ""
  echo " The bot needs access to your Google Drive."
  echo " A browser window will open (or a URL will be printed)."
  echo " Sign in with Google and click Allow."
  echo ""

  info "Building image..."
  docker build -t "${SERVICE_NAME}" . -q

  info "Starting authorization flow..."
  docker run --rm -it \
    -p 8080:8080 \
    -v "$(pwd)/.env:/app/.env:ro" \
    -v "$(pwd)/token.json:/app/token.json" \
    "${SERVICE_NAME}" \
    uv run python -m scripts.auth_drive /app/token.json

  [[ -s "$TOKEN_PATH" ]] && msg "Drive authorized" || err "Authorization failed"
fi

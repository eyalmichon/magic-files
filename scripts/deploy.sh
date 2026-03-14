#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# MagicFiles Bot — deploy to Docker host
#
# Run from the Docker host LXC console:
#   bash -c "$(wget -qLO - https://raw.githubusercontent.com/eyalmichon/magic-files/main/scripts/deploy.sh)"
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="https://github.com/eyalmichon/magic-files.git"
SERVICE_NAME="magic-files"
SERVICES_DIR="/opt/services"
DEPLOY_DIR="${SERVICES_DIR}/${SERVICE_NAME}"

GN="\033[1;32m"  YW="\033[33m"  BL="\033[36m"  RD="\033[01;31m"  CL="\033[m"
header()  { echo -e "\n${BL}──── $1 ────${CL}"; }
msg()     { echo -e " ${GN}✓${CL} $1"; }
info()    { echo -e " ${YW}→${CL} $1"; }
err()     { echo -e " ${RD}✗ $1${CL}" >&2; exit 1; }

command -v docker &>/dev/null || err "Docker not found. Run create-docker-host.sh on the Proxmox host first."
command -v git &>/dev/null    || err "git not found. Install with: apt install git"

header "MagicFiles Bot — Deploy"

# ── Clone or update repo ────────────────────────────────────────────────────
if [[ -d "${DEPLOY_DIR}/.git" ]]; then
  info "Updating existing installation..."
  cd "$DEPLOY_DIR"
  git pull --ff-only
  msg "Code updated"
else
  info "Cloning repository..."
  mkdir -p "$SERVICES_DIR"
  git clone "$REPO" "$DEPLOY_DIR"
  msg "Repository cloned"
fi

cd "$DEPLOY_DIR"

# ── Config ───────────────────────────────────────────────────────────────────
ENV_FILE="${DEPLOY_DIR}/.env"

if [[ -f "$ENV_FILE" ]]; then
  msg ".env already exists (not overwriting)"
else
  header "Configuration"
  echo ""

  read -rp " Telegram bot token (from @BotFather): " TG_TOKEN
  [[ -z "$TG_TOKEN" ]] && err "Telegram token is required."

  read -rp " Gemini API key (from aistudio.google.com): " GEMINI_KEY
  [[ -z "$GEMINI_KEY" ]] && err "Gemini API key is required."

  read -rp " Your Telegram user ID (send /start to @userinfobot): " ADMIN_ID
  [[ -z "$ADMIN_ID" ]] && err "Admin Telegram ID is required."

  echo ""
  echo " Google Drive OAuth credentials"
  echo " Go to: https://console.cloud.google.com/apis/credentials"
  echo " Create an OAuth client ID → Desktop app, then copy the values below."
  echo ""

  read -rp " Google OAuth Client ID: " G_CLIENT_ID
  [[ -z "$G_CLIENT_ID" ]] && err "Google Client ID is required."

  read -rp " Google OAuth Client Secret: " G_CLIENT_SECRET
  [[ -z "$G_CLIENT_SECRET" ]] && err "Google Client Secret is required."

  cat > "$ENV_FILE" << EOF
TELEGRAM_BOT_TOKEN="${TG_TOKEN}"
GEMINI_API_KEY="${GEMINI_KEY}"
ADMIN_TELEGRAM_ID="${ADMIN_ID}"
GOOGLE_CLIENT_ID="${G_CLIENT_ID}"
GOOGLE_CLIENT_SECRET="${G_CLIENT_SECRET}"
EOF
  msg ".env created"
fi

# ── Google Drive authorization ────────────────────────────────────────────────
TOKEN_PATH="${DEPLOY_DIR}/token.json"

if [[ -s "$TOKEN_PATH" ]]; then
  msg "Google Drive already authorized"
else
  header "Google Drive Authorization"
  echo ""
  echo " The bot needs access to your Google Drive."
  echo " A browser window will open (or a URL will be printed)."
  echo " Sign in with Google and click Allow."
  echo ""

  info "Building image..."
  cd "$DEPLOY_DIR"
  docker build -t "${SERVICE_NAME}" . -q

  info "Starting authorization flow..."
  touch "$TOKEN_PATH"
  chmod 666 "$TOKEN_PATH"
  docker run --rm -it \
    -p 8080:8080 \
    -v "${ENV_FILE}:/app/.env:ro" \
    -v "${TOKEN_PATH}:/app/token.json" \
    "${SERVICE_NAME}" \
    uv run python -m scripts.auth_drive /app/token.json

  [[ -s "$TOKEN_PATH" ]] && msg "Drive authorized" || err "Authorization failed"
fi

# ── Generate docker-compose.yml ──────────────────────────────────────────────
COMPOSE_FILE="${SERVICES_DIR}/docker-compose.yml"

header "Registering service"

STATE_PATH="${DEPLOY_DIR}/state.json"
[[ -d "$STATE_PATH" ]] && rm -rf "$STATE_PATH"
[[ -s "$STATE_PATH" ]] || echo '{}' > "$STATE_PATH"
chmod 666 "$STATE_PATH"

VOLUMES="      - ${DEPLOY_DIR}/.env:/app/.env:ro"
[[ -s "${DEPLOY_DIR}/token.json" ]] && VOLUMES="${VOLUMES}
      - ${DEPLOY_DIR}/token.json:/app/token.json:ro"
VOLUMES="${VOLUMES}
      - ${STATE_PATH}:/app/state.json"

cat > "$COMPOSE_FILE" << EOF
services:
  ${SERVICE_NAME}:
    build: ${DEPLOY_DIR}
    container_name: ${SERVICE_NAME}
    restart: unless-stopped
    volumes:
${VOLUMES}
EOF
msg "Written ${COMPOSE_FILE}"

# ── Build and start ──────────────────────────────────────────────────────────
header "Building and starting"
cd "$SERVICES_DIR"
docker compose up -d --build "$SERVICE_NAME"
msg "Container is running"

# ── Done ─────────────────────────────────────────────────────────────────────
CT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
header "Deployed!"
echo ""
echo -e " ${GN}Service:${CL}  ${SERVICE_NAME}"
echo -e " ${GN}Status:${CL}   docker compose -f ${COMPOSE_FILE} ps"
echo -e " ${GN}Logs:${CL}     docker compose -f ${COMPOSE_FILE} logs -f ${SERVICE_NAME}"
echo -e " ${GN}Restart:${CL}  docker compose -f ${COMPOSE_FILE} restart ${SERVICE_NAME}"
echo ""
echo -e " ${YW}To update later, run this same one-liner again.${CL}"
echo ""

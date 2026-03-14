#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# MagicFiles Bot — deploy to Docker host
#
# Run from the Docker host LXC console:
#   bash -c "$(wget -qLO - https://raw.githubusercontent.com/eyalmichon/drive-bot/main/scripts/deploy.sh)"
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="https://github.com/eyalmichon/drive-bot.git"
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

  cat > "$ENV_FILE" << EOF
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
GEMINI_API_KEY=${GEMINI_KEY}
ADMIN_TELEGRAM_ID=${ADMIN_ID}
EOF
  msg ".env created"
fi

# ── Google Drive authorization ────────────────────────────────────────────────
TOKEN_PATH="${DEPLOY_DIR}/token.json"

if [[ -f "$TOKEN_PATH" ]]; then
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
  docker run --rm -it \
    -p 8080:8080 \
    -v "${TOKEN_PATH}:/app/token.json" \
    "${SERVICE_NAME}" \
    uv run python -m scripts.auth_drive /app/token.json

  [[ -s "$TOKEN_PATH" ]] && msg "Drive authorized" || err "Authorization failed"
fi

# ── Register in docker-compose.yml ───────────────────────────────────────────
COMPOSE_FILE="${SERVICES_DIR}/docker-compose.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  cat > "$COMPOSE_FILE" << 'YAML'
services: {}
YAML
fi

header "Registering service"
python3 - "$COMPOSE_FILE" "$SERVICE_NAME" "$DEPLOY_DIR" << 'PYEOF'
import sys, subprocess
from pathlib import Path

compose_path, service_name, build_dir = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    import yaml
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyyaml"])
    import yaml

compose_file = Path(compose_path)
data = {}
if compose_file.exists():
    with open(compose_file) as f:
        data = yaml.safe_load(f) or {}

if "services" not in data or data["services"] is None:
    data["services"] = {}

volumes = [f"{build_dir}/.env:/app/.env:ro"]
token = Path(build_dir) / "token.json"
state = Path(build_dir) / "state.json"
secrets = Path(build_dir) / "secrets"
if token.exists():
    volumes.append(f"{build_dir}/token.json:/app/token.json:ro")
if state.exists():
    volumes.append(f"{build_dir}/state.json:/app/state.json")
if secrets.exists():
    volumes.append(f"{build_dir}/secrets:/app/secrets:ro")

svc = {
    "build": build_dir,
    "container_name": service_name,
    "restart": "unless-stopped",
    "volumes": volumes,
}
if secrets.exists():
    svc["environment"] = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/app/secrets/adc.json",
    }

data["services"][service_name] = svc

with open(compose_file, "w") as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
PYEOF
msg "Added to ${COMPOSE_FILE}"

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

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
if [[ -f "${DEPLOY_DIR}/config.yaml" ]]; then
  msg "config.yaml already exists (not overwriting)"
else
  header "Configuration"
  echo " The bot only needs 2 keys. Everything else is configured via Telegram."
  echo ""

  read -rp " Telegram bot token (from @BotFather): " TG_TOKEN
  [[ -z "$TG_TOKEN" ]] && err "Telegram token is required."

  read -rp " Gemini API key (from aistudio.google.com): " GEMINI_KEY
  [[ -z "$GEMINI_KEY" ]] && err "Gemini API key is required."

  read -rp " Gemini model [gemini-2.5-flash]: " GEMINI_MODEL
  GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"

  cat > "${DEPLOY_DIR}/config.yaml" << YAML
telegram_bot_token: "${TG_TOKEN}"
gemini_api_key: "${GEMINI_KEY}"
gemini_model: "${GEMINI_MODEL}"
allowed_user_ids: []
YAML
  msg "config.yaml created"
  info "On first /start, the bot will register you as admin and walk you through setup"
fi

# ── Google credentials ───────────────────────────────────────────────────────
SECRETS_DIR="${DEPLOY_DIR}/secrets"
mkdir -p "$SECRETS_DIR"

TOKEN_PATH="${DEPLOY_DIR}/token.json"
OAUTH_CREDS="${DEPLOY_DIR}/credentials.json"

if [[ -f "$TOKEN_PATH" ]]; then
  msg "Google Drive already authorized"
elif [[ -f "${SECRETS_DIR}/adc.json" ]]; then
  msg "Google credentials already in place"
else
  header "Google Drive Authorization"
  echo ""
  echo " The bot needs access to your Google Drive."
  echo " You'll need an OAuth client ID from GCP Console."
  echo ""

  if [[ ! -f "$OAUTH_CREDS" ]]; then
    echo " If you don't have one yet:"
    echo "   1. Go to https://console.cloud.google.com/apis/credentials"
    echo "   2. Create Credentials → OAuth client ID → Desktop app"
    echo "   3. Download the JSON"
    echo ""
    echo " Paste the OAuth client JSON below (then press Enter + Ctrl+D):"
    echo ""

    CLIENT_JSON=$(cat)

    if [[ -n "$CLIENT_JSON" ]]; then
      echo "$CLIENT_JSON" > "$OAUTH_CREDS"
      msg "OAuth client saved"
    else
      err "OAuth client JSON is required for Drive access."
    fi
  fi

  info "Starting authorization flow..."
  cd "$DEPLOY_DIR"
  docker run --rm -it \
    -v "${OAUTH_CREDS}:/app/credentials.json:ro" \
    -v "${DEPLOY_DIR}:/app/output" \
    python:3.12-slim bash -c "
      pip install -q google-auth-oauthlib && \
      python3 -c \"
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path

flow = InstalledAppFlow.from_client_secrets_file('/app/credentials.json', [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
])
flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
auth_url, _ = flow.authorization_url(prompt='consent')

print()
print('Open this URL in any browser and authorize:')
print()
print(f'  {auth_url}')
print()
code = input('Paste the authorization code here: ').strip()
flow.fetch_token(code=code)
Path('/app/output/token.json').write_text(flow.credentials.to_json())
print('Authorized!')
\"
    "

  [[ -f "$TOKEN_PATH" ]] && msg "Drive authorized" || err "Authorization failed"

  rm -f "$OAUTH_CREDS"
  msg "OAuth client JSON cleaned up"
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

volumes = [f"{build_dir}/config.yaml:/app/config.yaml:ro"]
token = Path(build_dir) / "token.json"
secrets = Path(build_dir) / "secrets"
if token.exists():
    volumes.append(f"{build_dir}/token.json:/app/token.json:ro")
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

#!/usr/bin/env bash
set -euo pipefail

if [[ -t 1 ]]; then
  C_BOLD="$(printf '\033[1m')"
  C_BLUE="$(printf '\033[34m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_RED="$(printf '\033[31m')"
  C_RESET="$(printf '\033[0m')"
else
  C_BOLD=""
  C_BLUE=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
  C_RESET=""
fi

say() { printf "%b\n" "$*"; }
step() { say "${C_BLUE}${C_BOLD}==>${C_RESET} $*"; }
ok() { say "${C_GREEN}${C_BOLD}✔${C_RESET} $*"; }
warn() { say "${C_YELLOW}${C_BOLD}⚠${C_RESET} $*"; }
die() { say "${C_RED}${C_BOLD}ERROR:${C_RESET} $*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  die "Run as root: sudo bash install.sh"
fi

MODE="${1:-}"
if [[ -z "${MODE}" ]]; then
  say "${C_BOLD}I2PChat BlindBox daemon installer${C_RESET}"
  say
  say "Choose exactly one mode:"
  say "  public - replica token stays empty; best for a public I2P-only relay"
  say "  token  - generate a replica token automatically"
  say
  printf "BlindBox mode [public/token]: "
  read -r MODE
fi
MODE="$(printf '%s' "${MODE}" | tr '[:upper:]' '[:lower:]')"
case "${MODE}" in
  public|token) ;;
  *)
    die "Usage: install.sh [public|token]"
    ;;
esac

APP_USER="i2pchatbb"
APP_GROUP="${APP_USER}"
APP_ROOT="/opt/i2pchat-blindbox"
CONF_DIR="/etc/i2pchat-blindbox"
DATA_DIR="/var/lib/${APP_USER}"
STORE_DIR="${DATA_DIR}/.i2pchat-blindbox"
SERVICE_FILE="/etc/systemd/system/i2pchat-blindbox.service"
FAIL2BAN_FILTER="/etc/fail2ban/filter.d/i2pchat-blindbox.conf"
FAIL2BAN_JAIL="/etc/fail2ban/jail.d/i2pchat-blindbox.local"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPO_URL="${I2PCHAT_BLINDBOX_REPO_URL:-https://github.com/MetanoicArmor/I2PChat/archive/refs/heads/main.tar.gz}"
TMP_ROOT=""

cleanup() {
  if [[ -n "${TMP_ROOT}" && -d "${TMP_ROOT}" ]]; then
    rm -rf "${TMP_ROOT}"
  fi
}
trap cleanup EXIT

print_plan() {
  say
  say "${C_BOLD}Install plan${C_RESET}"
  say "  Mode:           ${MODE}"
  say "  Service user:   ${APP_USER}"
  say "  App root:       ${APP_ROOT}"
  say "  Config dir:     ${CONF_DIR}"
  say "  Data dir:       ${DATA_DIR}"
  say "  systemd unit:   ${SERVICE_FILE}"
  say
}

ensure_assets() {
  if [[ -f "${DAEMON_ROOT}/service.py" && -f "${DAEMON_ROOT}/env/daemon.env.example" ]]; then
    ok "Using bundled daemon assets from ${DAEMON_ROOT}"
    return
  fi
  step "Bundled daemon assets not found; downloading them from ${REPO_URL}"
  TMP_ROOT="$(mktemp -d)"
  apt-get update
  apt-get install -y ca-certificates curl python3 tar
  curl -fsSL "${REPO_URL}" -o "${TMP_ROOT}/repo.tar.gz"
  tar -xzf "${TMP_ROOT}/repo.tar.gz" -C "${TMP_ROOT}"
  local extracted
  extracted="$(find "${TMP_ROOT}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [[ -z "${extracted}" || ! -d "${extracted}/i2pchat/blindbox/daemon" ]]; then
    die "Failed to fetch daemon assets from ${REPO_URL}"
  fi
  DAEMON_ROOT="${extracted}/i2pchat/blindbox/daemon"
  ok "Downloaded daemon assets"
}

print_plan
ensure_assets

step "Installing required system packages"
apt-get update
apt-get install -y ca-certificates python3
ok "System packages are present"

step "Preparing dedicated service account"
if ! getent group "${APP_GROUP}" >/dev/null; then
  groupadd --system "${APP_GROUP}"
fi
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${APP_GROUP}" --home-dir "${DATA_DIR}" --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi
ok "Service account ready: ${APP_USER}"

step "Installing daemon files"
rm -rf "${APP_ROOT}"
mkdir -p "${APP_ROOT}"
cp -R "$(cd "${DAEMON_ROOT}/../../.." && pwd)/i2pchat" "${APP_ROOT}/"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_ROOT}"
ok "Daemon files installed to ${APP_ROOT}"

step "Preparing config and data directories"
install -d -m 0750 "${CONF_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0700 "${DATA_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0700 "${STORE_DIR}"
ok "Config and data directories ready"

step "Generating tokens"
ADMIN_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"

REPLICA_TOKEN=""
if [[ "${MODE}" == "token" ]]; then
  REPLICA_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"
fi
ok "Tokens generated"

step "Writing daemon.env"
cat > "${CONF_DIR}/daemon.env" <<ENV
BLINDBOX_AUTH_TOKEN=${REPLICA_TOKEN}
BLINDBOX_ADMIN_TOKEN=${ADMIN_TOKEN}
BLINDBOX_MAX_TOTAL_BYTES=536870912
BLINDBOX_MAX_FILES=4096
BLINDBOX_MAX_PREFIX_BYTES=33554432
BLINDBOX_MAX_PREFIX_FILES=256
BLINDBOX_TTL_SEC=1209600
BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE=240
BLINDBOX_RATE_LIMIT_BYTES_PER_MINUTE=67108864
BLINDBOX_AUDIT_LOG_MAX_BYTES=1048576
BLINDBOX_AUDIT_LOG_BACKUPS=3
BLINDBOX_HTTP_STATUS=1
BLINDBOX_HTTP_HOST=127.0.0.1
BLINDBOX_HTTP_PORT=19445
BLINDBOX_METRICS_JSON_PATH=${STORE_DIR}/metrics.json
BLINDBOX_METRICS_PROM_PATH=${STORE_DIR}/metrics.prom
ENV
chmod 600 "${CONF_DIR}/daemon.env"
ok "Config written to ${CONF_DIR}/daemon.env"

step "Writing systemd service"
cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=I2PChat BlindBox daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_ROOT}
EnvironmentFile=${CONF_DIR}/daemon.env
ExecStart=/usr/bin/python3 -m i2pchat.blindbox.daemon
Environment=PYTHONPATH=${APP_ROOT}
Restart=always
RestartSec=2
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${APP_ROOT} ${CONF_DIR} ${DATA_DIR}
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=yes
MemoryDenyWriteExecute=yes

[Install]
WantedBy=multi-user.target
SERVICE
ok "systemd unit written"

step "Installing fail2ban examples"
if [[ -f "${DAEMON_ROOT}/fail2ban/i2pchat-blindbox.conf" ]]; then
  install -D -m 0644 "${DAEMON_ROOT}/fail2ban/i2pchat-blindbox.conf" "${FAIL2BAN_FILTER}"
fi
if [[ -f "${DAEMON_ROOT}/fail2ban/jail.local.example" ]]; then
  install -D -m 0644 "${DAEMON_ROOT}/fail2ban/jail.local.example" "${FAIL2BAN_JAIL}"
fi
ok "fail2ban examples installed"

step "Enabling and starting the daemon"
systemctl daemon-reload
systemctl enable --now i2pchat-blindbox.service
ok "Daemon is enabled and started"

echo
say "${C_GREEN}${C_BOLD}=== I2PChat BlindBox daemon installed ===${C_RESET}"
say "Mode: ${MODE}"
say "Service: i2pchat-blindbox.service"
say "Config: ${CONF_DIR}/daemon.env"
say "App root: ${APP_ROOT}"
say "Admin token: ${ADMIN_TOKEN}"
if [[ -n "${REPLICA_TOKEN}" ]]; then
  say "Replica token: ${REPLICA_TOKEN}"
else
  say "Replica token: <empty> (public mode)"
fi
say
say "${C_BOLD}Health checks${C_RESET}"
say "  curl -H \"Authorization: Bearer ${ADMIN_TOKEN}\" http://127.0.0.1:19445/healthz"
say "  curl -H \"Authorization: Bearer ${ADMIN_TOKEN}\" http://127.0.0.1:19445/status.json"
say "  curl -H \"Authorization: Bearer ${ADMIN_TOKEN}\" http://127.0.0.1:19445/metrics"
say
say "${C_BOLD}Next server step${C_RESET}"
say "  Add the i2pd server tunnel from the GUI 'I2pd' tab and restart i2pd."

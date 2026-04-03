#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install the I2PChat BlindBox replica as a systemd service.

Usage:
  sudo ./scripts/install_blindbox_replica.sh [options]

Options:
  --user USER            Linux user that will run the service. Default: blindbox
  --group GROUP          Linux group for the service. Default: same as --user
  --service NAME         systemd service name. Default: blindbox
  --install-dir DIR      Installation directory. Default: /opt/i2pchat-blindbox
  --base-dir DIR         BlindBox storage base. Default: /var/lib/blindbox/.i2pchat-blindbox
  --bind-host HOST       Local bind host. Default: 127.0.0.1
  --port PORT            Local listen port for the replica. Default: 19444
  --max-blob BYTES       Max blob size in bytes. Default: 1048576
  --ttl-sec SECONDS      Blob retention TTL. Default: 1209600 (14 days)
  --python PATH          Python interpreter. Default: /usr/bin/python3
  --token TOKEN          Optional BLINDBOX_AUTH_TOKEN. Default: public / no token
  --public               Force public mode without BLINDBOX_AUTH_TOKEN
  --write-i2pd-conf PATH Write an i2pd server-tunnel snippet to this path
  --no-start             Install files but do not enable/start the service
  -h, --help             Show this help

The script:
  1. creates the service user if needed;
  2. installs the queue-only BlindBox replica Python server;
  3. writes the replica .env with storage/bind/ttl settings (and optional token);
  4. writes /etc/systemd/system/<service>.service;
  5. optionally writes an i2pd tunnel snippet.
EOF
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "This installer must run as root (use sudo)." >&2
    exit 1
  fi
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

generate_token() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_SERVER="${REPO_ROOT}/i2pchat/blindbox/blindbox_server_example.py"

SERVICE_USER="blindbox"
SERVICE_GROUP=""
SERVICE_NAME="blindbox"
INSTALL_DIR="/opt/i2pchat-blindbox"
BASE_DIR="/var/lib/blindbox/.i2pchat-blindbox"
BIND_HOST="127.0.0.1"
BLINDBOX_PORT="19444"
MAX_BLOB="1048576"
TTL_SEC="1209600"
PYTHON_BIN="/usr/bin/python3"
WRITE_I2PD_CONF=""
NO_START="0"
TOKEN_OVERRIDE=""
FORCE_PUBLIC="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      SERVICE_USER="$2"
      shift 2
      ;;
    --group)
      SERVICE_GROUP="$2"
      shift 2
      ;;
    --service)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --base-dir)
      BASE_DIR="$2"
      shift 2
      ;;
    --bind-host)
      BIND_HOST="$2"
      shift 2
      ;;
    --port)
      BLINDBOX_PORT="$2"
      shift 2
      ;;
    --max-blob)
      MAX_BLOB="$2"
      shift 2
      ;;
    --ttl-sec)
      TTL_SEC="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --token)
      TOKEN_OVERRIDE="$2"
      shift 2
      ;;
    --public)
      FORCE_PUBLIC="1"
      shift
      ;;
    --write-i2pd-conf)
      WRITE_I2PD_CONF="$2"
      shift 2
      ;;
    --no-start)
      NO_START="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_root
require_cmd install
require_cmd systemctl
require_cmd "$PYTHON_BIN"

if [[ ! -f "$SOURCE_SERVER" ]]; then
  echo "Cannot find source server script: $SOURCE_SERVER" >&2
  exit 1
fi

if [[ -z "$SERVICE_GROUP" ]]; then
  SERVICE_GROUP="$SERVICE_USER"
fi

if ! [[ "$BLINDBOX_PORT" =~ ^[0-9]+$ ]] || [[ "$BLINDBOX_PORT" -lt 1 ]] || [[ "$BLINDBOX_PORT" -gt 65535 ]]; then
  echo "Invalid port: $BLINDBOX_PORT" >&2
  exit 1
fi
if ! [[ "$MAX_BLOB" =~ ^[0-9]+$ ]] || [[ "$MAX_BLOB" -lt 1 ]]; then
  echo "Invalid max blob size: $MAX_BLOB" >&2
  exit 1
fi
if ! [[ "$TTL_SEC" =~ ^[0-9]+$ ]] || [[ "$TTL_SEC" -lt 1 ]]; then
  echo "Invalid TTL: $TTL_SEC" >&2
  exit 1
fi

if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
  groupadd --system "$SERVICE_GROUP"
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "$SERVICE_GROUP" \
    --home-dir "/var/lib/${SERVICE_NAME}" \
    --create-home \
    --shell /usr/sbin/nologin \
    "$SERVICE_USER"
fi

USER_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
if [[ -z "$USER_HOME" ]]; then
  echo "Failed to resolve home directory for user $SERVICE_USER" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
install -m 0755 "$SOURCE_SERVER" "${INSTALL_DIR}/blindbox_server.py"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$INSTALL_DIR"

DATA_DIR="${BASE_DIR}"
ENV_FILE="${DATA_DIR}/.env"
mkdir -p "$DATA_DIR"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR"
chmod 0700 "$DATA_DIR"

TOKEN_VALUE="$TOKEN_OVERRIDE"
if [[ "$FORCE_PUBLIC" != "1" && -z "$TOKEN_VALUE" && -f "$ENV_FILE" ]]; then
  TOKEN_VALUE="$(sed -n 's/^BLINDBOX_AUTH_TOKEN=//p' "$ENV_FILE" | head -n1)"
fi
if [[ "$FORCE_PUBLIC" == "1" ]]; then
  TOKEN_VALUE=""
fi

cat >"$ENV_FILE" <<EOF
BLINDBOX_BASE=${DATA_DIR}
BLINDBOX_BIND_HOST=${BIND_HOST}
BLINDBOX_PORT=${BLINDBOX_PORT}
BLINDBOX_MAX_BLOB=${MAX_BLOB}
BLINDBOX_TTL_SEC=${TTL_SEC}
EOF
if [[ -n "$TOKEN_VALUE" ]]; then
  cat >>"$ENV_FILE" <<EOF
BLINDBOX_AUTH_TOKEN=${TOKEN_VALUE}
EOF
fi
chown "${SERVICE_USER}:${SERVICE_GROUP}" "$ENV_FILE"
chmod 0600 "$ENV_FILE"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=I2PChat BlindBox replica
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=HOME=${USER_HOME}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/blindbox_server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

if [[ -n "$WRITE_I2PD_CONF" ]]; then
  mkdir -p "$(dirname "$WRITE_I2PD_CONF")"
  cat >"$WRITE_I2PD_CONF" <<EOF
[${SERVICE_NAME}]
type = server
host = 127.0.0.1
port = ${BLINDBOX_PORT}
keys = ${SERVICE_NAME}.dat
inport = ${BLINDBOX_PORT}
EOF
fi

systemctl daemon-reload
if [[ "$NO_START" != "1" ]]; then
  systemctl enable --now "${SERVICE_NAME}.service"
fi

cat <<EOF

BlindBox replica installed.

Service:
  systemctl status ${SERVICE_NAME}.service

Replica runtime:
  user        : ${SERVICE_USER}
  home        : ${USER_HOME}
  install dir : ${INSTALL_DIR}
  base dir    : ${DATA_DIR}
  bind host   : ${BIND_HOST}
  local port  : ${BLINDBOX_PORT}
  max blob    : ${MAX_BLOB}
  ttl sec     : ${TTL_SEC}
  env file    : ${ENV_FILE}
EOF
if [[ -n "$TOKEN_VALUE" ]]; then
cat <<EOF

BlindBox auth token:
  ${TOKEN_VALUE}

Add this token in I2PChat -> BlindBox diagnostics -> Replica auth for the matching endpoint.
EOF
else
cat <<EOF

BlindBox auth token:
  not configured (public replica mode)
EOF
fi

cat <<EOF

If you have not written an i2pd tunnel config yet, add something like this to tunnels.conf:

[${SERVICE_NAME}]
type = server
host = ${BIND_HOST}
port = ${BLINDBOX_PORT}
keys = ${SERVICE_NAME}.dat
inport = ${BLINDBOX_PORT}

Then restart i2pd and use the resulting *.b32.i2p:${BLINDBOX_PORT} endpoint in I2PChat.
EOF

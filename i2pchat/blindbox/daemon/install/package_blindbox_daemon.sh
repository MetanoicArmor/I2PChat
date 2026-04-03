#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DAEMON_ROOT}/../../../.." && pwd)"
VERSION_FILE="${REPO_ROOT}/VERSION"

if [[ ! -f "${VERSION_FILE}" ]]; then
  echo "VERSION file not found: ${VERSION_FILE}" >&2
  exit 1
fi

VERSION="$(tr -d '\r\n' < "${VERSION_FILE}")"
OUT_DIR="${REPO_ROOT}/dist/I2PChat-BlindBox-daemon-v${VERSION}"

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}/daemon" "${OUT_DIR}/systemd" "${OUT_DIR}/fail2ban" "${OUT_DIR}/install"

install -m 0644 "${REPO_ROOT}/i2pchat/blindbox/blindbox_server_example.py" "${OUT_DIR}/daemon/blindbox_server_example.py"
install -m 0644 "${DAEMON_ROOT}/service.py" "${OUT_DIR}/daemon/service.py"
install -m 0644 "${DAEMON_ROOT}/__main__.py" "${OUT_DIR}/daemon/__main__.py"
install -m 0644 "${DAEMON_ROOT}/env/daemon.env.example" "${OUT_DIR}/daemon.env.example"
install -m 0644 "${DAEMON_ROOT}/systemd/i2pchat-blindbox.service" "${OUT_DIR}/systemd/i2pchat-blindbox.service"
install -m 0644 "${DAEMON_ROOT}/fail2ban/i2pchat-blindbox.conf" "${OUT_DIR}/fail2ban/i2pchat-blindbox.conf"
install -m 0644 "${DAEMON_ROOT}/fail2ban/jail.local.example" "${OUT_DIR}/fail2ban/jail.local.example"
install -m 0755 "${SCRIPT_DIR}/install_blindbox_daemon.sh" "${OUT_DIR}/install/install_blindbox_daemon.sh"
install -m 0755 "${SCRIPT_DIR}/install.sh" "${OUT_DIR}/install/install.sh"

cat > "${OUT_DIR}/README.txt" <<EOF
I2PChat BlindBox daemon bundle v${VERSION}

Install from source package:
  python3 -m i2pchat.blindbox.daemon

Bundled assets:
  - systemd/i2pchat-blindbox.service
  - daemon.env.example
  - fail2ban/*
  - install/install.sh
  - install/install_blindbox_daemon.sh
EOF

echo "Created ${OUT_DIR}"

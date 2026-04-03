#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TARGET_USER="${SUDO_USER:-${USER}}"
TARGET_HOME="$(eval echo "~${TARGET_USER}")"
CONFIG_DIR="${TARGET_HOME}/.config/i2pchat-blindbox"
SYSTEMD_DIR="${TARGET_HOME}/.config/systemd/user"
FAIL2BAN_FILTER_DIR="${TARGET_HOME}/.config/fail2ban/filter.d"
FAIL2BAN_JAIL_DIR="${TARGET_HOME}/.config/fail2ban/jail.d"

mkdir -p "${CONFIG_DIR}" "${SYSTEMD_DIR}" "${FAIL2BAN_FILTER_DIR}" "${FAIL2BAN_JAIL_DIR}"

install -m 0644 "${DAEMON_ROOT}/env/daemon.env.example" "${CONFIG_DIR}/daemon.env"
install -m 0644 "${DAEMON_ROOT}/systemd/i2pchat-blindbox.service" "${SYSTEMD_DIR}/i2pchat-blindbox.service"
install -m 0644 "${DAEMON_ROOT}/fail2ban/i2pchat-blindbox.conf" "${FAIL2BAN_FILTER_DIR}/i2pchat-blindbox.conf"
install -m 0644 "${DAEMON_ROOT}/fail2ban/jail.local.example" "${FAIL2BAN_JAIL_DIR}/i2pchat-blindbox.local"

cat <<EOF
Installed BlindBox daemon assets for ${TARGET_USER}

Files:
  ${CONFIG_DIR}/daemon.env
  ${SYSTEMD_DIR}/i2pchat-blindbox.service
  ${FAIL2BAN_FILTER_DIR}/i2pchat-blindbox.conf
  ${FAIL2BAN_JAIL_DIR}/i2pchat-blindbox.local

Next steps:
  1. Edit ${CONFIG_DIR}/daemon.env
  2. systemctl --user daemon-reload
  3. systemctl --user enable --now i2pchat-blindbox.service
  4. Copy the fail2ban files into your system fail2ban config if needed
EOF

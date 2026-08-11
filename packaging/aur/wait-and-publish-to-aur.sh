#!/usr/bin/env bash
# Wait until AUR git SSH leaves maintenance, then run publish-to-aur.sh.
# Usage:
#   eval "$(ssh-agent -s)"
#   ssh-add ~/.ssh/aur_ed25519
#   ./packaging/aur/wait-and-publish-to-aur.sh
#
# Optional:
#   I2PCHAT_AUR_RETRY_SECS=120   # poll interval (default 90)
#   I2PCHAT_AUR_MAX_WAIT_SECS=0  # 0 = forever
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETRY_SECS="${I2PCHAT_AUR_RETRY_SECS:-90}"
MAX_WAIT="${I2PCHAT_AUR_MAX_WAIT_SECS:-0}"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o IdentitiesOnly=yes -i ${HOME}/.ssh/aur_ed25519}"

probe_aur_ssh() {
  # Capture stderr: maintenance vs Welcome vs auth errors.
  local out
  out="$(ssh -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes \
    -i "${HOME}/.ssh/aur_ed25519" -T aur@aur.archlinux.org 2>&1 || true)"
  printf '%s\n' "$out"
  if printf '%s' "$out" | grep -qi 'Welcome to the AUR'; then
    return 0
  fi
  if printf '%s' "$out" | grep -qi 'down due to maintenance'; then
    return 2
  fi
  if printf '%s' "$out" | grep -qi 'Permission denied'; then
    return 3
  fi
  return 1
}

echo "==> Waiting for AUR git SSH (interval ${RETRY_SECS}s)…"
echo "    (веб может работать, а git/SSH — в maintenance; это нормально сейчас)"
started="$(date +%s)"

while true; do
  set +e
  msg="$(probe_aur_ssh)"
  rc=$?
  set -e
  now="$(date +%s)"
  elapsed=$((now - started))
  ts="$(date '+%H:%M:%S')"

  case "$rc" in
    0)
      echo "[${ts}] AUR SSH OK — публикую"
      exec "${SCRIPT_DIR}/publish-to-aur.sh"
      ;;
    2)
      echo "[${ts}] ещё maintenance (${elapsed}s): $(printf '%s' "$msg" | head -1)"
      ;;
    3)
      echo "[${ts}] ERROR: Permission denied — ключ не в ssh-agent." >&2
      echo "  eval \"\$(ssh-agent -s)\"" >&2
      echo "  ssh-add ~/.ssh/aur_ed25519" >&2
      exit 1
      ;;
    *)
      echo "[${ts}] SSH probe rc=${rc}: $(printf '%s' "$msg" | tr '\n' ' ' | head -c 200)"
      ;;
  esac

  if [[ "${MAX_WAIT}" -gt 0 && "${elapsed}" -ge "${MAX_WAIT}" ]]; then
    echo "ERROR: timeout after ${elapsed}s — AUR всё ещё недоступен для push." >&2
    exit 1
  fi
  sleep "${RETRY_SECS}"
done

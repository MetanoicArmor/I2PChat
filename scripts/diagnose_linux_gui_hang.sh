#!/usr/bin/env bash
# Снятие стека при зависании GUI на Linux (план диагностики 1.1.0).
# Использование:
#   1) Запустить I2PChat, дождаться зависания.
#   2) В другом терминале: scripts/diagnose_linux_gui_hang.sh <PID>
# Или перед запуском приложения — проверки окружения из плана.

set -euo pipefail

diag_pid="${1:-}"

echo "=== I2PChat Linux hang — быстрые проверки окружения ==="
echo "PYTHON_KEYRING_BACKEND=${PYTHON_KEYRING_BACKEND:-<unset>}  (для теста: keyring.backends.fail.Keyring)"
echo "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-<unset>}  (для теста: xcb при проблемах Wayland)"
echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<unset>}"
echo

if [[ -z "$diag_pid" ]]; then
  echo "Укажите PID зависшего процесса для gdb/strace:"
  echo "  $0 <pid>"
  exit 0
fi

if ! kill -0 "$diag_pid" 2>/dev/null; then
  echo "Процесс $diag_pid не найден."
  exit 1
fi

echo "=== gdb thread apply all bt (batch) ==="
if command -v gdb >/dev/null 2>&1; then
  gdb -p "$diag_pid" -batch -ex "thread apply all bt" 2>&1 | head -200
else
  echo "gdb не установлен."
fi

echo
echo "=== strace -f -p (5 с, первые ~80 строк) ==="
if command -v strace >/dev/null 2>&1; then
  timeout 5 strace -f -p "$diag_pid" 2>&1 | head -80 || true
else
  echo "strace не установлен."
fi

echo
echo "Подсказка: искать в стеке dbus, secretstorage, poll, sam/7656, pthread_mutex."

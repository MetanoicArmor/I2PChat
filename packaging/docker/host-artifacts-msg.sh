#!/usr/bin/env bash
# Print where Linux Docker build outputs land on the host (bind mount ROOT -> /src).
# Usage: . path/to/host-artifacts-msg.sh && i2pchat_print_linux_host_artifacts /abs/repo [aarch64|x86_64]
i2pchat_print_linux_host_artifacts() {
  local root="${1:?repo root}"
  local arch="${2:-aarch64}"
  local ver
  ver="$(tr -d '\r\n' <"${root}/VERSION" 2>/dev/null || echo '?')"

  echo ""
  echo "==> Сборки на хосте (репозиторий смонтирован в контейнер как /src)"
  echo "    Каталог: ${root}"
  echo "    Версия из VERSION: ${ver}"
  echo ""
  echo "    Основные файлы:"
  echo "      ${root}/dist/                          — PyInstaller (onedir), AppImage с архитектурой в имени"
  echo "      ${root}/I2PChat.AppImage               — AppImage (текущая архитектура контейнера)"
  echo "      ${root}/I2PChat-linux-${arch}-v${ver}.zip   — GUI zip (в корне репо)"
  echo "      ${root}/I2PChat-linux-${arch}-tui-v${ver}.zip — TUI zip (в корне репо)"
  echo "      ${root}/SHA256SUMS                     — хеши двух zip (и SHA256SUMS.asc при подписи)"
  echo ""
  echo "    Отдельно «извлекать» из образа не нужно: всё пишется прямо в дерево репозитория."
  echo "    Если файлы принадлежат root (Linux + Docker без user namespace), исправьте владельца:"
  echo "      sudo chown -R \"\$(id -u):\$(id -g)\" \"${root}/dist\" \"${root}/build\" \"${root}/.gm\""
  echo "      sudo chown \"\$(id -u):\$(id -g)\" \"${root}/I2PChat.AppImage\" \"${root}/I2PChat-linux-${arch}-v${ver}.zip\" \"${root}/I2PChat-linux-${arch}-tui-v${ver}.zip\" \"${root}/SHA256SUMS\" 2>/dev/null || true"
}

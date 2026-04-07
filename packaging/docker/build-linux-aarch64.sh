#!/usr/bin/env bash
# Build **both** Linux aarch64 release artifacts in Docker (same as ./build-linux.sh end-to-end):
#   1) GUI — PyInstaller + AppImage → dist/…AppImage; zip по умолчанию **portable** (бинарники в корне zip)
#   2) TUI — PyInstaller slim + zip → I2PChat-linux-aarch64-tui-v*.zip
# Релизные zip лежат в корне репо (канон для SHA256SUMS); в dist/ — AppImage и onedir PyInstaller.
#
# Prerequisites:
#   - Docker with buildx; on x86_64 hosts use a builder that can run linux/arm64 (QEMU).
#   - Bundled i2pd: empty vendor/i2pd/ triggers ensure_bundled_i2pd.sh → default clone
#     https://github.com/MetanoicArmor/i2pchat-bundled-i2pd (needs network unless pre-staged).
#
# Usage (repo root):
#   ./packaging/docker/build-linux-aarch64.sh
#
# По умолчанию GUI zip — **portable** (в корне архива: I2PChat, I2PChat-tui, _internal, vendor).
# AppImage по-прежнему собирается в dist/ и I2PChat.AppImage в корне репо.
# Как в CI (один AppImage в zip): I2PCHAT_LINUX_GUI_ZIP_MODE=appimage ./packaging/docker/build-linux-aarch64.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host-artifacts-msg.sh
source "${DOCKER_DIR}/host-artifacts-msg.sh"
DOCKERFILE="${ROOT}/packaging/docker/linux-build-ubuntu2404-arm64.Dockerfile"
IMAGE="${I2PCHAT_LINUX_ARM64_IMAGE:-i2pchat-linux-build:ubuntu-24.04-arm64}"

if [[ ! -f "${ROOT}/vendor/i2pd/linux-aarch64/i2pd" ]]; then
  echo "==> No bundled aarch64 i2pd found in vendor/; continuing without embedded router"
fi

echo "==> Building Docker image ${IMAGE} (linux/arm64)"
docker buildx build --platform linux/arm64 --load -f "${DOCKERFILE}" -t "${IMAGE}" "${ROOT}/packaging/docker"

echo "==> Running full build-linux.sh in container (GUI AppImage + zip, затем TUI zip)"
export I2PCHAT_LINUX_GUI_ZIP_MODE="${I2PCHAT_LINUX_GUI_ZIP_MODE:-portable}"
docker run --rm --platform linux/arm64 \
  -v "${ROOT}:/src:rw" \
  -w /src \
  -e QT_QPA_PLATFORM=offscreen \
  -e APPIMAGE_EXTRACT_AND_RUN=1 \
  -e I2PCHAT_SKIP_GPG_SIGN=1 \
  -e "I2PCHAT_LINUX_GUI_ZIP_MODE=${I2PCHAT_LINUX_GUI_ZIP_MODE}" \
  "${IMAGE}" \
  ./build-linux.sh

VER="$(tr -d '\r\n' < "${ROOT}/VERSION")"
ZIP_GUI="${ROOT}/I2PChat-linux-aarch64-v${VER}.zip"
ZIP_TUI="${ROOT}/I2PChat-linux-aarch64-tui-v${VER}.zip"
for f in "${ZIP_GUI}" "${ZIP_TUI}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: после сборки должны быть оба zip (GUI + TUI), отсутствует: ${f}" >&2
    exit 1
  fi
done
echo "==> OK: оба релизных zip в корне репозитория"
ls -la "${ZIP_GUI}" "${ZIP_TUI}"

i2pchat_print_linux_host_artifacts "${ROOT}" aarch64

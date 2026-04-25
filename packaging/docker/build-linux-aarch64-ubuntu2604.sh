#!/usr/bin/env bash
# Build Linux aarch64 artifacts in Docker on Ubuntu 26.04.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host-artifacts-msg.sh
source "${DOCKER_DIR}/host-artifacts-msg.sh"
DOCKERFILE="${ROOT}/packaging/docker/linux-build-ubuntu2604-arm64.Dockerfile"
IMAGE="${I2PCHAT_LINUX_ARM64_IMAGE:-i2pchat-linux-build:ubuntu-26.04-arm64}"

if [[ ! -f "${ROOT}/vendor/i2pd/linux-aarch64/i2pd" ]]; then
  echo "==> No bundled aarch64 i2pd found in vendor/; continuing without embedded router"
fi

echo "==> Building Docker image ${IMAGE} (linux/arm64)"
docker buildx build --platform linux/arm64 --load -f "${DOCKERFILE}" -t "${IMAGE}" "${ROOT}/packaging/docker"

echo "==> Running full build-linux.sh in container (GUI AppImage + zip, then TUI zip)"
export I2PCHAT_LINUX_GUI_ZIP_MODE="${I2PCHAT_LINUX_GUI_ZIP_MODE:-portable}"
docker run --rm --platform linux/arm64 \
  -v "${ROOT}:/src:rw" \
  -w /src \
  -e QT_QPA_PLATFORM=offscreen \
  -e APPIMAGE_EXTRACT_AND_RUN=1 \
  -e I2PCHAT_SKIP_GPG_SIGN=1 \
  -e "I2PCHAT_LINUX_GUI_ZIP_MODE=${I2PCHAT_LINUX_GUI_ZIP_MODE}" \
  -e UV_PROJECT_ENVIRONMENT=/opt/i2pchat-venv \
  -e UV_LINK_MODE=copy \
  "${IMAGE}" \
  ./build-linux.sh

VER="$(tr -d '\r\n' < "${ROOT}/VERSION")"
ZIP_GUI="${ROOT}/I2PChat-linux-aarch64-v${VER}.zip"
ZIP_TUI="${ROOT}/I2PChat-linux-aarch64-tui-v${VER}.zip"
for f in "${ZIP_GUI}" "${ZIP_TUI}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: after build both zips must exist, missing: ${f}" >&2
    exit 1
  fi
done
echo "==> OK: both release zips are present in repo root"
ls -la "${ZIP_GUI}" "${ZIP_TUI}"

i2pchat_print_linux_host_artifacts "${ROOT}" aarch64

#!/usr/bin/env bash
# Build Linux AppImage + zips inside Ubuntu 24.04 (glibc 2.39).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${I2PCHAT_LINUX_DOCKER_TAG:-i2pchat-linux:noble-glibc239}"
DOCKERFILE="${ROOT}/packaging/docker/Dockerfile.linux-noble-glibc239"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found in PATH" >&2
  exit 1
fi

echo "==> Building image ${IMAGE_TAG}"
docker build -f "${DOCKERFILE}" -t "${IMAGE_TAG}" "${ROOT}/packaging/docker"

echo "==> Running build-linux.sh in container (mount ${ROOT} -> /src)"
exec docker run --rm -it \
  -e "I2PCHAT_SKIP_GPG_SIGN=${I2PCHAT_SKIP_GPG_SIGN:-1}" \
  -e "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}" \
  -e "APPIMAGE_EXTRACT_AND_RUN=${APPIMAGE_EXTRACT_AND_RUN:-1}" \
  -v "${ROOT}:/src:rw" \
  -w /src \
  "${IMAGE_TAG}" \
  ./build-linux.sh

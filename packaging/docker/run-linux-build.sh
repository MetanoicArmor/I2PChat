#!/usr/bin/env bash
# Build Linux AppImage + zips inside Ubuntu 24.04 (glibc 2.39).
# Uses Docker or Podman (auto-detect). Override: I2PCHAT_CONTAINER_RUNTIME=podman|docker
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host-artifacts-msg.sh
source "${DOCKER_DIR}/host-artifacts-msg.sh"
IMAGE_TAG="${I2PCHAT_LINUX_DOCKER_TAG:-i2pchat-linux:noble-glibc239}"
DOCKERFILE="${ROOT}/packaging/docker/Dockerfile.linux-noble-glibc239"

pick_runtime() {
  if [ -n "${I2PCHAT_CONTAINER_RUNTIME:-}" ]; then
    printf '%s\n' "${I2PCHAT_CONTAINER_RUNTIME}"
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    printf '%s\n' docker
    return 0
  fi
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    printf '%s\n' podman
    return 0
  fi
  return 1
}

if ! RT="$(pick_runtime)"; then
  echo "ERROR: нет доступного контейнерного рантайма (docker / podman)." >&2
  echo "" >&2
  echo "Docker (Arch / CachyOS и т.п.):" >&2
  echo "  sudo systemctl enable --now docker" >&2
  echo "  sudo usermod -aG docker \"\$USER\"   # затем перелогиниться" >&2
  echo "  # или разово: sudo docker build ..." >&2
  echo "" >&2
  echo "Podman:" >&2
  echo "  sudo pacman -S podman && podman info   # обычно без systemd-сокета Docker" >&2
  echo "" >&2
  echo "Проверка: docker info   или   podman info" >&2
  exit 1
fi

echo "==> Runtime: ${RT}"
# BuildKit нужен docker-buildx. На части систем классический builder падает на
# «failed to export layer» после тяжёлого RUN — тогда BuildKit обычно помогает.
if [ "${RT}" = docker ]; then
  if [ "${I2PCHAT_DOCKER_BUILDKIT:-}" = 0 ]; then
    :
  elif [ "${I2PCHAT_DOCKER_BUILDKIT:-}" = 1 ]; then
    export DOCKER_BUILDKIT=1
  elif docker buildx version >/dev/null 2>&1; then
    export DOCKER_BUILDKIT=1
    echo "==> DOCKER_BUILDKIT=1 (найден docker-buildx)"
  fi
fi

echo "==> Building image ${IMAGE_TAG}"
"${RT}" build -f "${DOCKERFILE}" -t "${IMAGE_TAG}" "${ROOT}/packaging/docker"

echo "==> Running build-linux.sh in container (mount ${ROOT} -> /src)"
"${RT}" run --rm -it \
  -e "I2PCHAT_SKIP_GPG_SIGN=${I2PCHAT_SKIP_GPG_SIGN:-1}" \
  -e "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}" \
  -e "APPIMAGE_EXTRACT_AND_RUN=${APPIMAGE_EXTRACT_AND_RUN:-1}" \
  -v "${ROOT}:/src:rw" \
  -w /src \
  "${IMAGE_TAG}" \
  ./build-linux.sh

i2pchat_print_linux_host_artifacts "${ROOT}" x86_64

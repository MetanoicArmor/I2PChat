#!/usr/bin/env bash
# System packages + FTXUI for ./build-linux.sh on Ubuntu 24.04 (amd64/arm64).
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq

FUSE_PKG=libfuse2
if apt-cache show libfuse2t64 >/dev/null 2>&1; then
  FUSE_PKG=libfuse2t64
fi
GLIB_PKG=libglib2.0-0
if apt-cache show libglib2.0-0t64 >/dev/null 2>&1; then
  GLIB_PKG=libglib2.0-0t64
fi

sudo apt-get install -y --no-install-recommends \
  ca-certificates git cmake ninja-build g++ pkg-config zip wget \
  desktop-file-utils file patchelf "${FUSE_PKG}" \
  libsodium-dev libboost-all-dev nlohmann-json3-dev \
  qt6-base-dev qt6-base-dev-tools \
  libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3 \
  "${GLIB_PKG}" libfontconfig1 libfreetype6 libdrm2 \
  libxcb1 libxcb-xinerama0 libxcb-xfixes0 libxcb-shape0 libxcb-render0 \
  libxcb-shm0 libxcb-randr0 libxcb-keysyms1 libxcb-image0 libxcb-icccm4 \
  libxcb-util1 libxcb-sync1 libxcb-xinput0 libxcb-cursor0

FTXUI_REF="${I2PCHAT_FTXUI_REF:-v6.1.9}"
SRC="/tmp/ftxui-src"
BUILD="/tmp/ftxui-build"
rm -rf "${SRC}" "${BUILD}"
git clone --depth 1 --branch "${FTXUI_REF}" https://github.com/ArthurSonzogni/FTXUI.git "${SRC}"
cmake -S "${SRC}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr/local \
  -DFTXUI_BUILD_EXAMPLES=OFF \
  -DFTXUI_BUILD_DOCS=OFF \
  -DFTXUI_BUILD_TESTS=OFF
cmake --build "${BUILD}"
sudo cmake --install "${BUILD}"
sudo ldconfig

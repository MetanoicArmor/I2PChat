#!/usr/bin/env bash
# Configure, build and install the C++ clients into a prefix.
# Usage: scripts/build_cpp_binaries.sh <build-dir> <install-prefix>
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <build-dir> <install-prefix>" >&2
  exit 1
fi

BUILD_DIR="$1"
PREFIX="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CPP_SRC="${REPO_ROOT}/cpp"

if [ ! -f "${CPP_SRC}/CMakeLists.txt" ]; then
  echo "ERROR: C++ sources not found at ${CPP_SRC}" >&2
  exit 1
fi

PREFIX_ARGS=()
TOOLCHAIN=()
if [ -n "${CMAKE_PREFIX_PATH:-}" ]; then
  PREFIX_ARGS=(-DCMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}")
elif [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  QT_PREFIX="$(brew --prefix qt 2>/dev/null || true)"
  FTXUI_PREFIX="$(brew --prefix ftxui 2>/dev/null || true)"
  extra=""
  [ -n "${QT_PREFIX}" ] && extra="${QT_PREFIX}"
  [ -n "${FTXUI_PREFIX}" ] && extra="${extra:+$extra;}${FTXUI_PREFIX}"
  if [ -n "${extra}" ]; then
    PREFIX_ARGS=(-DCMAKE_PREFIX_PATH="${extra}")
  fi
fi

TOOLCHAIN=()
if [ -n "${CMAKE_TOOLCHAIN_FILE:-}" ]; then
  TOOLCHAIN=(-DCMAKE_TOOLCHAIN_FILE="${CMAKE_TOOLCHAIN_FILE}")
elif [ -n "${VCPKG_ROOT:-}" ] && [ -f "${VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake" ]; then
  TOOLCHAIN=(-DCMAKE_TOOLCHAIN_FILE="${VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake")
  export VCPKG_MANIFEST_FEATURES="${VCPKG_MANIFEST_FEATURES:-tui;gui}"
fi

echo "==> CMake configure (${BUILD_DIR})"
set -- cmake -S "${CPP_SRC}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-Release}" \
  -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
  -DI2PCHAT_BUILD_TESTS=OFF \
  -DI2PCHAT_BUILD_TUI=ON \
  -DI2PCHAT_BUILD_GUI=ON
if command -v ninja >/dev/null 2>&1; then
  set -- "$@" -G Ninja
fi
if [ "$(uname -s)" = "Linux" ]; then
  set -- "$@" -DCMAKE_INSTALL_RPATH='$ORIGIN/../lib'
elif [ "$(uname -s)" = "Darwin" ]; then
  QT_LIB=""
  if command -v brew >/dev/null 2>&1; then
    QT_LIB="$(brew --prefix qt 2>/dev/null || true)/lib"
    QTBASE_LIB="$(brew --prefix qtbase 2>/dev/null || true)/lib"
    rpath="${QT_LIB}"
    if [ -d "${QTBASE_LIB}" ] && [ "${QTBASE_LIB}" != "${QT_LIB}" ]; then
      rpath="${rpath};${QTBASE_LIB}"
    fi
    set -- "$@" -DCMAKE_BUILD_RPATH="${rpath}"
  fi
  set -- "$@" -DCMAKE_INSTALL_RPATH="@executable_path/../Frameworks"
fi
if [ "${#PREFIX_ARGS[@]}" -gt 0 ]; then
  set -- "$@" "${PREFIX_ARGS[@]}"
fi
if [ "${#TOOLCHAIN[@]}" -gt 0 ]; then
  set -- "$@" "${TOOLCHAIN[@]}"
fi
"$@"

echo "==> CMake build"
cmake --build "${BUILD_DIR}" --config "${CMAKE_BUILD_TYPE:-Release}" --parallel

echo "==> CMake install → ${PREFIX}"
cmake --install "${BUILD_DIR}" --config "${CMAKE_BUILD_TYPE:-Release}" --prefix "${PREFIX}"

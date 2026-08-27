#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
APPDIR="${APP_NAME}.AppDir"
APPIMAGETOOL_VERSION="1.9.1"
cd "$(dirname "${BASH_SOURCE[0]}")"

VERSION_FILE="VERSION"
if [ ! -f "${VERSION_FILE}" ]; then
  echo "ERROR: VERSION file not found: ${VERSION_FILE}" >&2
  exit 1
fi
RELEASE_VERSION="$(tr -d '\r\n' < "${VERSION_FILE}")"
if [ -z "${RELEASE_VERSION}" ]; then
  echo "ERROR: VERSION file is empty: ${VERSION_FILE}" >&2
  exit 1
fi

ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  ARCH_SUFFIX="x86_64" ;;
  aarch64) ARCH_SUFFIX="aarch64" ;;
  armv7l)  ARCH_SUFFIX="armhf" ;;
  *)       ARCH_SUFFIX="$ARCH" ;;
esac

echo "==> Building C++ client for architecture: ${ARCH_SUFFIX}"

case "${ARCH_SUFFIX}" in
  aarch64) I2PD_LINUX_SUBDIR="linux-aarch64" ;;
  *)       I2PD_LINUX_SUBDIR="linux-x86_64" ;;
esac

if ! command -v cmake >/dev/null 2>&1; then
  echo "ERROR: cmake >= 3.24 is required." >&2
  exit 1
fi

REPO_ROOT="$(pwd)"

file_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    echo "ERROR: need sha256sum or shasum" >&2
    exit 1
  fi
}

write_checksums() {
  local out="$1"
  shift
  : > "${out}"
  local path
  for path in "$@"; do
    printf '%s  %s\n' "$(file_sha256 "${path}")" "$(basename "${path}")" >> "${out}"
  done
}

safe_rm_rf() {
  local p
  _try_rm() {
    local t="$1"
    chmod -R u+w "$t" 2>/dev/null || true
    rm -rf "$t"
  }
  for p in "$@"; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    if _try_rm "$p"; then
      continue
    fi
    chmod -R a+rwx "$p" 2>/dev/null || true
    if _try_rm "$p"; then
      continue
    fi
    if command -v sudo >/dev/null 2>&1; then
      echo "WARN: «$p» не удалось снять без sudo (вероятно владелец root); пробую sudo chown…" >&2
      if sudo chown -R "$(id -u):$(id -g)" "$p" 2>/dev/null; then
        chmod -R u+rwX "$p" 2>/dev/null || true
        if _try_rm "$p"; then
          continue
        fi
      fi
    fi
    echo "ERROR: не удалось удалить «$p» (нет прав или файл занят)." >&2
    echo "       Закройте ${APP_NAME} / ${APP_NAME}-tui и снимите монтирование AppImage, если оно из этого каталога." >&2
    exit 1
  done
}

stage_elf_deps() {
  local bin="$1"
  local dest="$2"
  mkdir -p "${dest}"
  if ! command -v ldd >/dev/null 2>&1; then
    return 0
  fi
  ldd "${bin}" | awk '/=>/ { print $3 }' | while read -r lib; do
    [ -n "${lib}" ] && [ -f "${lib}" ] || continue
    case "${lib}" in
      */ld-linux*.so* | */libc.so* | */libm.so* | */libpthread.so* | */libdl.so* | \
      */libresolv.so* | */librt.so* | */libstdc++.so* | */libgcc_s.so*)
        continue
        ;;
    esac
    cp -a "${lib}" "${dest}/" 2>/dev/null || true
  done
}

echo "==> Checking optional bundled i2pd source"
"${REPO_ROOT}/scripts/ensure_bundled_i2pd.sh"
if [ ! -f "vendor/i2pd/${I2PD_LINUX_SUBDIR}/i2pd" ]; then
  echo "WARN: нет vendor/i2pd/${I2PD_LINUX_SUBDIR}/i2pd — AppImage и GUI-zip будут без встроенного i2pd." >&2
  echo "      Задайте I2PCHAT_BUNDLED_I2PD_SOURCE_DIR, SSH URL в I2PCHAT_BUNDLED_I2PD_GIT_URL, или ./scripts/fetch_bundled_i2pd.sh --from …" >&2
fi

I2PD_BUNDLE_DIR="vendor/i2pd/${I2PD_LINUX_SUBDIR}"
if [ -f "${I2PD_BUNDLE_DIR}/i2pd" ]; then
  chmod +x "${I2PD_BUNDLE_DIR}/i2pd" 2>/dev/null || true
  if [ ! -f "${I2PD_BUNDLE_DIR}/libboost_program_options.so.1.83.0" ] || [ -L "${I2PD_BUNDLE_DIR}/libboost_program_options.so.1.83.0" ]; then
    for CAND in \
      /usr/lib/libboost_program_options.so.1.83.0 \
      /usr/lib64/libboost_program_options.so.1.83.0 \
      /lib/libboost_program_options.so.1.83.0 \
      /lib64/libboost_program_options.so.1.83.0 \
      /usr/lib/*/libboost_program_options.so.1.83.0 \
      /lib/*/libboost_program_options.so.1.83.0; do
      if [ -f "$CAND" ]; then
        rm -f "${I2PD_BUNDLE_DIR}/libboost_program_options.so.1.83.0"
        cp -f "$CAND" "${I2PD_BUNDLE_DIR}/libboost_program_options.so.1.83.0"
        chmod +x "${I2PD_BUNDLE_DIR}/libboost_program_options.so.1.83.0" 2>/dev/null || true
        echo "==> Added libboost_program_options.so.1.83.0 to ${I2PD_BUNDLE_DIR}"
        break
      fi
    done
  fi
fi

if [ -f "${I2PD_BUNDLE_DIR}/i2pd" ] && command -v objdump >/dev/null 2>&1; then
  if ! "${REPO_ROOT}/scripts/stage_i2pd_linux_shlibs.sh"; then
    if [ "${I2PCHAT_SKIP_DISTRO_I2PD_FALLBACK:-0}" = "1" ]; then
      exit 1
    fi
    if command -v i2pd >/dev/null 2>&1; then
      echo "==> Staging Boost для bundled i2pd не удался; копирую системный i2pd из PATH" >&2
      cp -f "$(command -v i2pd)" "${I2PD_BUNDLE_DIR}/i2pd"
      chmod +x "${I2PD_BUNDLE_DIR}/i2pd"
      "${REPO_ROOT}/scripts/stage_i2pd_linux_shlibs.sh" || {
        echo "ERROR: после копирования i2pd из PATH staging всё ещё не прошёл." >&2
        exit 1
      }
    else
      exit 1
    fi
  fi
fi

STAGE="${REPO_ROOT}/dist/cpp-install"
BUILD_DIR="${REPO_ROOT}/cpp/build-release"
safe_rm_rf "${STAGE}"
mkdir -p "${STAGE}"
"${REPO_ROOT}/scripts/build_cpp_binaries.sh" "${BUILD_DIR}" "${STAGE}"

GUI_BIN="${STAGE}/bin/i2pchat-gui"
TUI_BIN="${STAGE}/bin/i2pchat-tui"
DAEMON_BIN="${STAGE}/bin/i2pchat-blindbox-daemon"
if [ ! -x "${GUI_BIN}" ] || [ ! -x "${TUI_BIN}" ]; then
  echo "ERROR: CMake install did not produce i2pchat-gui / i2pchat-tui under ${STAGE}/bin" >&2
  ls -la "${STAGE}/bin" >&2 || true
  exit 1
fi

ONEDIR="dist/${APP_NAME}"
safe_rm_rf "${ONEDIR}"
mkdir -p "${ONEDIR}"
cp "${GUI_BIN}" "${ONEDIR}/${APP_NAME}"
cp "${TUI_BIN}" "${ONEDIR}/${APP_NAME}-tui"
chmod +x "${ONEDIR}/${APP_NAME}" "${ONEDIR}/${APP_NAME}-tui"
if [ -x "${DAEMON_BIN}" ]; then
  cp "${DAEMON_BIN}" "${ONEDIR}/i2pchat-blindbox-daemon"
  chmod +x "${ONEDIR}/i2pchat-blindbox-daemon"
fi
mkdir -p "${ONEDIR}/lib"
stage_elf_deps "${ONEDIR}/${APP_NAME}" "${ONEDIR}/lib"
stage_elf_deps "${ONEDIR}/${APP_NAME}-tui" "${ONEDIR}/lib"
if [ -d "vendor/i2pd/${I2PD_LINUX_SUBDIR}" ]; then
  mkdir -p "${ONEDIR}/vendor/i2pd/${I2PD_LINUX_SUBDIR}"
  cp -a "vendor/i2pd/${I2PD_LINUX_SUBDIR}/." "${ONEDIR}/vendor/i2pd/${I2PD_LINUX_SUBDIR}/"
fi

# Qt platform plugins so the GUI runs off a machine that has no system Qt.
QT_PLUGIN_DIR=""
if command -v qtpaths6 >/dev/null 2>&1; then
  QT_PLUGIN_DIR="$(qtpaths6 --plugin-dir 2>/dev/null || true)"
elif command -v qtpaths >/dev/null 2>&1; then
  QT_PLUGIN_DIR="$(qtpaths --plugin-dir 2>/dev/null || true)"
fi
if [ -n "${QT_PLUGIN_DIR}" ] && [ -d "${QT_PLUGIN_DIR}/platforms" ]; then
  mkdir -p "${ONEDIR}/plugins"
  cp -a "${QT_PLUGIN_DIR}/platforms" "${ONEDIR}/plugins/"
  [ -d "${QT_PLUGIN_DIR}/imageformats" ] && cp -a "${QT_PLUGIN_DIR}/imageformats" "${ONEDIR}/plugins/"
  [ -d "${QT_PLUGIN_DIR}/tls" ] && cp -a "${QT_PLUGIN_DIR}/tls" "${ONEDIR}/plugins/"
fi

safe_rm_rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" \
         "${APPDIR}/usr/lib" \
         "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/icons/hicolor/512x512/apps"

cp "${ONEDIR}/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}"
cp "${ONEDIR}/${APP_NAME}-tui" "${APPDIR}/usr/bin/${APP_NAME}-tui"
[ -x "${ONEDIR}/i2pchat-blindbox-daemon" ] && cp "${ONEDIR}/i2pchat-blindbox-daemon" "${APPDIR}/usr/bin/"
cp -a "${ONEDIR}/lib/." "${APPDIR}/usr/lib/" 2>/dev/null || true
if [ -d "${ONEDIR}/plugins" ]; then
  cp -a "${ONEDIR}/plugins" "${APPDIR}/usr/plugins"
fi
if [ -d "${ONEDIR}/vendor" ]; then
  cp -a "${ONEDIR}/vendor" "${APPDIR}/usr/bin/vendor"
  if [ -f "${APPDIR}/usr/bin/vendor/i2pd/${I2PD_LINUX_SUBDIR}/i2pd" ]; then
    chmod +x "${APPDIR}/usr/bin/vendor/i2pd/${I2PD_LINUX_SUBDIR}/i2pd"
  fi
fi
cp icon.png "${APPDIR}/usr/share/icons/hicolor/512x512/apps/i2pchat.png"

cat > "${APPDIR}/usr/share/applications/i2pchat.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=I2P Chat
Comment=Secure chat over I2P (signed handshake, TOFU)
Exec=${APP_NAME}
Icon=i2pchat
Terminal=false
Categories=Network;Chat;
EOF

cat > "${APPDIR}/usr/share/applications/i2pchat-tui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=I2P Chat (terminal)
Comment=I2PChat FTXUI TUI — run in a terminal
Exec=${APP_NAME}-tui
Icon=i2pchat
Terminal=true
Categories=Network;Chat;
EOF

cp "${APPDIR}/usr/share/applications/i2pchat.desktop" "${APPDIR}/i2pchat.desktop"
cp "${APPDIR}/usr/share/applications/i2pchat-tui.desktop" "${APPDIR}/i2pchat-tui.desktop"
cp icon.png "${APPDIR}/i2pchat.png"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
if [ -d "$HERE/usr/plugins" ]; then
  export QT_PLUGIN_PATH="$HERE/usr/plugins"
  export QT_QPA_PLATFORM_PLUGIN_PATH="$HERE/usr/plugins/platforms"
fi
exec "$HERE/usr/bin/I2PChat" "$@"
EOF
chmod +x "${APPDIR}/AppRun" "${APPDIR}/usr/bin/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}-tui"

APPIMAGETOOL="appimagetool-${ARCH}.AppImage"
case "${ARCH}" in
  x86_64) APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0" ;;
  aarch64) APPIMAGETOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158" ;;
  armv7l) APPIMAGETOOL_SHA256="42b61cba5495d8aaf418a5c9a015a49b85ad92efabcbd3c341f1540440e4e23d" ;;
  *)
    echo "ERROR: Unsupported architecture for pinned appimagetool: ${ARCH}" >&2
    exit 1
    ;;
esac

if [ ! -f "$APPIMAGETOOL" ]; then
  echo "==> Downloading appimagetool for ${ARCH}..."
  wget "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/${APPIMAGETOOL}"
fi
ACTUAL_SHA256="$(file_sha256 "${APPIMAGETOOL}")"
if [ "${ACTUAL_SHA256}" != "${APPIMAGETOOL_SHA256}" ]; then
  echo "⚠ SHA256 mismatch for existing ${APPIMAGETOOL}, re-downloading pinned version..." >&2
  rm -f "${APPIMAGETOOL}"
  wget "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/${APPIMAGETOOL}"
  ACTUAL_SHA256="$(file_sha256 "${APPIMAGETOOL}")"
  if [ "${ACTUAL_SHA256}" != "${APPIMAGETOOL_SHA256}" ]; then
    echo "ERROR: SHA256 mismatch for downloaded ${APPIMAGETOOL}" >&2
    echo "Expected: ${APPIMAGETOOL_SHA256}" >&2
    echo "Actual:   ${ACTUAL_SHA256}" >&2
    exit 1
  fi
fi
chmod +x "$APPIMAGETOOL"

mkdir -p "dist"
OUTPUT_FILE="dist/${APP_NAME}-linux-${ARCH_SUFFIX}-v${RELEASE_VERSION}.AppImage"
./"$APPIMAGETOOL" "${APPDIR}" "$OUTPUT_FILE"
echo "✔ Built ${OUTPUT_FILE}"

ROOT_APPIMAGE="${APP_NAME}.AppImage"
if [ -e "$ROOT_APPIMAGE" ] || [ -L "$ROOT_APPIMAGE" ]; then
  if cp -f "$OUTPUT_FILE" "$ROOT_APPIMAGE" 2>/dev/null; then
    echo "✔ Updated ${ROOT_APPIMAGE}"
  else
    echo "⚠ Skipped ${ROOT_APPIMAGE}: file busy (artifact is ${OUTPUT_FILE})" >&2
  fi
else
  cp "$OUTPUT_FILE" "$ROOT_APPIMAGE" 2>/dev/null || true
fi

ZIP_FILE="${APP_NAME}-linux-${ARCH_SUFFIX}-v${RELEASE_VERSION}.zip"
rm -f "${ZIP_FILE}"
ZIP_MODE="${I2PCHAT_LINUX_GUI_ZIP_MODE:-appimage}"
case "${ZIP_MODE}" in
  appimage)
    if command -v zip >/dev/null 2>&1; then
      zip -j "${ZIP_FILE}" "${OUTPUT_FILE}"
    else
      python3 - "${OUTPUT_FILE}" "${ZIP_FILE}" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(src, arcname=os.path.basename(src))
PY
    fi
    ;;
  portable)
    ZIP_ABS="$(pwd)/${ZIP_FILE}"
    ( cd "${ONEDIR}" && zip -qry "${ZIP_ABS}" . )
    ;;
  *)
    echo "ERROR: unknown I2PCHAT_LINUX_GUI_ZIP_MODE=${ZIP_MODE} (use appimage or portable)" >&2
    exit 1
    ;;
esac
echo "✔ Packed ${ZIP_FILE} (GUI zip mode: ${ZIP_MODE})"

TUI_ZIP="${APP_NAME}-linux-${ARCH_SUFFIX}-tui-v${RELEASE_VERSION}.zip"
TUI_STAGE="${APP_NAME}-linux-${ARCH_SUFFIX}-tui-v${RELEASE_VERSION}-stage"
safe_rm_rf "${TUI_STAGE}"
mkdir -p "${TUI_STAGE}/usr/bin" "${TUI_STAGE}/usr/lib"
cp "${ONEDIR}/${APP_NAME}-tui" "${TUI_STAGE}/usr/bin/"
cp -a "${ONEDIR}/lib/." "${TUI_STAGE}/usr/lib/" 2>/dev/null || true
if [ -d "${ONEDIR}/vendor" ]; then
  cp -a "${ONEDIR}/vendor" "${TUI_STAGE}/usr/bin/vendor"
fi
cat > "${TUI_STAGE}/i2pchat-tui" <<EOF
#!/bin/sh
SCRIPT="\$0"
while [ -h "\$SCRIPT" ]; do
  LINK="\$(readlink "\$SCRIPT" 2>/dev/null || true)"
  case "\$LINK" in
    /*) SCRIPT="\$LINK" ;;
    *) SCRIPT="\$(dirname "\$SCRIPT")/\$LINK" ;;
  esac
done
HERE="\$(cd "\$(dirname "\$SCRIPT")" && pwd)"
export LD_LIBRARY_PATH="\$HERE/usr/lib:\${LD_LIBRARY_PATH:-}"
exec "\$HERE/usr/bin/${APP_NAME}-tui" "\$@"
EOF
chmod +x "${TUI_STAGE}/i2pchat-tui" "${TUI_STAGE}/usr/bin/${APP_NAME}-tui"
rm -f "${TUI_ZIP}"
TUI_ZIP_ABS="$(pwd)/${TUI_ZIP}"
( cd "${TUI_STAGE}" && zip -qry "${TUI_ZIP_ABS}" . )
safe_rm_rf "${TUI_STAGE}"
echo "✔ Packed ${TUI_ZIP}"

SHA256_FILE="SHA256SUMS"
write_checksums "${SHA256_FILE}" "${ZIP_FILE}" "${TUI_ZIP}"
echo "✔ Generated ${SHA256_FILE} (GUI + TUI zips)"

if [ "${I2PCHAT_SKIP_GPG_SIGN:-0}" = "1" ]; then
  echo "⚠ Skipping GPG detached signature (I2PCHAT_SKIP_GPG_SIGN=1)"
elif ! command -v gpg >/dev/null 2>&1; then
  if [ "${I2PCHAT_REQUIRE_GPG:-0}" = "1" ]; then
    echo "ERROR: gpg is required to create detached release signature" >&2
    exit 1
  fi
  echo "⚠ gpg not found; skipping detached signature (set I2PCHAT_REQUIRE_GPG=1 to enforce)"
else
  use_gpg_batch=1
  if [ -t 0 ] || [ -t 1 ]; then
    use_gpg_batch=0
  fi
  case "${I2PCHAT_GPG_BATCH:-}" in
    0) use_gpg_batch=0 ;;
    1) use_gpg_batch=1 ;;
  esac
  GPG_ARGS=()
  if [ "$use_gpg_batch" = 1 ]; then
    GPG_ARGS+=(--batch --yes)
  fi
  GPG_ARGS+=(--armor --detach-sign --output "${SHA256_FILE}.asc")
  if [ -n "${I2PCHAT_GPG_KEY_ID:-}" ]; then
    GPG_ARGS+=(--local-user "${I2PCHAT_GPG_KEY_ID}")
  fi
  if gpg "${GPG_ARGS[@]}" "${SHA256_FILE}"; then
    echo "✔ Generated ${SHA256_FILE}.asc"
  else
    if [ "${I2PCHAT_REQUIRE_GPG:-0}" = "1" ]; then
      echo "ERROR: gpg signing failed in required mode" >&2
      exit 1
    fi
    echo "⚠ gpg signing failed; continuing without detached signature" >&2
  fi
fi

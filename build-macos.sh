#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
BLINDBOX_PACKAGING="cpp/apps/blindbox-daemon/packaging"
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
I2PD_DARWIN_VENDOR_SUB="darwin-arm64"
case "$ARCH" in
  x86_64) ARCH_SUFFIX="x64" ; I2PD_DARWIN_VENDOR_SUB="darwin-x64" ;;
  arm64)  ARCH_SUFFIX="arm64" ;;
  *)      ARCH_SUFFIX="$ARCH" ;;
esac

echo "==> Building C++ client for architecture: ${ARCH_SUFFIX}"
if ! command -v cmake >/dev/null 2>&1; then
  echo "ERROR: cmake >= 3.24 is required (brew install cmake)." >&2
  exit 1
fi

REPO_ROOT="$(pwd)"

file_sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
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

echo "==> Checking optional bundled i2pd source"
"${REPO_ROOT}/scripts/ensure_bundled_i2pd.sh"
if [ ! -f "vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd" ]; then
  echo "WARN: нет vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd — .app будет без встроенного i2pd." >&2
fi

STAGE="${REPO_ROOT}/dist/cpp-install"
BUILD_DIR="${REPO_ROOT}/cpp/build-release"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"
"${REPO_ROOT}/scripts/build_cpp_binaries.sh" "${BUILD_DIR}" "${STAGE}"

GUI_BIN="${STAGE}/bin/i2pchat-gui"
TUI_BIN="${STAGE}/bin/i2pchat-tui"
DAEMON_BIN="${STAGE}/bin/i2pchat-blindbox-daemon"
if [ ! -x "${GUI_BIN}" ] || [ ! -x "${TUI_BIN}" ]; then
  echo "ERROR: missing i2pchat-gui / i2pchat-tui in ${STAGE}/bin" >&2
  exit 1
fi

echo "==> Собираю I2PChat.app"
rm -rf "dist/${APP_NAME}.app"
mkdir -p "dist/${APP_NAME}.app/Contents/MacOS" "dist/${APP_NAME}.app/Contents/Resources"
cp "${GUI_BIN}" "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
cp "${TUI_BIN}" "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}-tui"
chmod +x "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}" \
         "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}-tui"
if [ -x "${DAEMON_BIN}" ]; then
  cp "${DAEMON_BIN}" "dist/${APP_NAME}.app/Contents/MacOS/i2pchat-blindbox-daemon"
  chmod +x "dist/${APP_NAME}.app/Contents/MacOS/i2pchat-blindbox-daemon"
fi

if [ -f "vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd" ]; then
  mkdir -p "dist/${APP_NAME}.app/Contents/Resources/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}"
  cp "vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd" \
    "dist/${APP_NAME}.app/Contents/Resources/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd"
  chmod +x "dist/${APP_NAME}.app/Contents/Resources/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd"
fi

if [ -f "I2PChat.icns" ]; then
  cp "I2PChat.icns" "dist/${APP_NAME}.app/Contents/Resources/I2PChat.icns"
elif [ -f "icon.png" ]; then
  echo "WARNING: I2PChat.icns not found, fallback to icon.png"
  cp "icon.png" "dist/${APP_NAME}.app/Contents/Resources/I2PChat.icns"
fi

if [ -d "i2pchat/gui/fluent_emoji" ]; then
  mkdir -p "dist/${APP_NAME}.app/Contents/Resources/fluent_emoji"
  rsync -a --delete "i2pchat/gui/fluent_emoji/" "dist/${APP_NAME}.app/Contents/Resources/fluent_emoji/"
fi
if [ -f "i2pchat/gui/icons/face.dashed.png" ]; then
  mkdir -p "dist/${APP_NAME}.app/Contents/Resources/icons"
  cp "i2pchat/gui/icons/face.dashed.png" "dist/${APP_NAME}.app/Contents/Resources/icons/"
fi

cat > "dist/${APP_NAME}.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>I2PChat</string>
	<key>CFBundleIconFile</key>
	<string>I2PChat.icns</string>
	<key>CFBundleIdentifier</key>
	<string>net.i2pchat.I2PChat</string>
	<key>CFBundleName</key>
	<string>I2PChat</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>${RELEASE_VERSION}</string>
	<key>CFBundleVersion</key>
	<string>${RELEASE_VERSION}</string>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
</dict>
</plist>
PLIST

MACDEPLOYQT=""
QT_LIBPATHS=()
if command -v brew >/dev/null 2>&1; then
  QT_PREFIX="$(brew --prefix qt 2>/dev/null || true)"
  QTBASE_PREFIX="$(brew --prefix qtbase 2>/dev/null || true)"
  if [ -x "${QT_PREFIX}/bin/macdeployqt" ]; then
    MACDEPLOYQT="${QT_PREFIX}/bin/macdeployqt"
  fi
  if [ -d "${QT_PREFIX}/lib" ]; then
    QT_LIBPATHS+=(-libpath="${QT_PREFIX}/lib")
  fi
  if [ -d "${QTBASE_PREFIX}/lib" ] && [ "${QTBASE_PREFIX}" != "${QT_PREFIX}" ]; then
    QT_LIBPATHS+=(-libpath="${QTBASE_PREFIX}/lib")
  fi
fi
if [ -z "${MACDEPLOYQT}" ] && command -v macdeployqt >/dev/null 2>&1; then
  MACDEPLOYQT="$(command -v macdeployqt)"
fi
if [ -n "${MACDEPLOYQT}" ]; then
  echo "==> macdeployqt"
  # Homebrew Qt 6 plugins resolve @rpath against the .app's sibling lib/
  # (dist/I2PChat.app → dist/lib). -libpath is ignored for that lookup.
  DIST_LIB_LINK=""
  if [ -n "${QT_PREFIX:-}" ] && [ -d "${QT_PREFIX}/lib" ]; then
    if [ -L dist/lib ] || [ ! -e dist/lib ]; then
      ln -sfn "${QT_PREFIX}/lib" dist/lib
      DIST_LIB_LINK=1
    fi
  fi
  "${MACDEPLOYQT}" "dist/${APP_NAME}.app" \
    -executable="dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}-tui" \
    -always-overwrite \
    "${QT_LIBPATHS[@]}"
  if [ -n "${DIST_LIB_LINK}" ]; then
    rm -f dist/lib
  fi
  if [ ! -d "dist/${APP_NAME}.app/Contents/Frameworks/QtCore.framework" ]; then
    echo "ERROR: macdeployqt did not copy QtCore.framework (rpath/libpath)." >&2
    echo "       Pass Homebrew Qt via: brew --prefix qt  → ${QT_PREFIX:-unset}" >&2
    exit 1
  fi
else
  echo "WARN: macdeployqt not found; the .app will need a system Qt 6 install to run." >&2
fi

echo "==> Rewrite Homebrew install names to bundled Frameworks"
"${REPO_ROOT}/scripts/macos_relink_bundle_binaries.sh" "dist/${APP_NAME}.app"

# Homebrew/Qt dylibs keep a signature that becomes invalid after install-name
# rewrites. Unsigned or stale nested Mach-O pages then SIGKILL on launch
# (CODESIGNING / Invalid Page), including on local unsigned apps.
sign_macho() {
  local identity="$1"
  shift
  local extra=()
  if [ "${identity}" != "-" ]; then
    extra+=(--options runtime --timestamp)
  else
    extra+=(--timestamp=none)
  fi
  codesign --force --sign "${identity}" "${extra[@]}" "$@"
}

sign_app_bundle() {
  local app="$1"
  local identity="${I2PCHAT_CODESIGN_IDENTITY:--}"
  echo "==> codesign (${identity})"
  xattr -cr "${app}" 2>/dev/null || true
  local f
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    sign_macho "${identity}" "${f}"
  done < <(find "${app}" -type f \( -name '*.dylib' -o -name '*.so' \) 2>/dev/null | sort)
  if [ -d "${app}/Contents/PlugIns" ]; then
    while IFS= read -r f; do
      [ -n "${f}" ] || continue
      if file -b "${f}" | grep -q 'Mach-O'; then
        sign_macho "${identity}" "${f}"
      fi
    done < <(find "${app}/Contents/PlugIns" -type f 2>/dev/null | sort)
  fi
  if [ -d "${app}/Contents/Frameworks" ]; then
    while IFS= read -r f; do
      [ -n "${f}" ] || continue
      sign_macho "${identity}" "${f}"
    done < <(find "${app}/Contents/Frameworks" -name '*.framework' -print 2>/dev/null | awk '{ print length, $0 }' | sort -nr | awk '{ $1=""; sub(/^ /,""); print }')
  fi
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    if file -b "${f}" | grep -q 'Mach-O'; then
      sign_macho "${identity}" "${f}"
    fi
  done < <(find "${app}/Contents" -type f ! -name '*.dylib' ! -name '*.so' 2>/dev/null | sort)
  sign_macho "${identity}" "${app}"
}

sign_app_bundle "dist/${APP_NAME}.app"

echo "✔ GUI собран: dist/${APP_NAME}.app (${ARCH_SUFFIX})"

ZIP_FILE="I2PChat-macOS-${ARCH_SUFFIX}-v${RELEASE_VERSION}.zip"
rm -f "${ZIP_FILE}"
ZIP_STAGE="dist/${APP_NAME}-macOS-${ARCH_SUFFIX}-bundle"
rm -rf "${ZIP_STAGE}"
mkdir -p "${ZIP_STAGE}"
cp -R "dist/${APP_NAME}.app" "${ZIP_STAGE}/"
if [ -d "${BLINDBOX_PACKAGING}" ]; then
  mkdir -p "${ZIP_STAGE}/blindbox-daemon"
  cp "${BLINDBOX_PACKAGING}/i2pchat-blindbox.service" \
     "${BLINDBOX_PACKAGING}/daemon.env.example" \
     "${ZIP_STAGE}/blindbox-daemon/" 2>/dev/null || true
fi
ditto -c -k --sequesterRsrc --keepParent "${ZIP_STAGE}" "${ZIP_FILE}"
rm -rf "${ZIP_STAGE}"
echo "✔ Packed ${ZIP_FILE}"

TUI_ZIP="I2PChat-macOS-${ARCH_SUFFIX}-tui-v${RELEASE_VERSION}.zip"
TUI_STAGE="dist/${APP_NAME}-macOS-${ARCH_SUFFIX}-tui-stage"
rm -rf "${TUI_STAGE}"
mkdir -p "${TUI_STAGE}/I2PChat"
cp "${TUI_BIN}" "${TUI_STAGE}/I2PChat/${APP_NAME}-tui"
chmod +x "${TUI_STAGE}/I2PChat/${APP_NAME}-tui"
codesign --force --sign - --timestamp=none "${TUI_STAGE}/I2PChat/${APP_NAME}-tui" 2>/dev/null || true
if [ -f "vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd" ]; then
  mkdir -p "${TUI_STAGE}/I2PChat/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}"
  cp "vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd" \
    "${TUI_STAGE}/I2PChat/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd"
  chmod +x "${TUI_STAGE}/I2PChat/vendor/i2pd/${I2PD_DARWIN_VENDOR_SUB}/i2pd"
fi
cat > "${TUI_STAGE}/i2pchat-tui" <<'EOF'
#!/bin/sh
SCRIPT="$0"
while [ -h "$SCRIPT" ]; do
  LINK="$(readlink "$SCRIPT" 2>/dev/null || true)"
  case "$LINK" in
    /*) SCRIPT="$LINK" ;;
    *) SCRIPT="$(dirname "$SCRIPT")/$LINK" ;;
  esac
done
HERE="$(cd "$(dirname "$SCRIPT")" && pwd)"
if [ ! -x "$HERE/I2PChat/I2PChat-tui" ]; then
  for P in "/opt/homebrew/opt/i2pchat-tui" "/usr/local/opt/i2pchat-tui"; do
    if [ -x "$P/I2PChat/I2PChat-tui" ]; then
      HERE="$P"
      break
    fi
  done
fi
exec "$HERE/I2PChat/I2PChat-tui" "$@"
EOF
chmod +x "${TUI_STAGE}/i2pchat-tui"
rm -f "${TUI_ZIP}"
TUI_ZIP_ABS="$(pwd)/${TUI_ZIP}"
( cd "${TUI_STAGE}" && zip -qry "${TUI_ZIP_ABS}" . )
rm -rf "${TUI_STAGE}"
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
echo "  Можно перенести dist/${APP_NAME}.app в /Applications и запускать двойным кликом."
echo "  TUI: dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}-tui"

#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
VENV_DIR=".venv314"
BLINDBOX_INSTALL_SRC="i2pchat/blindbox/daemon/install/install.sh"
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

# Определяем архитектуру
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ARCH_SUFFIX="x64" ;;
  arm64)  ARCH_SUFFIX="arm64" ;;
  *)      ARCH_SUFFIX="$ARCH" ;;
esac

echo "==> Building for architecture: ${ARCH_SUFFIX}"
echo "==> Активирую окружение ${VENV_DIR}"
if [ ! -d "${VENV_DIR}" ]; then
  if command -v python3.14 >/dev/null 2>&1; then
    PYTHON_BIN="python3.14"
  else
    PYTHON_BIN="python3"
  fi
  echo "==> Создаю виртуальное окружение ${VENV_DIR} на базе ${PYTHON_BIN}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"

if [ -x "${VENV_DIR}/bin/python" ]; then
  PYTHON_CMD="${VENV_DIR}/bin/python"
elif [ -x "${VENV_DIR}/bin/python3" ]; then
  PYTHON_CMD="${VENV_DIR}/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3)"
else
  PYTHON_CMD="$(command -v python)"
fi

echo "==> Устанавливаю/обновляю зависимости"
"${PYTHON_CMD}" -m pip install --upgrade pip
"${PYTHON_CMD}" -m pip install --require-hashes -r requirements.txt
"${PYTHON_CMD}" -m pip install --require-hashes -r requirements-build.txt

echo "==> Проверяю PyNaCl (обязателен для secure protocol)"
"${PYTHON_CMD}" - <<'PY'
import sys
try:
    import nacl
    from nacl.secret import SecretBox  # noqa: F401
except Exception as exc:
    print(f"ERROR: PyNaCl is required for secure protocol build: {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"PyNaCl OK: {getattr(nacl, '__version__', 'unknown')}")
PY

echo "==> Проверяю синтаксис пакетов и вспомогательных скриптов"
"${PYTHON_CMD}" -m compileall i2pchat vendor/i2plib scripts make_icon.py

echo "==> Собираю GUI (PyInstaller I2PChat.spec)"
rm -rf "dist/${APP_NAME}" "build/${APP_NAME}"
"${PYTHON_CMD}" -m PyInstaller --clean -y I2PChat.spec

echo "==> Собираю I2PChat.app"
rm -rf "dist/${APP_NAME}.app"
mkdir -p "dist/${APP_NAME}.app/Contents/MacOS" "dist/${APP_NAME}.app/Contents/Resources"
cp -R "dist/${APP_NAME}" "dist/${APP_NAME}.app/Contents/Resources/${APP_NAME}"
if [ -f "vendor/i2pd/darwin-arm64/i2pd" ]; then
  mkdir -p "dist/${APP_NAME}.app/Contents/Resources/${APP_NAME}/vendor/i2pd/darwin-arm64"
  cp "vendor/i2pd/darwin-arm64/i2pd" \
    "dist/${APP_NAME}.app/Contents/Resources/${APP_NAME}/vendor/i2pd/darwin-arm64/i2pd"
  chmod +x "dist/${APP_NAME}.app/Contents/Resources/${APP_NAME}/vendor/i2pd/darwin-arm64/i2pd"
fi
if [ -f "I2PChat.icns" ]; then
  cp "I2PChat.icns" "dist/${APP_NAME}.app/Contents/Resources/I2PChat.icns"
else
  echo "WARNING: I2PChat.icns not found, fallback to icon.png"
  cp "icon.png" "dist/${APP_NAME}.app/Contents/Resources/I2PChat.icns"
fi
printf '%s\n' '#!/bin/sh' "exec \"\$(dirname \"\$0\")/../Resources/${APP_NAME}/${APP_NAME}\" \"\$@\"" > "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
chmod +x "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"

# Info.plist
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
	<key>LSMinimumSystemVersion</key>
	<string>10.13</string>
</dict>
</plist>
PLIST

echo
echo "✔ GUI собран: dist/${APP_NAME}.app (${ARCH_SUFFIX})"

ZIP_FILE="I2PChat-macOS-${ARCH_SUFFIX}-v${RELEASE_VERSION}.zip"
rm -f "${ZIP_FILE}"
ZIP_STAGE="dist/${APP_NAME}-macOS-${ARCH_SUFFIX}-bundle"
rm -rf "${ZIP_STAGE}"
mkdir -p "${ZIP_STAGE}"
cp -R "dist/${APP_NAME}.app" "${ZIP_STAGE}/"
if [ -f "${BLINDBOX_INSTALL_SRC}" ]; then
  cp "${BLINDBOX_INSTALL_SRC}" "${ZIP_STAGE}/install.sh"
fi
ditto -c -k --sequesterRsrc --keepParent "${ZIP_STAGE}" "${ZIP_FILE}"
rm -rf "${ZIP_STAGE}"
echo "✔ Packed ${ZIP_FILE}"

# Release integrity artifacts: SHA256SUMS + detached GPG signature (SHA256SUMS.asc)
SHA256_FILE="SHA256SUMS"
"${PYTHON_CMD}" - "${ZIP_FILE}" "${SHA256_FILE}" <<'PY'
import hashlib
import os
import sys

artifact, checksums = sys.argv[1], sys.argv[2]
h = hashlib.sha256()
with open(artifact, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
with open(checksums, "w", encoding="utf-8") as out:
    out.write(f"{h.hexdigest()}  {os.path.basename(artifact)}\n")
PY
echo "✔ Generated ${SHA256_FILE}"

if [ "${I2PCHAT_SKIP_GPG_SIGN:-0}" = "1" ]; then
  echo "⚠ Skipping GPG detached signature (I2PCHAT_SKIP_GPG_SIGN=1)"
elif ! command -v gpg >/dev/null 2>&1; then
  if [ "${I2PCHAT_REQUIRE_GPG:-0}" = "1" ]; then
    echo "ERROR: gpg is required to create detached release signature" >&2
    exit 1
  fi
  echo "⚠ gpg not found; skipping detached signature (set I2PCHAT_REQUIRE_GPG=1 to enforce)"
else
  GPG_ARGS=(--batch --yes --armor --detach-sign --output "${SHA256_FILE}.asc")
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
    echo "⚠ gpg signing failed; continuing without detached signature"
  fi
fi
echo "  Можно перенести dist/${APP_NAME}.app в /Applications и запускать двойным кликом."

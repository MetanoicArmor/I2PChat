#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
VENV_DIR=".venv314"
RELEASE_VERSION="0.3.0"

# Определяем архитектуру
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ARCH_SUFFIX="x64" ;;
  arm64)  ARCH_SUFFIX="arm64" ;;
  *)      ARCH_SUFFIX="$ARCH" ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")"

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

echo "==> Устанавливаю/обновляю зависимости"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

echo "==> Проверяю PyNaCl (обязателен для secure protocol)"
python - <<'PY'
import sys
try:
    import nacl
    from nacl.secret import SecretBox  # noqa: F401
except Exception as exc:
    print(f"ERROR: PyNaCl is required for secure protocol build: {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"PyNaCl OK: {getattr(nacl, '__version__', 'unknown')}")
PY

echo "==> Проверяю синтаксис ключевых модулей"
python -m compileall i2p_chat_core.py crypto.py main_qt.py

echo "==> Собираю GUI (PyInstaller I2PChat.spec)"
rm -rf "dist/${APP_NAME}" "build/${APP_NAME}"
pyinstaller --clean -y I2PChat.spec

echo "==> Собираю I2PChat.app"
rm -rf "dist/${APP_NAME}.app"
mkdir -p "dist/${APP_NAME}.app/Contents/MacOS" "dist/${APP_NAME}.app/Contents/Resources"
cp -R "dist/${APP_NAME}" "dist/${APP_NAME}.app/Contents/Resources/${APP_NAME}"
cp icon-1024.png "dist/${APP_NAME}.app/Contents/Resources/I2PChat.icns"
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
echo "  Для релиза: zip -r I2PChat-macOS-${ARCH_SUFFIX}-v${RELEASE_VERSION}.zip dist/${APP_NAME}.app"
echo "  Можно перенести в /Applications и запускать двойным кликом."

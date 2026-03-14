#!/usr/bin/env bash
set -e

APP_NAME="I2PChat"
VENV_DIR=".venv314"

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
  echo "Сначала выполни: ./build-macos-app.sh (создаст venv) или python3.14 -m venv ${VENV_DIR} && pip install -r requirements.txt pyinstaller"
  exit 1
fi
source "${VENV_DIR}/bin/activate"

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
cat > "dist/${APP_NAME}.app/Contents/Info.plist" << 'PLIST'
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
	<string>0.1.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>10.13</string>
</dict>
</plist>
PLIST

echo
echo "✔ GUI собран: dist/${APP_NAME}.app (${ARCH_SUFFIX})"
echo "  Для релиза: zip -r I2PChat-macOS-${ARCH_SUFFIX}.zip dist/${APP_NAME}.app"
echo "  Можно перенести в /Applications и запускать двойным кликом."

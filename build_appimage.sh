#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
APPDIR="${APP_NAME}.AppDir"

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d ".venv" ]; then
  echo "Python venv .venv не найден. Сначала создайте его и установите зависимости."
  echo "Пример:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install --upgrade pip"
  echo "  pip install -r requirements.txt pillow pyinstaller"
  exit 1
fi

source .venv/bin/activate

# 1) сборка pyinstaller
pyinstaller --name "${APP_NAME}" \
  --windowed \
  --icon icon-1024.png \
  main_qt.py

# 2) подготовка AppDir
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" \
         "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/icons/hicolor/512x512/apps"

cp "dist/${APP_NAME}/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}"
cp icon-1024.png "${APPDIR}/usr/share/icons/hicolor/512x512/apps/i2pchat.png"

cat > "${APPDIR}/usr/share/applications/i2pchat.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=I2P Chat
Comment=Secure chat over I2P
Exec=${APP_NAME}
Icon=i2pchat
Terminal=false
Categories=Network;Chat;
EOF

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/I2PChat" "$@"
EOF

chmod +x "${APPDIR}/AppRun" "${APPDIR}/usr/bin/${APP_NAME}"

# 3) appimagetool
if [ ! -x appimagetool-x86_64.AppImage ]; then
  wget https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
  chmod +x appimagetool-x86_64.AppImage
fi

./appimagetool-x86_64.AppImage "${APPDIR}" "${APP_NAME}-x86_64.AppImage"
echo "Built ${APP_NAME}-x86_64.AppImage"


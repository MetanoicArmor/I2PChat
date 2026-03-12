#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
APPDIR="${APP_NAME}.AppDir"
VENV_DIR=".venv314"

cd "$(dirname "${BASH_SOURCE[0]}")"

if command -v python3.14 >/dev/null 2>&1; then
  PYTHON_BIN="python3.14"
else
  PYTHON_BIN="python3"
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "Создаю виртуальное окружение ${VENV_DIR} на базе ${PYTHON_BIN}..."
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

# гарантируем, что в окружении есть нужные зависимости
pip install --upgrade pip
pip install -r requirements.txt pillow pyinstaller

# 1) сборка PyInstaller под актуальный Python
pyinstaller -y --name "${APP_NAME}" \
  --windowed \
  --icon icon-1024.png \
  main_qt.py

# 2) упаковка в AppDir
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" \
         "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/icons/hicolor/512x512/apps"

# Кладём внутрь AppDir бинарник и каталог _internal (с libpython и всеми зависимостями)
cp "dist/${APP_NAME}/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}"
cp -r "dist/${APP_NAME}/_internal" "${APPDIR}/usr/bin/_internal"

# Добрасываем libcrypt, если он есть в системе, чтобы не требовать его снаружи
for CAND in /usr/lib/libcrypt.so.2 /lib64/libcrypt.so.2 /lib/libcrypt.so.2; do
  if [ -f "$CAND" ]; then
    cp "$CAND" "${APPDIR}/usr/bin/_internal/"
    break
  fi
done
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

# копия .desktop и иконки в корень AppDir, чтобы appimagetool их увидел
cp "${APPDIR}/usr/share/applications/i2pchat.desktop" "${APPDIR}/i2pchat.desktop"
cp icon-1024.png "${APPDIR}/i2pchat.png"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# Добавляем наш _internal в путь поиска библиотек,
# чтобы подхватывались libpython и, например, libcrypt.so.2
export LD_LIBRARY_PATH="$HERE/usr/bin/_internal:${LD_LIBRARY_PATH:-}"
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


#!/usr/bin/env bash
set -euo pipefail

APP_NAME="I2PChat"
APPDIR="${APP_NAME}.AppDir"
VENV_DIR=".venv314"
APPIMAGETOOL_VERSION="1.9.1"
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
  x86_64)  ARCH_SUFFIX="x86_64" ;;
  aarch64) ARCH_SUFFIX="aarch64" ;;
  armv7l)  ARCH_SUFFIX="armhf" ;;
  *)       ARCH_SUFFIX="$ARCH" ;;
esac

echo "==> Building for architecture: ${ARCH_SUFFIX}"

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

pip install --require-hashes -r requirements.txt
pip install --require-hashes -r requirements-build.txt

# Security gate: secure protocol requires PyNaCl
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

# Быстрая проверка синтаксиса пакетов и вспомогательных скриптов (без glob *.py в корне)
python -m compileall i2pchat vendor/i2plib scripts make_icon.py

# 1) сборка PyInstaller с использованием spec файла (анализирует i2pchat/run_gui.py и зависимости)
rm -rf "dist/${APP_NAME}" "build/${APP_NAME}"
pyinstaller --clean -y I2PChat.spec

# 2) упаковка в AppDir
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" \
         "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/icons/hicolor/512x512/apps"

# Кладём внутрь AppDir бинарник и каталог _internal (с libpython и всеми зависимостями)
cp "dist/${APP_NAME}/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}"
cp -r "dist/${APP_NAME}/_internal" "${APPDIR}/usr/bin/_internal"
if [ -d "dist/${APP_NAME}/vendor" ]; then
  cp -r "dist/${APP_NAME}/vendor" "${APPDIR}/usr/bin/vendor"
fi
if [ -f "${APPDIR}/usr/bin/vendor/i2pd/linux-x86_64/i2pd" ]; then
  chmod +x "${APPDIR}/usr/bin/vendor/i2pd/linux-x86_64/i2pd"
fi

# Добрасываем libcrypt, если он есть в системе, чтобы не требовать его снаружи
for CAND in /usr/lib/libcrypt.so.2 /lib64/libcrypt.so.2 /lib/libcrypt.so.2; do
  if [ -f "$CAND" ]; then
    cp "$CAND" "${APPDIR}/usr/bin/_internal/"
    break
  fi
done
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

# копия .desktop и иконки в корень AppDir, чтобы appimagetool их увидел
cp "${APPDIR}/usr/share/applications/i2pchat.desktop" "${APPDIR}/i2pchat.desktop"
cp icon.png "${APPDIR}/i2pchat.png"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# Добавляем наш _internal в путь поиска библиотек,
# чтобы подхватывались libpython и, например, libcrypt.so.2
export LD_LIBRARY_PATH="$HERE/usr/bin/_internal:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/I2PChat" "$@"
EOF

chmod +x "${APPDIR}/AppRun" "${APPDIR}/usr/bin/${APP_NAME}"

# 3) appimagetool (pinned release + SHA256 verification)
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
ACTUAL_SHA256="$(python - "$APPIMAGETOOL" <<'PY'
import hashlib
import sys
path = sys.argv[1]
h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
)"
if [ "${ACTUAL_SHA256}" != "${APPIMAGETOOL_SHA256}" ]; then
  echo "⚠ SHA256 mismatch for existing ${APPIMAGETOOL}, re-downloading pinned version..." >&2
  echo "Expected: ${APPIMAGETOOL_SHA256}" >&2
  echo "Actual:   ${ACTUAL_SHA256}" >&2
  rm -f "${APPIMAGETOOL}"
  wget "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/${APPIMAGETOOL}"
  ACTUAL_SHA256="$(python - "$APPIMAGETOOL" <<'PY'
import hashlib
import sys
path = sys.argv[1]
h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
)"
  if [ "${ACTUAL_SHA256}" != "${APPIMAGETOOL_SHA256}" ]; then
    echo "ERROR: SHA256 mismatch for downloaded ${APPIMAGETOOL}" >&2
    echo "Expected: ${APPIMAGETOOL_SHA256}" >&2
    echo "Actual:   ${ACTUAL_SHA256}" >&2
    exit 1
  fi
fi
chmod +x "$APPIMAGETOOL"

# Писать только в dist/ с версией в имени: перезапись ./I2PChat.AppImage в корне
# даёт ETXTBSY («Text file busy»), если этот AppImage сейчас запущен или смонтирован.
mkdir -p "dist"
OUTPUT_FILE="dist/${APP_NAME}-linux-${ARCH_SUFFIX}-v${RELEASE_VERSION}.AppImage"
./"$APPIMAGETOOL" "${APPDIR}" "$OUTPUT_FILE"
echo "✔ Built ${OUTPUT_FILE}"

ROOT_APPIMAGE="${APP_NAME}.AppImage"
if [ -e "$ROOT_APPIMAGE" ] || [ -L "$ROOT_APPIMAGE" ]; then
  if cp -f "$OUTPUT_FILE" "$ROOT_APPIMAGE" 2>/dev/null; then
    echo "✔ Updated ${ROOT_APPIMAGE} (copy from dist; close running AppImage if copy ever fails)"
  else
    echo "⚠ Skipped ${ROOT_APPIMAGE}: file busy or not writable (artifact is ${OUTPUT_FILE})" >&2
  fi
else
  if cp "$OUTPUT_FILE" "$ROOT_APPIMAGE" 2>/dev/null; then
    echo "✔ Created ${ROOT_APPIMAGE}"
  fi
fi

# 4) архив для релиза: версия + архитектура в имени zip
ZIP_FILE="${APP_NAME}-linux-${ARCH_SUFFIX}-v${RELEASE_VERSION}.zip"
rm -f "${ZIP_FILE}"
python - "${OUTPUT_FILE}" "${ZIP_FILE}" "${BLINDBOX_INSTALL_SRC}" <<'PY'
import os
import sys
import zipfile

src, dst, install_src = sys.argv[1], sys.argv[2], sys.argv[3]
with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(src, arcname=os.path.basename(src))
    if os.path.isfile(install_src):
        zf.write(install_src, arcname="install.sh")
PY
echo "✔ Packed ${ZIP_FILE}"

# 5) release integrity artifacts: SHA256SUMS + detached GPG signature (SHA256SUMS.asc)
SHA256_FILE="SHA256SUMS"
python - "${ZIP_FILE}" "${SHA256_FILE}" <<'PY'
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

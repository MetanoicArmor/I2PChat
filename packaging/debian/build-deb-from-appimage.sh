#!/usr/bin/env bash
# Build a minimal amd64 .deb from the official Linux release zip (AppImage inside).
# Usage: from repo root — ./packaging/debian/build-deb-from-appimage.sh [version]
# Default version: first line of VERSION file in repo root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VER="${1:-}"
if [[ -z "$VER" ]]; then
  VER="$(tr -d '\r\n' < VERSION)"
fi

if [[ -z "$VER" ]]; then
  echo "ERROR: empty version" >&2
  exit 1
fi

# CI sets GITHUB_REPOSITORY; local default matches upstream (override with I2PCHAT_RELEASE_REPO=owner/name).
REPO="${I2PCHAT_RELEASE_REPO:-${GITHUB_REPOSITORY:-MetanoicArmor/I2PChat}}"
TAG_REF="v${VER}"
ZIP_NAME="I2PChat-linux-x86_64-v${VER}.zip"
ZIP_URL="https://github.com/${REPO}/releases/download/${TAG_REF}/${ZIP_NAME}"
ICON_URL="https://github.com/${REPO}/raw/${TAG_REF}/icon.png"

# In CI we retry longer (GitHub can list an asset before cdn download returns 200). Locally default is shorter.
_default_zip_attempts() {
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then echo 36; else echo 8; fi
}
ZIP_ATTEMPTS="${I2PCHAT_ZIP_DOWNLOAD_ATTEMPTS:-$(_default_zip_attempts)}"

curl_retry() {
  local url="$1" dest="$2" attempts="${3:-36}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsSL --connect-timeout 30 --max-time 900 -o "$dest" "$url"; then
      return 0
    fi
    echo "WARN: download failed (${i}/${attempts}): ${url}" >&2
    if ((i < attempts)); then
      sleep 10
    fi
  done
  echo "ERROR: could not download after ${attempts} tries (HTTP 404 often means the Linux zip is not on the release yet, or wrong repo/tag). Repo=${REPO} tag=${TAG_REF} file=${ZIP_NAME}" >&2
  return 1
}

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "==> Downloading ${ZIP_URL}"
curl_retry "$ZIP_URL" "$WORKDIR/${ZIP_NAME}" "$ZIP_ATTEMPTS"
curl_retry "$ICON_URL" "$WORKDIR/icon.png" 12

echo "==> Extracting AppImage"
unzip -q "$WORKDIR/${ZIP_NAME}" -d "$WORKDIR/stage"
# Release zip contains one file: I2PChat-linux-<arch>-v<ver>.AppImage (see build-linux.sh)
APPIMAGE="$WORKDIR/stage/I2PChat-linux-x86_64-v${VER}.AppImage"
if [[ ! -f "$APPIMAGE" ]]; then
  APPIMAGE="$(find "$WORKDIR/stage" -maxdepth 1 -name '*.AppImage' -print -quit)"
fi
if [[ -z "$APPIMAGE" || ! -f "$APPIMAGE" ]]; then
  echo "ERROR: no .AppImage found inside zip" >&2
  exit 1
fi
chmod +x "$APPIMAGE"

PKG_ROOT="$WORKDIR/pkg"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/i2pchat"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"
mkdir -p "$PKG_ROOT/usr/share/pixmaps"

cp "$APPIMAGE" "$PKG_ROOT/opt/i2pchat/I2PChat.AppImage"
cp "$WORKDIR/icon.png" "$PKG_ROOT/usr/share/pixmaps/i2pchat.png"

ln -sf /opt/i2pchat/I2PChat.AppImage "$PKG_ROOT/usr/bin/i2pchat"

cat > "$PKG_ROOT/usr/share/applications/i2pchat.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=I2P Chat
Comment=Secure chat over I2P
Exec=/usr/bin/i2pchat %u
Icon=i2pchat
Terminal=false
Categories=Network;Chat;
EOF

INSTALLED_BYTES="$(du -sk "$PKG_ROOT" | cut -f1)"
INSTALLED_KB="$((INSTALLED_BYTES + 8))"

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: i2pchat
Version: ${VER}-1
Section: net
Priority: optional
Architecture: amd64
Maintainer: MetanoicArmor <https://github.com/MetanoicArmor/I2PChat>
Homepage: https://github.com/MetanoicArmor/I2PChat
Depends: zlib1g
Description: Experimental peer-to-peer chat client for I2P
 Bundled AppImage from upstream GitHub releases; GUI chat over I2P (PyQt6).
Installed-Size: ${INSTALLED_KB}
EOF

mkdir -p dist
DEB_OUT="dist/i2pchat_${VER}_amd64.deb"
rm -f "$DEB_OUT"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$DEB_OUT"
echo "✔ Built ${DEB_OUT}"

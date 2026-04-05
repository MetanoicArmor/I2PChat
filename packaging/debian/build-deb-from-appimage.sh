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

ZIP_NAME="I2PChat-linux-x86_64-v${VER}.zip"
ZIP_URL="https://github.com/MetanoicArmor/I2PChat/releases/download/v${VER}/${ZIP_NAME}"
ICON_URL="https://github.com/MetanoicArmor/I2PChat/raw/v${VER}/icon.png"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "==> Downloading ${ZIP_URL}"
curl -fsSL -o "$WORKDIR/${ZIP_NAME}" "$ZIP_URL"
curl -fsSL -o "$WORKDIR/icon.png" "$ICON_URL"

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

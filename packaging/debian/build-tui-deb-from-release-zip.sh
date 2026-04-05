#!/usr/bin/env bash
# Build a minimal .deb from the official Linux TUI release zip (PyInstaller onedir layout).
# I2PCHAT_DEB_ARCH=amd64 (default) or arm64 — см. build-deb-from-appimage.sh.
# Usage: from repo root — ./packaging/debian/build-tui-deb-from-release-zip.sh [version]
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

DEB_ARCH="${I2PCHAT_DEB_ARCH:-amd64}"
case "$DEB_ARCH" in
  amd64) LINUX_ZIP_ARCH="x86_64" ;;
  arm64) LINUX_ZIP_ARCH="aarch64" ;;
  *)
    echo "ERROR: I2PCHAT_DEB_ARCH must be amd64 or arm64, got: ${DEB_ARCH}" >&2
    exit 1
    ;;
esac

REPO="${I2PCHAT_RELEASE_REPO:-${GITHUB_REPOSITORY:-MetanoicArmor/I2PChat}}"
TAG_REF="v${VER}"
ZIP_NAME="I2PChat-linux-${LINUX_ZIP_ARCH}-tui-v${VER}.zip"
ZIP_URL="https://github.com/${REPO}/releases/download/${TAG_REF}/${ZIP_NAME}"
ICON_URL="https://github.com/${REPO}/raw/${TAG_REF}/icon.png"

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
  echo "ERROR: could not download after ${attempts} tries: ${url}" >&2
  return 1
}

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "==> Downloading ${ZIP_URL}"
curl_retry "$ZIP_URL" "$WORKDIR/${ZIP_NAME}" "$ZIP_ATTEMPTS"
curl_retry "$ICON_URL" "$WORKDIR/icon.png" 12

echo "==> Extracting TUI zip"
unzip -q "$WORKDIR/${ZIP_NAME}" -d "$WORKDIR/stage"
# Official zip (build-linux.sh): launcher script i2pchat-tui + usr/bin/... at archive root — not i2pchat-tui/
if [[ ! -e "$WORKDIR/stage/i2pchat-tui" || ! -d "$WORKDIR/stage/usr" ]]; then
  echo "ERROR: expected i2pchat-tui (file or dir) and usr/ at top level of ${ZIP_NAME}" >&2
  echo "Top-level entries:" >&2
  ls -la "$WORKDIR/stage" >&2 || true
  exit 1
fi

PKG_ROOT="$WORKDIR/pkg"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/i2pchat-tui"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"
mkdir -p "$PKG_ROOT/usr/share/pixmaps"

cp -a "$WORKDIR/stage/i2pchat-tui" "$WORKDIR/stage/usr" "$PKG_ROOT/opt/i2pchat-tui/"
chmod +x "$PKG_ROOT/opt/i2pchat-tui/i2pchat-tui" \
  "$PKG_ROOT/opt/i2pchat-tui/usr/bin/I2PChat-tui"

ln -sf /opt/i2pchat-tui/i2pchat-tui "$PKG_ROOT/usr/bin/i2pchat-tui"
cp "$WORKDIR/icon.png" "$PKG_ROOT/usr/share/pixmaps/i2pchat-tui.png"

cat > "$PKG_ROOT/usr/share/applications/i2pchat-tui.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=I2P Chat (TUI)
Comment=I2PChat terminal UI
Exec=i2pchat-tui
Icon=i2pchat-tui
Terminal=true
Categories=Network;Chat;
EOF

INSTALLED_BYTES="$(du -sk "$PKG_ROOT" | cut -f1)"
INSTALLED_KB="$((INSTALLED_BYTES + 8))"

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: i2pchat-tui
Version: ${VER}-1
Section: net
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: MetanoicArmor <https://github.com/MetanoicArmor/I2PChat>
Homepage: https://github.com/MetanoicArmor/I2PChat
Depends: zlib1g
Description: I2PChat Textual terminal UI (official Linux TUI zip)
 PyInstaller bundle from upstream GitHub releases; no PyQt6.
Installed-Size: ${INSTALLED_KB}
EOF

mkdir -p dist
DEB_OUT="dist/i2pchat-tui_${VER}_${DEB_ARCH}.deb"
rm -f "$DEB_OUT"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$DEB_OUT"
echo "✔ Built ${DEB_OUT}"

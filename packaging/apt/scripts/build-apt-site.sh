#!/usr/bin/env bash
# Build a static apt repository under packaging/apt/site (dists/ + pool/).
#
# Usage:
#   VERSION=1.2.3 DEB_PATH=/path/gui_amd64.deb DEB_PATH_2=/path/tui_amd64.deb ./packaging/apt/scripts/build-apt-site.sh
#
# Optional (multi-arch mirror — same pool, separate Packages per arch):
#   DEB_ARM64_PATH=/path/gui_arm64.deb DEB_ARM64_PATH_2=/path/tui_arm64.deb
#
# Optional APT_DEB_FILENAME_URL: rewrite Packages "Filename:" (see original comment).
# APT_DEB_FILENAME_URL is not supported together with DEB_PATH_2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="${VERSION:-}"
DEB="${DEB_PATH:-}"
DEB2="${DEB_PATH_2:-}"
DEBA="${DEB_ARM64_PATH:-}"
DEBA2="${DEB_ARM64_PATH_2:-}"

if [[ -z "$VER" || -z "$DEB" ]]; then
  echo "Usage: VERSION=x.y.z DEB_PATH=/abs/gui_amd64.deb [DEB_PATH_2=/abs/tui_amd64.deb] [DEB_ARM64_PATH= ... DEB_ARM64_PATH_2= ...] $0" >&2
  exit 1
fi
if [[ ! -f "$DEB" ]]; then
  echo "ERROR: .deb not found: $DEB" >&2
  exit 1
fi
if [[ -n "$DEB2" && ! -f "$DEB2" ]]; then
  echo "ERROR: DEB_PATH_2 set but file missing: $DEB2" >&2
  exit 1
fi
if [[ -n "${APT_DEB_FILENAME_URL:-}" && -n "$DEB2" ]]; then
  echo "ERROR: APT_DEB_FILENAME_URL cannot be used with DEB_PATH_2 (multiple packages)" >&2
  exit 1
fi

HAVE_ARM64=0
if [[ -n "$DEBA" && -n "$DEBA2" && -f "$DEBA" && -f "$DEBA2" ]]; then
  HAVE_ARM64=1
elif [[ -n "$DEBA" || -n "$DEBA2" ]]; then
  echo "ERROR: set both DEB_ARM64_PATH and DEB_ARM64_PATH_2 or omit both" >&2
  exit 1
fi

SITE="${SITE_DIR:-$ROOT/site}"
rm -rf "$SITE"
mkdir -p "$SITE/dists/stable/main/binary-amd64"

SCAN_ROOT="$(mktemp -d)"
cleanup() { rm -rf "$SCAN_ROOT"; }
trap cleanup EXIT

mkdir -p "$SCAN_ROOT/pool/main"
cp -f "$DEB" "$SCAN_ROOT/pool/main/"
if [[ -n "$DEB2" ]]; then
  cp -f "$DEB2" "$SCAN_ROOT/pool/main/"
fi

(
  cd "$SCAN_ROOT"
  dpkg-scanpackages pool/main /dev/null
) > "$SITE/dists/stable/main/binary-amd64/Packages"

if [[ -n "${APT_DEB_FILENAME_URL:-}" ]]; then
  sed -i "s#^Filename:.*#Filename: ${APT_DEB_FILENAME_URL}#" \
    "$SITE/dists/stable/main/binary-amd64/Packages"
  echo "Using remote Filename (no .deb in site/): ${APT_DEB_FILENAME_URL}"
else
  mkdir -p "$SITE/pool/main"
  cp -f "$SCAN_ROOT/pool/main"/* "$SITE/pool/main/"
  echo "Copied $(find "$SITE/pool/main" -maxdepth 1 -name '*.deb' | wc -l) .deb file(s) into site/pool/main/ (amd64 stage)."
fi

gzip -9kf "$SITE/dists/stable/main/binary-amd64/Packages"

ARCH_LIST="amd64"

if [[ "$HAVE_ARM64" -eq 1 ]]; then
  mkdir -p "$SITE/dists/stable/main/binary-arm64"
  TMP_ARM="$(mktemp -d)"
  mkdir -p "$TMP_ARM/pool/main"
  cp -f "$DEBA" "$DEBA2" "$TMP_ARM/pool/main"
  (
    cd "$TMP_ARM"
    dpkg-scanpackages pool/main /dev/null
  ) > "$SITE/dists/stable/main/binary-arm64/Packages"
  gzip -9kf "$SITE/dists/stable/main/binary-arm64/Packages"
  rm -rf "$TMP_ARM"
  if [[ -z "${APT_DEB_FILENAME_URL:-}" ]]; then
    cp -f "$DEBA" "$DEBA2" "$SITE/pool/main/"
    echo "Added arm64 .deb files to site/pool/main/."
  fi
  ARCH_LIST="amd64 arm64"
fi

CONF_TMP="$(mktemp)"
{
  echo 'APT::FTPArchive::Release::Origin "I2PChat";'
  echo 'APT::FTPArchive::Release::Label "I2PChat";'
  echo 'APT::FTPArchive::Release::Suite "stable";'
  echo 'APT::FTPArchive::Release::Codename "stable";'
  echo "APT::FTPArchive::Release::Architectures \"${ARCH_LIST}\";"
  echo 'APT::FTPArchive::Release::Components "main";'
  echo 'APT::FTPArchive::Release::Description "Unofficial apt mirror for I2PChat (.deb from GitHub Releases)";'
} > "$CONF_TMP"
apt-ftparchive -c="$CONF_TMP" release "$SITE/dists/stable" > "$SITE/dists/stable/Release"
rm -f "$CONF_TMP"

echo "Built unsigned tree under $SITE (architectures: ${ARCH_LIST}; sign Release with GPG next)."

#!/usr/bin/env bash
# Build a static apt repository under packaging/apt/site (dists/ + optional pool/).
#
# Usage:
#   VERSION=1.2.3 DEB_PATH=/path/to.deb ./packaging/apt/scripts/build-apt-site.sh
#
# Optional APT_DEB_FILENAME_URL: rewrite Packages "Filename:" to this URL and skip copying .deb
# into site/. WARNING: stock apt resolves Filename relative to the repo base URL and will break
# (e.g. base + https://github.com/...). Use only with a client/tool that supports absolute URLs,
# or prefer publishing the full site (pool/ + .deb) via GitHub Actions → Pages artifact.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="${VERSION:-}"
DEB="${DEB_PATH:-}"

if [[ -z "$VER" || -z "$DEB" ]]; then
  echo "Usage: VERSION=x.y.z DEB_PATH=/abs/path/to.deb [APT_DEB_FILENAME_URL=https://...] $0" >&2
  exit 1
fi
if [[ ! -f "$DEB" ]]; then
  echo "ERROR: .deb not found: $DEB" >&2
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

(
  cd "$SCAN_ROOT"
  dpkg-scanpackages pool/main /dev/null
) > "$SITE/dists/stable/main/binary-amd64/Packages"

if [[ -n "${APT_DEB_FILENAME_URL:-}" ]]; then
  # GNU sed (Linux / CI)
  sed -i "s#^Filename:.*#Filename: ${APT_DEB_FILENAME_URL}#" \
    "$SITE/dists/stable/main/binary-amd64/Packages"
  echo "Using remote Filename (no .deb in site/): ${APT_DEB_FILENAME_URL}"
else
  mkdir -p "$SITE/pool/main"
  cp -f "$DEB" "$SITE/pool/main/"
  echo "Copied .deb into site/pool/main/ (local mirror)."
fi

gzip -9kf "$SITE/dists/stable/main/binary-amd64/Packages"

CONF="$ROOT/config/apt-ftparchive-release.conf"
apt-ftparchive -c="$CONF" release "$SITE/dists/stable" > "$SITE/dists/stable/Release"

echo "Built unsigned tree under $SITE (sign Release with GPG next)."

#!/usr/bin/env bash
# Build a static apt repository under packaging/apt/site (dists/ + optional pool/).
#
# Usage:
#   VERSION=1.2.3 DEB_PATH=/path/gui.deb [DEB_PATH_2=/path/tui.deb] ./packaging/apt/scripts/build-apt-site.sh
#
# Optional APT_DEB_FILENAME_URL: rewrite Packages "Filename:" to this URL and skip copying .deb
# into site/. WARNING: stock apt resolves Filename relative to the repo base URL and will break
# (e.g. base + https://github.com/...). Use only with a client/tool that supports absolute URLs,
# or prefer publishing the full site (pool/ + .deb) via GitHub Actions → Pages artifact.
# APT_DEB_FILENAME_URL is not supported together with DEB_PATH_2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="${VERSION:-}"
DEB="${DEB_PATH:-}"
DEB2="${DEB_PATH_2:-}"

if [[ -z "$VER" || -z "$DEB" ]]; then
  echo "Usage: VERSION=x.y.z DEB_PATH=/abs/gui.deb [DEB_PATH_2=/abs/tui.deb] ... $0" >&2
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
  echo "Copied $(find "$SITE/pool/main" -maxdepth 1 -name '*.deb' | wc -l) .deb file(s) into site/pool/main/."
fi

gzip -9kf "$SITE/dists/stable/main/binary-amd64/Packages"

CONF="$ROOT/config/apt-ftparchive-release.conf"
apt-ftparchive -c="$CONF" release "$SITE/dists/stable" > "$SITE/dists/stable/Release"

echo "Built unsigned tree under $SITE (sign Release with GPG next)."

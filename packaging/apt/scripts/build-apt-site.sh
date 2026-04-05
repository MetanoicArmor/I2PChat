#!/usr/bin/env bash
# Build a static apt repository tree under packaging/apt/site (dists/ + pool/) from one .deb.
# Usage: VERSION=1.2.3 DEB_PATH=/path/to/i2pchat_1.2.3_amd64.deb ./packaging/apt/scripts/build-apt-site.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="${VERSION:-}"
DEB="${DEB_PATH:-}"

if [[ -z "$VER" || -z "$DEB" ]]; then
  echo "Usage: VERSION=x.y.z DEB_PATH=/abs/path/to.deb $0" >&2
  exit 1
fi
if [[ ! -f "$DEB" ]]; then
  echo "ERROR: .deb not found: $DEB" >&2
  exit 1
fi

SITE="${SITE_DIR:-$ROOT/site}"
rm -rf "$SITE"
mkdir -p "$SITE/pool/main"
mkdir -p "$SITE/dists/stable/main/binary-amd64"

cp -f "$DEB" "$SITE/pool/main/"

(
  cd "$SITE"
  dpkg-scanpackages pool/main /dev/null > dists/stable/main/binary-amd64/Packages
  gzip -9kf dists/stable/main/binary-amd64/Packages
)

CONF="$ROOT/config/apt-ftparchive-release.conf"
apt-ftparchive -c="$CONF" release "$SITE/dists/stable" > "$SITE/dists/stable/Release"

echo "Built unsigned tree under $SITE (sign Release with GPG next)."

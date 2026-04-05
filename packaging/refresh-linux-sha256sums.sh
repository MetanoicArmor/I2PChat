#!/usr/bin/env bash
# Regenerate Linux release SHA256SUMS (GUI zip + TUI zip) from GitHub assets.
# Same two-line format as build-linux.sh — use after replacing zips on a release without rebuilding locally.
#
# Usage (from repo root):
#   ./packaging/refresh-linux-sha256sums.sh [v]X.Y.Z
#   I2PCHAT_RELEASE_REPO=owner/repo ./packaging/refresh-linux-sha256sums.sh 1.2.3
#
# Writes dist/SHA256SUMS and prints it. Re-sign if you publish SHA256SUMS.asc:
#   gpg --batch --yes --armor --detach-sign --output dist/SHA256SUMS.asc dist/SHA256SUMS
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="${1:-}"
if [[ -z "$VER" ]]; then
  VER="$(tr -d '\r\n' < VERSION 2>/dev/null || true)"
fi
VER="${VER#v}"
if [[ -z "$VER" ]]; then
  echo "ERROR: pass version (e.g. 1.2.3) or ensure VERSION file exists" >&2
  exit 1
fi

REPO="${I2PCHAT_RELEASE_REPO:-MetanoicArmor/I2PChat}"
BASE="https://github.com/${REPO}/releases/download/v${VER}"
ZIP_GUI="I2PChat-linux-x86_64-v${VER}.zip"
ZIP_TUI="I2PChat-linux-x86_64-tui-v${VER}.zip"
OUT="${ROOT}/dist/SHA256SUMS"

mkdir -p "${ROOT}/dist"
rm -f "$OUT"

hash_one() {
  local url="$1" name="$2"
  local sum
  sum="$(curl -fsSL "$url" | sha256sum | awk '{print $1}')"
  printf '%s  %s\n' "$sum" "$name"
}

{
  hash_one "${BASE}/${ZIP_GUI}" "$ZIP_GUI"
  hash_one "${BASE}/${ZIP_TUI}" "$ZIP_TUI"
} | tee "$OUT"

echo "==> Wrote $OUT" >&2
echo "==> Upload: gh release upload v${VER} \"$OUT\" --clobber" >&2

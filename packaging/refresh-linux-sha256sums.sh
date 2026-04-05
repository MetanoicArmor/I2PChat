#!/usr/bin/env bash
# Regenerate Linux release checksum files from GitHub assets (same two-line format as build-linux.sh per arch).
# Use after adding/replacing zips on a release without rebuilding locally.
#
# Usage (from repo root):
#   ./packaging/refresh-linux-sha256sums.sh [v]X.Y.Z
#   I2PCHAT_RELEASE_REPO=owner/repo ./packaging/refresh-linux-sha256sums.sh 1.2.3
#
# Writes:
#   dist/SHA256SUMS — x86_64 GUI + TUI zip
#   dist/SHA256SUMS.linux-aarch64 — aarch64 GUI + TUI zip (if assets exist on the release)
#
# Re-sign if you publish *.asc:
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
ZIP_GUI_AARCH64="I2PChat-linux-aarch64-v${VER}.zip"
ZIP_TUI_AARCH64="I2PChat-linux-aarch64-tui-v${VER}.zip"
OUT="${ROOT}/dist/SHA256SUMS"
OUT_AARCH64="${ROOT}/dist/SHA256SUMS.linux-aarch64"

mkdir -p "${ROOT}/dist"
rm -f "$OUT" "$OUT_AARCH64"

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

_tmp_a="$(mktemp)"
if {
  hash_one "${BASE}/${ZIP_GUI_AARCH64}" "$ZIP_GUI_AARCH64"
  hash_one "${BASE}/${ZIP_TUI_AARCH64}" "$ZIP_TUI_AARCH64"
} >"$_tmp_a" 2>/dev/null; then
  mv -f "$_tmp_a" "$OUT_AARCH64"
  echo "==> Wrote $OUT_AARCH64" >&2
  echo "==> Upload: gh release upload v${VER} \"$OUT_AARCH64\" --clobber" >&2
else
  rm -f "$_tmp_a" "$OUT_AARCH64"
  echo "==> Пропуск ${OUT_AARCH64}: на релизе нет обоих aarch64 zip или ошибка curl" >&2
fi

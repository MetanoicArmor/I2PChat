#!/usr/bin/env bash
# Build x86_64 RPM from the official Linux release zip (AppImage inside), like the .deb script.
# Intended for: Fedora host, or CI (e.g. docker run -v "$PWD:/workspace" -w /workspace fedora:42 …).
# Usage: ./packaging/fedora/build-rpm-from-release.sh <version>
#   version: X.Y.Z (no leading v)
set -euo pipefail

VER="${1:?version X.Y.Z}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if ! command -v rpmbuild >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y rpm-build curl unzip
  else
    echo "ERROR: need rpmbuild (e.g. dnf install rpm-build) or run inside Fedora container" >&2
    exit 1
  fi
fi

REPO="${I2PCHAT_RELEASE_REPO:-${GITHUB_REPOSITORY:-MetanoicArmor/I2PChat}}"
TAG_REF="v${VER}"
ZIP_NAME="I2PChat-linux-x86_64-v${VER}.zip"
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
  echo "ERROR: could not download after ${attempts} tries. Repo=${REPO} tag=${TAG_REF}" >&2
  return 1
}

mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
SPEC_DST=~/rpmbuild/SPECS/i2pchat.spec
cp "$ROOT/packaging/fedora/i2pchat.spec" "$SPEC_DST"
sed -i "s/^Version:.*/Version:        ${VER}/" "$SPEC_DST"

curl_retry "$ZIP_URL" ~/rpmbuild/SOURCES/"${ZIP_NAME}" "$ZIP_ATTEMPTS"
curl_retry "$ICON_URL" ~/rpmbuild/SOURCES/icon.png 12

rpmbuild -ba "$SPEC_DST"

mkdir -p "$ROOT/dist"
RPM_FILE="$(find ~/rpmbuild/RPMS/x86_64 -maxdepth 1 -name "i2pchat-${VER}-*.rpm" -type f -print -quit)"
if [[ -z "$RPM_FILE" || ! -f "$RPM_FILE" ]]; then
  echo "ERROR: no binary RPM under ~/rpmbuild/RPMS/x86_64" >&2
  exit 1
fi
# Stable asset name for GitHub Releases (parallel to dist/i2pchat_${VER}_amd64.deb)
STABLE="dist/i2pchat_${VER}_x86_64.rpm"
rm -f "$STABLE"
cp "$RPM_FILE" "$STABLE"
echo "✔ Built ${STABLE}"

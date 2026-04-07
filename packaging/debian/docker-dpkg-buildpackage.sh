#!/usr/bin/env bash
# Build Debian binary packages in a clean container (Docker on macOS ARM or Linux).
# Usage: from repo root — ./packaging/debian/docker-dpkg-buildpackage.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Default debian:sid: archive i2pd satisfies python3-i2pchat Depends (>= 2.59.0~).
# Ubuntu 24.04 ships i2pd 2.49 — use DEBIAN_BUILD_IMAGE=ubuntu:24.04 only if you relax Depends.
IMAGE="${DEBIAN_BUILD_IMAGE:-debian:sid}"
docker run --rm -i -v "${ROOT}:/src:rw" -w /src "${IMAGE}" bash -s <<'EOS'
set -euo pipefail
shopt -s nullglob
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  autopkgtest \
  build-essential \
  debhelper \
  devscripts \
  dh-python \
  fakeroot \
  lintian \
  pybuild-plugin-pyproject \
  python3-all \
  python3-hatchling

# Full source+binary (native tarball) so lintian sees the same tree as dpkg-source -b.
dpkg-buildpackage -us -uc

mkdir -p debian-ci-out
rm -f debian-ci-out/*
ver="$(dpkg-parsechangelog -l debian/changelog -SVersion)"
arch="$(dpkg-architecture -qDEB_BUILD_ARCH)"
stage=(
  "../i2pchat_${ver}.dsc"
  "../i2pchat_${ver}.tar.xz"
  "../i2pchat_${ver}_${arch}.buildinfo"
  "../i2pchat_${ver}_${arch}.changes"
  "../python3-i2pchat_${ver}_all.deb"
  "../i2pchat_${ver}_all.deb"
  "../i2pchat-tui_${ver}_all.deb"
)
for f in "${stage[@]}"; do
  if [ ! -e "$f" ]; then
    echo "missing build artifact: $f" >&2
    exit 1
  fi
  cp "$f" debian-ci-out/
done
ls -la debian-ci-out/

ch=(debian-ci-out/*.changes)
if [ "${#ch[@]}" -ne 1 ]; then
  echo "Expected one .changes in debian-ci-out, got ${#ch[@]}" >&2
  exit 1
fi

set +e
lintian -E "${ch[0]}"
lint_ec=$?
set -e
# 0 ok; 1 overridden; 4/8 bitmask (warnings/info) on some lintian builds
if [ "$lint_ec" -ne 0 ] && [ "$lint_ec" -ne 1 ] && [ "$lint_ec" -ne 4 ] && [ "$lint_ec" -ne 8 ]; then
  echo "lintian -E failed with exit $lint_ec" >&2
  exit "$lint_ec"
fi

set +o pipefail
set +e
autopkgtest "${ch[0]}" -- null 2>&1 | tee /tmp/autopkgtest.log
set -e
set -o pipefail
if grep -E '^[a-zA-Z0-9_.-]+[[:space:]]+FAIL' /tmp/autopkgtest.log; then
  echo "autopkgtest reported FAIL" >&2
  exit 2
fi

debs=(debian-ci-out/*.deb)
if [ "${#debs[@]}" -lt 1 ]; then
  echo "No .deb in debian-ci-out" >&2
  exit 1
fi
# apt requires ./ prefix for local .deb paths
apt_debs=()
for d in "${debs[@]}"; do apt_debs+=("./$d"); done
apt-get install --reinstall -y -qq "${apt_debs[@]}"

marker=/usr/share/i2pchat/system-router-only
if [ ! -f "$marker" ]; then
  echo "missing $marker" >&2
  ls -la /usr/share/i2pchat/ >&2 || true
  exit 1
fi
grep -q "system i2pd" "$marker"
if dpkg -L python3-i2pchat | grep -E "(^|/)i2pd$"; then echo "bundled i2pd leak"; exit 1; fi

echo "Artifacts (also under debian-ci-out/):" && ls -la debian-ci-out/
echo OK
EOS

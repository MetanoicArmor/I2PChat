#!/usr/bin/env bash
# Build Debian binary packages in a clean container (Docker on macOS ARM or Linux).
# Usage: from repo root — ./packaging/debian/docker-dpkg-buildpackage.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Match CI: Ubuntu 24.04 has Python 3.12 (see pyproject.toml requires-python).
# For Debian stable with older Python only, expect build/runtime skew.
IMAGE="${DEBIAN_BUILD_IMAGE:-ubuntu:24.04}"
docker run --rm -i -v "${ROOT}:/src:rw" -w /src "${IMAGE}" bash -s <<'EOS'
set -euo pipefail
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
dpkg-buildpackage -us -uc -b
lintian -E ../*.changes
autopkgtest ../*.changes -- null
apt-get install -y -qq ../*.deb
test -f /usr/share/doc/python3-i2pchat/system-router-only
grep -q "system i2pd" /usr/share/doc/python3-i2pchat/system-router-only
if dpkg -L python3-i2pchat | grep -E "(^|/)i2pd$"; then echo "bundled i2pd leak"; exit 1; fi
echo "Artifacts:" && ls -la ../*.deb ../*.changes
EOS

#!/usr/bin/env bash
# Copy missing Linux shared libraries next to vendor/i2pd/*/i2pd so bundled
# i2pd runs under uv/dev and inside PyInstaller/AppImage (with LD_LIBRARY_PATH).
#
# Typical case: i2pd was linked against libboost_program_options.so.1.89.0 but
# the host only has 1.90 — we ship 1.90 and add a symlink under the 1.89.0 name.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) SUB=linux-x86_64 ;;
  aarch64) SUB=linux-aarch64 ;;
  *)
    echo "Unsupported machine: $ARCH" >&2
    exit 1
    ;;
esac

DIR="${ROOT}/vendor/i2pd/${SUB}"
I2PD="${DIR}/i2pd"

if [[ ! -f "$I2PD" ]]; then
  echo "No bundled i2pd at ${I2PD} — run ensure_bundled_i2pd.sh / fetch_bundled_i2pd.sh first." >&2
  exit 1
fi

copy_boost_from_host() {
  local libdir f base
  shopt -s nullglob
  for libdir in /usr/lib /usr/lib64; do
    [[ -d "$libdir" ]] || continue
    for f in "${libdir}"/libboost_program_options.so.[0-9]* "${libdir}"/libboost_container.so.[0-9]*; do
      [[ -f "$f" ]] || continue
      base="$(basename "$f")"
      [[ -e "${DIR}/${base}" ]] && continue
      cp -L "$f" "${DIR}/"
      echo "Copied ${base}"
    done
  done
  shopt -u nullglob
}

copy_boost_from_host

# Symlink any DT_NEEDED *.so.* that still resolve to "not found" but we have a newer minor .so
while IFS= read -r needed; do
  [[ -n "$needed" ]] || continue
  base="${needed%.so.*}"
  if [[ -f "${DIR}/${needed}" ]]; then
    continue
  fi
  cand="$(ls -1 "${DIR}/${base}.so."* 2>/dev/null | head -1 || true)"
  if [[ -n "$cand" && -f "$cand" ]]; then
    ln -sf "$(basename "$cand")" "${DIR}/${needed}"
    echo "Symlink ${needed} -> $(basename "$cand")"
  fi
done < <(objdump -p "$I2PD" 2>/dev/null | awk '/NEEDED/ {print $2}' | grep -E '^libboost_' || true)

if LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep -q 'not found'; then
  echo "WARN: ldd still reports missing libs — install deps or copy .so manually:" >&2
  LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep 'not found' >&2 || true
  exit 1
fi

echo "OK: ${I2PD} resolves all dynamic deps with LD_LIBRARY_PATH=${DIR}"

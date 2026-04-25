#!/usr/bin/env bash
# Copy host libboost_*.so.* next to vendor/i2pd/*/i2pd so bundled i2pd can run
# under uv/dev and inside PyInstaller/AppImage (LD_LIBRARY_PATH includes that dir).
#
# Only Boost is handled here — never copy glibc/libstdc++ into vendor.
# Exact SONAME from DT_NEEDED is copied from the system when present; we do not
# symlink one Boost SONAME to another (C++ ABI mismatch).
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
    for f in "${libdir}"/libboost_program_options.so.[0-9]* "${libdir}"/libboost_container.so.[0-9]* \
      "${libdir}"/libboost_system.so.[0-9]* "${libdir}"/libboost_filesystem.so.[0-9]*; do
      [[ -f "$f" ]] || continue
      base="$(basename "$f")"
      [[ -e "${DIR}/${base}" ]] && continue
      cp -L "$f" "${DIR}/"
      echo "Copied ${base}"
    done
  done
  for f in /usr/lib/x86_64-linux-gnu/libboost_*.so.[0-9]* /usr/lib/aarch64-linux-gnu/libboost_*.so.[0-9]*; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    [[ "$base" == libboost_*.so.* ]] || continue
    [[ -e "${DIR}/${base}" ]] && continue
    cp -L "$f" "${DIR}/"
    echo "Copied ${base}"
  done
  shopt -u nullglob
}

copy_exact_boost_needed() {
  local needed cand copied
  while IFS= read -r needed; do
    [[ -n "$needed" ]] || continue
    [[ "$needed" == libboost_*.so.* ]] || continue
    if [[ -f "${DIR}/${needed}" && ! -L "${DIR}/${needed}" ]]; then
      continue
    fi
    rm -f "${DIR}/${needed}"
    copied=0
    for cand in \
      "/usr/lib/${needed}" \
      "/usr/lib64/${needed}" \
      "/usr/lib/x86_64-linux-gnu/${needed}" \
      "/usr/lib/aarch64-linux-gnu/${needed}"; do
      if [[ -f "$cand" ]]; then
        cp -L "$cand" "${DIR}/${needed}"
        echo "Copied ${needed} from ${cand}"
        copied=1
        break
      fi
    done
    if [[ "$copied" -eq 0 ]]; then
      shopt -s nullglob
      for cand in /usr/lib/*/"${needed}" /usr/lib64/*/"${needed}"; do
        if [[ -f "$cand" ]]; then
          cp -L "$cand" "${DIR}/${needed}"
          echo "Copied ${needed} from ${cand}"
          copied=1
          break
        fi
      done
      shopt -u nullglob
    fi
  done < <(objdump -p "$I2PD" 2>/dev/null | awk '/NEEDED/ {print $2}' | grep '^libboost_' || true)
}

copy_boost_from_host
copy_exact_boost_needed

if LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep -q 'not found'; then
  echo "WARN: bundled i2pd still has unresolved libs (common on Arch if i2pd was built for another Boost):" >&2
  LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep 'not found' >&2 || true
  echo "Hint: use distro-matched i2pd — cp \"\$(command -v i2pd)\" \"${DIR}/i2pd\" && rerun this script." >&2
  exit 1
fi

echo "OK: ${I2PD} resolves all dynamic deps with LD_LIBRARY_PATH=${DIR}"

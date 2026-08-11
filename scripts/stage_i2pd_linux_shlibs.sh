#!/usr/bin/env bash
# Copy host shared libs next to vendor/i2pd/*/i2pd so bundled i2pd can run
# under uv/dev and inside PyInstaller/AppImage (LD_LIBRARY_PATH includes that dir).
#
# Stages Boost, libi2pd*, miniupnpc, OpenSSL, brotli, zstd, zlib, and any other
# DT_NEEDED deps resolved via ldd — never glibc / libstdc++ / libgcc.
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

is_system_lib() {
  local base="$1"
  case "$base" in
    linux-vdso.so.*|ld-linux*.so*|libc.so.*|libm.so.*|libdl.so.*|librt.so.*|libpthread.so.*|libstdc++.so.*|libgcc_s.so.*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

find_host_lib() {
  local needed="$1" cand
  for cand in \
    "/usr/lib/${needed}" \
    "/usr/lib64/${needed}" \
    "/usr/lib/x86_64-linux-gnu/${needed}" \
    "/usr/lib/aarch64-linux-gnu/${needed}" \
    "/lib/${needed}" \
    "/lib64/${needed}" \
    "/lib/x86_64-linux-gnu/${needed}" \
    "/lib/aarch64-linux-gnu/${needed}"; do
    if [[ -f "$cand" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  shopt -s nullglob
  for cand in /usr/lib/*/"${needed}" /usr/lib64/*/"${needed}" /lib/*/"${needed}" /lib64/*/"${needed}"; do
    if [[ -f "$cand" ]]; then
      printf '%s\n' "$cand"
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

copy_lib_into_vendor() {
  local src="$1" base
  base="$(basename "$src")"
  if is_system_lib "$base"; then
    return 0
  fi
  if [[ -f "${DIR}/${base}" && ! -L "${DIR}/${base}" ]]; then
    # Refresh if source is newer / different inode (optional: always overwrite for exact match)
    if cmp -s "$src" "${DIR}/${base}" 2>/dev/null; then
      return 0
    fi
  fi
  rm -f "${DIR}/${base}"
  cp -L "$src" "${DIR}/${base}"
  chmod a+rX "${DIR}/${base}" 2>/dev/null || true
  echo "Copied ${base}"
}

copy_boost_from_host() {
  local libdir f
  shopt -s nullglob
  for libdir in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu; do
    [[ -d "$libdir" ]] || continue
    for f in "${libdir}"/libboost_program_options.so.[0-9]* \
      "${libdir}"/libboost_container.so.[0-9]* \
      "${libdir}"/libboost_system.so.[0-9]* \
      "${libdir}"/libboost_filesystem.so.[0-9]*; do
      [[ -f "$f" ]] || continue
      copy_lib_into_vendor "$f"
    done
  done
  shopt -u nullglob
}

# Walk ldd of i2pd and every newly staged .so until closure (libi2pd*, miniupnpc, …).
stage_ldd_closure() {
  local -a queue=("$I2PD")
  local -A seen=()
  local target line name path resolved base

  while ((${#queue[@]})); do
    target="${queue[0]}"
    queue=("${queue[@]:1}")
    [[ -n "${seen[$target]:-}" ]] && continue
    seen["$target"]=1

    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      if [[ "$line" == *'=>'* ]]; then
        name="$(awk '{print $1}' <<<"$line")"
        path="$(awk '{print $3}' <<<"$line")"
        [[ -n "$path" && "$path" != "not" ]] || continue
      else
        continue
      fi
      base="$(basename "$name")"
      is_system_lib "$base" && continue

      if [[ -f "$path" ]]; then
        resolved="$path"
      elif resolved="$(find_host_lib "$base")"; then
        :
      else
        continue
      fi

      copy_lib_into_vendor "$resolved"
      if [[ -f "${DIR}/${base}" ]]; then
        queue+=("${DIR}/${base}")
      fi
    done < <(ldd "$target" 2>/dev/null || true)
  done
}

# Also pull exact DT_NEEDED names (covers libs ldd finds via system path before staging).
stage_dt_needed() {
  local elf="$1" needed cand
  [[ -f "$elf" ]] || return 0
  while IFS= read -r needed; do
    [[ -n "$needed" ]] || continue
    is_system_lib "$needed" && continue
    if [[ -f "${DIR}/${needed}" && ! -L "${DIR}/${needed}" ]]; then
      continue
    fi
    if cand="$(find_host_lib "$needed")"; then
      copy_lib_into_vendor "$cand"
    fi
  done < <(objdump -p "$elf" 2>/dev/null | awk '/NEEDED/ {print $2}' || true)
}

# Drop Boost SONAMEs not reachable from i2pd via staged libs (leftovers from other distros / Docker).
prune_unrelated_boost() {
  local keep_file f base line name
  keep_file="$(mktemp)"
  # Collect every libboost_* that ldd resolves for i2pd + staged *.so (transitive).
  {
    printf '%s\n' "$I2PD"
    shopt -s nullglob
    printf '%s\n' "${DIR}"/*.so*
    shopt -u nullglob
  } | while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$f" 2>/dev/null | while IFS= read -r line; do
      name="$(awk '{print $1}' <<<"$line")"
      [[ "$name" == libboost_*.so* ]] || continue
      printf '%s\n' "$name"
    done
  done | sort -u >"$keep_file"

  shopt -s nullglob
  for f in "${DIR}"/libboost_*.so*; do
    base="$(basename "$f")"
    if ! grep -qxF "$base" "$keep_file"; then
      echo "Removing unrelated ${base}"
      rm -f "$f"
    fi
  done
  shopt -u nullglob
  rm -f "$keep_file"
}

copy_boost_from_host
stage_dt_needed "$I2PD"
stage_ldd_closure

# Second pass: DT_NEEDED of every staged .so (in case ldd skipped something).
shopt -s nullglob
for f in "${DIR}"/*.so*; do
  stage_dt_needed "$f"
done
shopt -u nullglob
stage_ldd_closure
prune_unrelated_boost

if LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep -q 'not found'; then
  echo "WARN: bundled i2pd still has unresolved libs (common on Arch if i2pd was built for another Boost):" >&2
  LD_LIBRARY_PATH="${DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" ldd "$I2PD" 2>/dev/null | grep 'not found' >&2 || true
  echo "Hint: use distro-matched i2pd — cp \"\$(command -v i2pd)\" \"${DIR}/i2pd\" && rerun this script." >&2
  exit 1
fi

# Also fail if loader reports missing symbol versions (e.g. CXXABI) against vendor-only path.
if ! LD_LIBRARY_PATH="${DIR}" ldd "$I2PD" >/dev/null 2>"${DIR}/.ldd-stderr"; then
  :
fi
if grep -q 'version .* not found' "${DIR}/.ldd-stderr" 2>/dev/null; then
  echo "WARN: bundled i2pd has ABI/version issues with staged libs:" >&2
  cat "${DIR}/.ldd-stderr" >&2 || true
  rm -f "${DIR}/.ldd-stderr"
  exit 1
fi
rm -f "${DIR}/.ldd-stderr"

echo "OK: ${I2PD} resolves all dynamic deps with LD_LIBRARY_PATH=${DIR}"
ls -la "${DIR}"

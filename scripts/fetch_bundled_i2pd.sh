#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="${ROOT}/vendor/i2pd"

usage() {
  cat <<'EOF'
Prepare local bundled i2pd binaries for portable builds.

This script populates vendor/i2pd/ as a LOCAL, UNTRACKED staging area.
It is intended for AppImage/macOS/Windows portable builds only.

Official binary layouts (git): https://github.com/MetanoicArmor/i2pchat-bundled-i2pd
Build scripts run ./scripts/ensure_bundled_i2pd.sh first, which clones that repo by default when vendor/i2pd/ is empty.

Usage:
  ./scripts/fetch_bundled_i2pd.sh --from /path/to/binaries
  ./scripts/fetch_bundled_i2pd.sh --clean

Accepted source layouts:

  /path/to/binaries/
    darwin-arm64/i2pd
    linux-aarch64/i2pd
    linux-x86_64/i2pd
    windows-x64/i2pd.exe

or flat names:

  /path/to/binaries/
    i2pd-darwin-arm64
    i2pd-linux-aarch64
    i2pd-linux-x86_64
    i2pd-windows-x64.exe

Optional URL env vars are also supported:

  I2PCHAT_I2PD_DARWIN_ARM64_URL
  I2PCHAT_I2PD_LINUX_AARCH64_URL
  I2PCHAT_I2PD_LINUX_X86_64_URL
  I2PCHAT_I2PD_WINDOWS_X64_URL

Examples:
  ./scripts/fetch_bundled_i2pd.sh --from "$HOME/i2pd-bundles"
  I2PCHAT_I2PD_WINDOWS_X64_URL=https://example/i2pd.exe ./scripts/fetch_bundled_i2pd.sh
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

copy_one() {
  local src="$1"
  local dest="$2"
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  chmod +x "$dest" || true
  log "Prepared $(realpath "$dest")"
}

download_one() {
  local url="$1"
  local dest="$2"
  mkdir -p "$(dirname "$dest")"
  curl -fsSL "$url" -o "$dest"
  chmod +x "$dest" || true
  log "Downloaded $(realpath "$dest")"
}

clean_target() {
  rm -rf "${TARGET_ROOT}"
  log "Removed local bundled-router staging area: ${TARGET_ROOT}"
}

find_source_file() {
  local base="$1"
  local nested="$2"
  local flat="$3"
  if [[ -f "${base}/${nested}" ]]; then
    printf '%s\n' "${base}/${nested}"
    return 0
  fi
  if [[ -f "${base}/${flat}" ]]; then
    printf '%s\n' "${base}/${flat}"
    return 0
  fi
  return 1
}

copy_from_dir() {
  local source_dir="$1"
  local found=0

  local src
  if src="$(find_source_file "${source_dir}" "darwin-arm64/i2pd" "i2pd-darwin-arm64")"; then
    copy_one "$src" "${TARGET_ROOT}/darwin-arm64/i2pd"
    found=1
  fi
  if src="$(find_source_file "${source_dir}" "linux-aarch64/i2pd" "i2pd-linux-aarch64")"; then
    copy_one "$src" "${TARGET_ROOT}/linux-aarch64/i2pd"
    found=1
  fi
  if src="$(find_source_file "${source_dir}" "linux-x86_64/i2pd" "i2pd-linux-x86_64")"; then
    copy_one "$src" "${TARGET_ROOT}/linux-x86_64/i2pd"
    found=1
  fi
  if src="$(find_source_file "${source_dir}" "windows-x64/i2pd.exe" "i2pd-windows-x64.exe")"; then
    copy_one "$src" "${TARGET_ROOT}/windows-x64/i2pd.exe"
    found=1
  fi

  if [[ "$found" -ne 1 ]]; then
    die "No supported bundled i2pd files found in ${source_dir}"
  fi
}

copy_from_urls() {
  local found=0
  if [[ -n "${I2PCHAT_I2PD_DARWIN_ARM64_URL:-}" ]]; then
    download_one "${I2PCHAT_I2PD_DARWIN_ARM64_URL}" "${TARGET_ROOT}/darwin-arm64/i2pd"
    found=1
  fi
  if [[ -n "${I2PCHAT_I2PD_LINUX_AARCH64_URL:-}" ]]; then
    download_one "${I2PCHAT_I2PD_LINUX_AARCH64_URL}" "${TARGET_ROOT}/linux-aarch64/i2pd"
    found=1
  fi
  if [[ -n "${I2PCHAT_I2PD_LINUX_X86_64_URL:-}" ]]; then
    download_one "${I2PCHAT_I2PD_LINUX_X86_64_URL}" "${TARGET_ROOT}/linux-x86_64/i2pd"
    found=1
  fi
  if [[ -n "${I2PCHAT_I2PD_WINDOWS_X64_URL:-}" ]]; then
    download_one "${I2PCHAT_I2PD_WINDOWS_X64_URL}" "${TARGET_ROOT}/windows-x64/i2pd.exe"
    found=1
  fi

  if [[ "$found" -ne 1 ]]; then
    die "No source directory was provided and no I2PCHAT_I2PD_*_URL env vars were set (try ./scripts/ensure_bundled_i2pd.sh or --from; official repo: https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)"
  fi
}

main() {
  local source_dir=""
  local clean=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --from)
        [[ $# -ge 2 ]] || die "--from requires a directory argument"
        source_dir="$2"
        shift 2
        ;;
      --clean)
        clean=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done

  if [[ "$clean" -eq 1 ]]; then
    clean_target
    exit 0
  fi

  mkdir -p "${TARGET_ROOT}"

  if [[ -n "$source_dir" ]]; then
    [[ -d "$source_dir" ]] || die "Source directory not found: $source_dir"
    copy_from_dir "$source_dir"
  else
    copy_from_urls
  fi

  log ""
  log "Local bundled-router staging is ready in ${TARGET_ROOT}"
  log "Portable builds may now embed i2pd if their platform-specific file exists."
}

main "$@"

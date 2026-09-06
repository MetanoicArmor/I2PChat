#!/usr/bin/env bash
# Rewrite Homebrew/Cellar absolute dylib IDs in a .app to @executable_path/../Frameworks.
# Needed after replacing MacOS/I2PChat with a freshly cmake-installed binary: that
# binary still points at /opt/homebrew, so dyld loads Qt twice (brew + bundle) and
# QApplication aborts in init_platform.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <I2PChat.app>" >&2
  exit 1
fi

APP="${1%/}"
MACOS="${APP}/Contents/MacOS"
FW="${APP}/Contents/Frameworks"
if [ ! -d "${MACOS}" ] || [ ! -d "${FW}" ]; then
  echo "ERROR: not a macOS app bundle: ${APP}" >&2
  exit 1
fi

bundle_rel() {
  local dep="$1"
  case "${dep}" in
    *.framework/*)
      printf '%s' "${dep##*/lib/}"
      ;;
    *)
      basename "${dep}"
      ;;
  esac
}

relink_file() {
  local bin="$1"
  local dep name dest new
  while IFS= read -r dep; do
    [ -n "${dep}" ] || continue
    case "${dep}" in
      /opt/homebrew/*|/usr/local/opt/*|/usr/local/Cellar/*|/opt/local/*) ;;
      *) continue ;;
    esac
    name="$(bundle_rel "${dep}")"
    dest="${FW}/${name}"
    if [ ! -e "${dest}" ]; then
      echo "WARN: ${bin} links ${dep} but ${dest} is missing" >&2
      continue
    fi
    new="@executable_path/../Frameworks/${name}"
    echo "  ${dep}  →  ${new}"
    install_name_tool -change "${dep}" "${new}" "${bin}"
  done < <(otool -L "${bin}" | awk 'NR>1 {print $1}')
}

echo "==> Relink Homebrew paths in ${APP}"
while IFS= read -r bin; do
  [ -n "${bin}" ] || continue
  if file -b "${bin}" | grep -q 'Mach-O'; then
    echo "-- ${bin#${APP}/}"
    relink_file "${bin}"
  fi
done < <(find "${MACOS}" -type f)

# Also rewrite nested copies that macdeployqt already handled; harmless if none.
echo "✔ Relink done"

#!/usr/bin/env bash
# Copy missing Homebrew / @rpath dylibs into Contents/Frameworks and rewrite
# load commands so the .app does not depend on /opt/homebrew at launch.
# Needed after replacing MacOS/I2PChat with a freshly cmake-built binary, and
# after macdeployqt leaves transitive libs (e.g. libbrotlicommon) on @rpath.
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

lookup_named_dylib() {
  local name="$1"
  local candidate
  if [ -e "${FW}/${name}" ]; then
    printf '%s' "${FW}/${name}"
    return 0
  fi
  for candidate in \
      "/opt/homebrew/lib/${name}" \
      "/usr/local/lib/${name}" \
      "/opt/homebrew/opt/brotli/lib/${name}"; do
    if [ -e "${candidate}" ]; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  candidate="$(find /opt/homebrew/opt /usr/local/opt /opt/local/lib \
    -name "${name}" 2>/dev/null | head -n 1 || true)"
  if [ -n "${candidate}" ] && [ -e "${candidate}" ]; then
    printf '%s' "${candidate}"
    return 0
  fi
  return 1
}

copy_dylib_into_fw() {
  local src="$1"
  local name dest
  name="$(basename "${src}")"
  dest="${FW}/${name}"
  if [ -e "${dest}" ]; then
    return 0
  fi
  echo "  COPY ${src}  →  Frameworks/${name}"
  cp -L "${src}" "${dest}"
  chmod u+w "${dest}" 2>/dev/null || true
  install_name_tool -id "@executable_path/../Frameworks/${name}" "${dest}" 2>/dev/null || true
}

rewrite_dep() {
  local bin="$1"
  local dep="$2"
  local name dest new rest
  case "${dep}" in
    @rpath/*.framework/*)
      rest="${dep#@rpath/}"
      dest="${FW}/${rest}"
      if [ ! -e "${dest}" ]; then
        echo "WARN: ${bin} needs ${dep} but ${dest} is missing" >&2
        return 0
      fi
      new="@executable_path/../Frameworks/${rest}"
      if [ "${dep}" != "${new}" ]; then
        echo "  ${dep}  →  ${new}"
        install_name_tool -change "${dep}" "${new}" "${bin}"
      fi
      ;;
    @rpath/*.dylib|@rpath/*.so)
      name="$(basename "${dep}")"
      dest="${FW}/${name}"
      if [ ! -e "${dest}" ]; then
        local found
        if found="$(lookup_named_dylib "${name}")"; then
          copy_dylib_into_fw "${found}"
        else
          echo "WARN: ${bin} needs ${dep} but ${name} was not found" >&2
          return 0
        fi
      fi
      if [[ "${bin}" == "${FW}/"* ]]; then
        new="@loader_path/${name}"
      else
        new="@executable_path/../Frameworks/${name}"
      fi
      if [ "${dep}" != "${new}" ]; then
        echo "  ${dep}  →  ${new}"
        install_name_tool -change "${dep}" "${new}" "${bin}"
      fi
      ;;
    /opt/homebrew/*|/usr/local/opt/*|/usr/local/Cellar/*|/opt/local/*)
      name="$(bundle_rel "${dep}")"
      dest="${FW}/${name}"
      if [ ! -e "${dest}" ]; then
        if [ -e "${dep}" ]; then
          case "${dep}" in
            *.framework/*)
              echo "WARN: ${bin} links ${dep} but ${dest} is missing" >&2
              return 0
              ;;
            *)
              copy_dylib_into_fw "${dep}"
              ;;
          esac
        else
          echo "WARN: ${bin} links ${dep} but ${dest} is missing" >&2
          return 0
        fi
      fi
      new="@executable_path/../Frameworks/${name}"
      if [ "${dep}" != "${new}" ]; then
        echo "  ${dep}  →  ${new}"
        install_name_tool -change "${dep}" "${new}" "${bin}"
      fi
      ;;
  esac
}

strip_external_rpaths() {
  local bin="$1"
  local path
  while IFS= read -r path; do
    [ -n "${path}" ] || continue
    case "${path}" in
      /opt/homebrew/*|/usr/local/*|/opt/local/*)
        echo "  -delete_rpath ${path}"
        install_name_tool -delete_rpath "${path}" "${bin}" 2>/dev/null || true
        ;;
    esac
  done < <(otool -l "${bin}" 2>/dev/null | awk '/cmd LC_RPATH/{c=1} c && $1=="path"{print $2; c=0}')
}

ensure_bundle_rpath() {
  local bin="$1"
  case "${bin}" in
    "${MACOS}"/*) ;;
    *) return 0 ;;
  esac
  if otool -l "${bin}" 2>/dev/null | awk '/cmd LC_RPATH/{c=1} c && $1=="path"{print $2; c=0}' \
      | grep -qx '@executable_path/../Frameworks'; then
    return 0
  fi
  echo "  -add_rpath @executable_path/../Frameworks"
  install_name_tool -add_rpath "@executable_path/../Frameworks" "${bin}" 2>/dev/null || true
}

relink_file() {
  local bin="$1"
  local dep
  while IFS= read -r dep; do
    [ -n "${dep}" ] || continue
    rewrite_dep "${bin}" "${dep}"
  done < <(otool -L "${bin}" | awk 'NR>1 {print $1}')
}

iter_macho() {
  find "${MACOS}" "${FW}" "${APP}/Contents/PlugIns" -type f 2>/dev/null \
    | while IFS= read -r bin; do
        [ -n "${bin}" ] || continue
        if file -b "${bin}" | grep -q 'Mach-O'; then
          printf '%s\n' "${bin}"
        fi
      done
}

echo "==> Relink Homebrew paths in ${APP}"
# Copying a dylib can introduce more @rpath deps; a few passes is enough.
pass=0
while [ "${pass}" -lt 6 ]; do
  pass=$((pass + 1))
  changed=0
  while IFS= read -r bin; do
    [ -n "${bin}" ] || continue
    echo "-- ${bin#${APP}/}"
    before="$(otool -L "${bin}" 2>/dev/null || true)"
    relink_file "${bin}"
    after="$(otool -L "${bin}" 2>/dev/null || true)"
    if [ "${before}" != "${after}" ]; then
      changed=1
    fi
  done < <(iter_macho)
  missing=0
  while IFS= read -r bin; do
    while IFS= read -r dep; do
      case "${dep}" in
        @rpath/*.dylib)
          if [ ! -e "${FW}/$(basename "${dep}")" ]; then
            missing=1
          fi
          ;;
      esac
    done < <(otool -L "${bin}" 2>/dev/null | awk 'NR>1 {print $1}')
  done < <(iter_macho)
  if [ "${changed}" -eq 0 ] && [ "${missing}" -eq 0 ]; then
    break
  fi
done

echo "==> Strip Homebrew LC_RPATH from bundled Mach-O"
while IFS= read -r bin; do
  [ -n "${bin}" ] || continue
  strip_external_rpaths "${bin}"
  ensure_bundle_rpath "${bin}"
done < <(iter_macho)

echo "✔ Relink done"

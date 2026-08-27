#!/usr/bin/env bash
# Resolve workflow_dispatch tag / source_ref into GitHub Actions outputs.
# Env: TAG (required), SOURCE_REF (optional). Writes tag, ver, checkout_ref to GITHUB_OUTPUT.
set -euo pipefail

trim() { echo "$1" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'; }
to_ver() {
  local x
  x="$(trim "$1")"
  x="${x#v}"
  x="${x#.}"
  echo "$x"
}

R="$(to_ver "${TAG:-}")"
if [ -z "$R" ]; then
  echo "ERROR: empty tag" >&2
  exit 1
fi
if ! echo "$R" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: tag must be semver X.Y.Z (e.g. v1.2.3), got: ${TAG}" >&2
  exit 1
fi

echo "tag=v${R}" >> "${GITHUB_OUTPUT}"
echo "ver=${R}" >> "${GITHUB_OUTPUT}"

SRC_RAW="$(trim "${SOURCE_REF:-}")"
if [ -z "$SRC_RAW" ]; then
  SRC="v${R}"
else
  TMP="$(to_ver "$SRC_RAW")"
  if echo "$TMP" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    SRC="v${TMP}"
  else
    SRC="$SRC_RAW"
  fi
fi
echo "checkout_ref=${SRC}" >> "${GITHUB_OUTPUT}"
echo "Will build from ref: ${SRC}, upload to release: v${R}"

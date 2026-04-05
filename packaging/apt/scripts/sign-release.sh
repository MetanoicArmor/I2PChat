#!/usr/bin/env bash
# Sign dists/stable/Release → InRelease + Release.gpg; export public KEY.gpg to site root.
# Requires: gpg with secret key imported; optional APT_REPO_GPG_PASSPHRASE in env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="${SITE_DIR:-$ROOT/site}"
REL="$SITE/dists/stable/Release"

if [[ ! -f "$REL" ]]; then
  echo "ERROR: missing $REL — run build-apt-site.sh first" >&2
  exit 1
fi

GPG_OPTS=(--batch --yes)
if [[ -n "${APT_REPO_GPG_PASSPHRASE:-}" ]]; then
  GPG_OPTS+=(--pinentry-mode loopback --passphrase-fd 0)
fi

FPR="$(
  gpg --with-colons --list-secret-keys --keyid-format=long 2>/dev/null \
    | awk -F: '$1 == "fpr" {print $10; exit}'
)"
if [[ -z "$FPR" ]]; then
  echo "ERROR: no GPG secret key in keyring" >&2
  exit 1
fi

cd "$SITE/dists/stable"
if [[ -n "${APT_REPO_GPG_PASSPHRASE:-}" ]]; then
  printf '%s\n' "$APT_REPO_GPG_PASSPHRASE" \
    | gpg "${GPG_OPTS[@]}" --local-user "$FPR" --clearsign --output InRelease Release
  printf '%s\n' "$APT_REPO_GPG_PASSPHRASE" \
    | gpg "${GPG_OPTS[@]}" --local-user "$FPR" -abs -o Release.gpg Release
else
  gpg "${GPG_OPTS[@]}" --local-user "$FPR" --clearsign --output InRelease Release
  gpg "${GPG_OPTS[@]}" --local-user "$FPR" -abs -o Release.gpg Release
fi

gpg --armor --export "$FPR" > "$SITE/KEY.gpg"
touch "$SITE/.nojekyll"
echo "Signed Release; wrote InRelease, Release.gpg, KEY.gpg"

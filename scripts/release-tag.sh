#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

usage() {
  cat <<'EOF'
Create a signed annotated release tag from the current HEAD.

Safety rails:
- refuses to run if HEAD is not a valid signed commit
- refuses to overwrite an existing local tag
- refuses to overwrite an existing remote tag on origin
- pushes only when --push is given

Usage:
  ./scripts/release-tag.sh v1.3.2
  ./scripts/release-tag.sh 1.3.2 --push

Optional environment:
  I2PCHAT_GPG_KEY_ID=<key id or fingerprint>  Select a specific signing key
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

ensure_head_signed() {
  git verify-commit HEAD >/dev/null 2>&1 || die "HEAD is not a locally verifiable signed commit; sign the release commit first"
}

ensure_tracked_worktree_clean() {
  local status
  status="$(git status --porcelain --untracked-files=no)"
  [[ -z "$status" ]] || die "tracked files are dirty; commit or stash changes before tagging"
}

ensure_tag_absent() {
  local tag="$1"
  if git rev-parse --verify --quiet "refs/tags/${tag}" >/dev/null; then
    die "local tag ${tag} already exists; do not move published release tags"
  fi
  if git ls-remote --exit-code --tags origin "refs/tags/${tag}" >/dev/null 2>&1; then
    die "remote tag ${tag} already exists on origin; cut a new release tag instead of moving it"
  fi
}

create_signed_tag() {
  local tag="$1"
  local message="$2"
  local -a args=(-s "$tag" -m "$message" HEAD)
  if [[ -n "${I2PCHAT_GPG_KEY_ID:-}" ]]; then
    args=(-s -u "${I2PCHAT_GPG_KEY_ID}" "$tag" -m "$message" HEAD)
  fi
  git tag "${args[@]}"
}

verify_tag() {
  local tag="$1"
  git tag -v "$tag"
}

push_branch_and_tag() {
  local tag="$1"
  local branch
  branch="$(git symbolic-ref --quiet --short HEAD)" || die "detached HEAD: push the commit manually, then push the tag"
  git push origin "${branch}"
  git push origin "refs/tags/${tag}"
}

main() {
  need_cmd git
  need_cmd gpg

  local tag=""
  local push=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --push)
        push=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      -*)
        die "unknown argument: $1"
        ;;
      *)
        [[ -z "$tag" ]] || die "tag already set to ${tag}; got extra argument $1"
        tag="$1"
        shift
        ;;
    esac
  done

  [[ -n "$tag" ]] || die "missing tag argument (example: v1.3.2)"
  [[ "$tag" == v* ]] || tag="v${tag}"

  export GPG_TTY="${GPG_TTY:-$(tty)}"
  gpg-connect-agent updatestartuptty /bye >/dev/null

  ensure_tracked_worktree_clean
  ensure_head_signed
  ensure_tag_absent "$tag"

  local message="I2PChat ${tag}"
  create_signed_tag "$tag" "$message"

  printf 'Created signed tag %s on %s\n' "$tag" "$(git rev-parse --short HEAD)"
  printf '\n== Local tag verification ==\n'
  verify_tag "$tag"

  if [[ "$push" -eq 1 ]]; then
    printf '\n== Push ==\n'
    push_branch_and_tag "$tag"
  else
    printf '\nTag created locally. Push when ready:\n'
    printf '  git push origin %s\n' "$(git symbolic-ref --quiet --short HEAD || printf '<branch>')"
    printf '  git push origin refs/tags/%s\n' "$tag"
  fi
}

main "$@"

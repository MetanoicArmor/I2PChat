#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_REPOSITORY="MetanoicArmor/I2PChat"

usage() {
  cat <<'EOF'
Sync the bundled roadmap/backlog to GitHub issues and milestones.

Usage:
  GITHUB_TOKEN=ghp_xxx ./scripts/sync_backlog.sh [owner/repo]

Examples:
  GITHUB_TOKEN=ghp_xxx ./scripts/sync_backlog.sh
  GITHUB_TOKEN=ghp_xxx ./scripts/sync_backlog.sh owner/repo

Environment:
  GITHUB_TOKEN       Required (or GH_TOKEN). Token with issue/milestone write access.
  GITHUB_REPOSITORY  Optional target repository.

Optional: repo-root `.env` or `scripts/.env` with GITHUB_TOKEN=… (see
scripts/backlog.env.example). The Python script loads these automatically.

If no repository is provided, the wrapper uses GITHUB_REPOSITORY or
MetanoicArmor/I2PChat by default.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

# Token may come from the environment or from repo-root / scripts/.env (loaded by Python).

if [[ $# -gt 1 ]]; then
  echo "error: expected at most one positional argument: [owner/repo]" >&2
  echo >&2
  usage >&2
  exit 1
fi

export GITHUB_REPOSITORY="${1:-${GITHUB_REPOSITORY:-$DEFAULT_REPOSITORY}}"

exec python3 "${REPO_ROOT}/scripts/sync_github_backlog.py"

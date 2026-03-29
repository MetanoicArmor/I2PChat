#!/usr/bin/env bash
# Runs run_tests.applescript with an explicit repo root (avoids flaky "path to me" in osascript).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
exec osascript "${SCRIPT_DIR}/run_tests.applescript" "${REPO_ROOT}"

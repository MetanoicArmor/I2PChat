#!/usr/bin/env bash
# Automated checks: full pytest suite + CI unittest gate (see .github/workflows/test-gate.yml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "run_tests.sh: uv not found" >&2
  echo "Install: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

export UV_PROJECT_ENVIRONMENT="${REPO_ROOT}/.venv"
uv sync --frozen

echo "==> pytest tests/"
uv run pytest tests/ -q --tb=short

echo ""
echo "==> unittest (Test Gate modules)"
uv run python -m unittest \
  tests.test_blindbox_state_wrap \
  tests.test_asyncio_regression \
  tests.test_blindbox_client \
  tests.test_atomic_writes \
  tests.test_chat_history \
  tests.test_history_ui_guards \
  tests.test_profile_import_overwrite \
  tests.test_protocol_framing_vnext \
  tests.test_sam_input_validation \
  tests.test_audit_remediation

echo ""
echo "All checks passed."

#!/usr/bin/env bash
# Automated checks: full pytest suite + CI unittest gate (see .github/workflows/test-gate.yml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${REPO_ROOT}/.gm/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  echo "run_tests.sh: no ${PYTHON}" >&2
  echo "Install deps with uv, e.g.: uv sync && uv run pytest tests/ -q" >&2
  exit 1
fi

echo "==> pytest tests/"
"${PYTHON}" -m pytest tests/ -q --tb=short

echo ""
echo "==> unittest (Test Gate modules)"
"${PYTHON}" -m unittest \
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

#!/bin/bash
# Double-click in Finder: runs tests in Terminal (macOS).
cd "$(dirname "$0")/.." || exit 1
exec ./scripts/run_tests.sh

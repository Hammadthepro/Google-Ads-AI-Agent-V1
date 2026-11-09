#!/usr/bin/env bash
# Real-API tests: they use the credentials saved on the Integrations page / in .env and only READ.
# Results: PASSED, FAILED, NOT CONFIGURED (credentials missing) or BLOCKED (configured but an external condition prevents the check).
# Skipped is never reported as passed.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" -m pytest live_tests -p no:cacheprovider -rs -q "$@"

#!/usr/bin/env bash
# Audits your .env against what the application actually reads. Prints NO secret values.
#   bash scripts/check-env.sh              # the .env at the repo root plus real environment variables
#   bash scripts/check-env.sh --file-only  # just the file
#   bash scripts/check-env.sh --json       # machine-readable
# Exit code 1 when an error is found.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PY="python"
[ -x .venv/bin/python ] && PY=".venv/bin/python"
exec "$PY" -m app.cli check-env "$@"

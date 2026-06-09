#!/usr/bin/env bash
# Start the Wagtail test site with /api/schema/ enabled, for schemathesis.
#
# Run from the repo root:
#     ./qa/schemathesis/start_server.sh
#
# The server listens on 0.0.0.0:8001 by default. Override with PORT=…
# Press Ctrl-C to stop. Once running, in another terminal:
#     ./qa/schemathesis/run_schemathesis.py
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

cd "$REPO_ROOT"

PORT="${PORT:-8001}"
export PYTHONPATH="$HERE:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export DJANGO_SETTINGS_MODULE=schemathesis_settings

exec "${PYTHON:-python}" wagtail/test/manage.py runserver "0:$PORT"

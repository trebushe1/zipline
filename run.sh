#!/bin/sh
# Starts zipline on http://127.0.0.1:8000
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port "${PORT:-8000}" "$@"

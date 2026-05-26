#!/usr/bin/env bash
# Linux launcher for the causal-platform backend on gx10.
# Mirrors launch_backend.bat but uses bash + Linux paths.
# Port 49234 is chosen to avoid Windows Hyper-V reserved ranges; safe on Linux too.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

export PYTHONPATH="$BACKEND_DIR"
export ENV="local"
export DEV_AUTH_BYPASS="true"
export HALLUCINATION_RISK_THRESHOLD="0.7"
export MAX_AGENT_CYCLES="5"
export CORS_ALLOWED_ORIGINS="http://localhost:3000,http://localhost:5173"

cd "$BACKEND_DIR"

# Prefer the project venv if present
if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    PY="$BACKEND_DIR/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

exec "$PY" -m uvicorn src.api.main:app --host 127.0.0.1 --port 49234

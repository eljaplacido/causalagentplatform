#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Causal Platform — local development launcher
#
# Usage:
#   ./start.sh                  # Start both backend and frontend
#   ./start.sh backend          # Start backend only
#   ./start.sh frontend         # Start frontend only
#   ./start.sh test             # Run backend unit tests (pytest)
#   ./start.sh e2e              # Run E2E tests (requires running stack)
#
# Prerequisites:
#   - Python 3.12+ with pip
#   - Node.js 20+ with npm
#
# The backend uses SQLite for local dev (ENV=local; no Docker required) and
# listens on the same port as launch_backend.sh and the README (49234). No LLM
# API key is needed: no code path calls an LLM today.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BACKEND_ENV="$BACKEND_DIR/.env"
BACKEND_PORT="${BACKEND_PORT:-49234}"
# The frontend's built-in default is :8000; point it at the port used here.
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:$BACKEND_PORT}"
export NEXT_PUBLIC_ENVIRONMENT="${NEXT_PUBLIC_ENVIRONMENT:-local}"

# ── Colours ───────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Load backend .env ─────────────────────────────────────────────────────────

load_env() {
  if [[ -f "$BACKEND_ENV" ]]; then
    # Export vars from .env, skipping comments and blank lines
    set -o allexport
    # shellcheck disable=SC1090
    source <(grep -v '^#' "$BACKEND_ENV" | grep -v '^$')
    set +o allexport
    info "Loaded env from $BACKEND_ENV"
  else
    warn "No backend/.env found — using shell environment"
    warn "Copy backend/.env.example → backend/.env (ENV=local, DEV_AUTH_BYPASS=true)"
  fi
}

# ── Backend ───────────────────────────────────────────────────────────────────

start_backend() {
  info "Starting backend (FastAPI + LangGraph) on http://localhost:$BACKEND_PORT"
  info "  ENV=${ENV:-local} | DEV_AUTH_BYPASS=${DEV_AUTH_BYPASS:-true} | SQLite"

  cd "$BACKEND_DIR"
  export ENV="${ENV:-local}" DEV_AUTH_BYPASS="${DEV_AUTH_BYPASS:-true}"

  # Install into the backend venv if it is missing. The kernel and the
  # decision layer are local packages the backend depends on.
  if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "import fastapi, causal_core, causal_decision" 2>/dev/null; then
    info "Installing Python dependencies into backend/.venv..."
    [[ -x .venv/bin/python ]] || python3 -m venv .venv
    .venv/bin/pip install --quiet -e ../packages/causal-core -e ../packages/causal-decision -e ".[dev]"
  fi

  exec .venv/bin/uvicorn src.api.main:app --reload --port "$BACKEND_PORT" --host 127.0.0.1
}

# ── Frontend ──────────────────────────────────────────────────────────────────

start_frontend() {
  info "Starting frontend (Next.js 15) on http://localhost:3000"

  cd "$FRONTEND_DIR"

  if [[ ! -d "node_modules" ]]; then
    info "Installing Node dependencies (npm workspaces, from the repo root)..."
    (cd "$SCRIPT_DIR" && npm ci)
  fi

  exec npm run dev
}

# ── Tests ─────────────────────────────────────────────────────────────────────

run_backend_tests() {
  info "Running backend unit + integration tests..."
  cd "$BACKEND_DIR"
  .venv/bin/python -m pytest tests/ -q --tb=short
  info "The full gate (every suite, ratchets, claim checks): bash .gnomon/ci.sh"
}

run_e2e_tests() {
  info "Running Playwright E2E tests..."
  info "Ensure the backend is running on :$BACKEND_PORT first."
  cd "$FRONTEND_DIR"
  npx playwright test
}

# ── Main ──────────────────────────────────────────────────────────────────────

load_env

MODE="${1:-both}"

case "$MODE" in
  backend)
    start_backend
    ;;
  frontend)
    start_frontend
    ;;
  test)
    run_backend_tests
    ;;
  e2e)
    run_e2e_tests
    ;;
  both)
    info "Starting full stack in background processes..."
    info "Backend  → http://localhost:$BACKEND_PORT"
    info "Frontend → http://localhost:3000"
    info "API docs → http://localhost:$BACKEND_PORT/docs"
    info ""
    info "Press Ctrl+C to stop both."
    echo ""

    # Start backend in background
    (start_backend 2>&1 | sed 's/^/[backend] /') &
    BACKEND_PID=$!

    # Give backend 3s to start
    sleep 3

    # Start frontend in background
    (cd "$FRONTEND_DIR" && npm run dev 2>&1 | sed 's/^/[frontend] /') &
    FRONTEND_PID=$!

    # Trap Ctrl+C to kill both
    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

    wait
    ;;
  *)
    error "Unknown mode: $MODE"
    echo "Usage: $0 [backend|frontend|test|e2e|both]"
    exit 1
    ;;
esac

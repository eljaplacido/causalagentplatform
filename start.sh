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
#   - ANTHROPIC_API_KEY set in backend/.env
#
# The backend uses SQLite for local dev (no Docker required).
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BACKEND_ENV="$BACKEND_DIR/.env"

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
    warn "Copy backend/.env.example → backend/.env and set ANTHROPIC_API_KEY"
  fi
}

# ── Check ANTHROPIC_API_KEY ───────────────────────────────────────────────────

check_api_key() {
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]] || [[ "$ANTHROPIC_API_KEY" == "your-anthropic-api-key-here" ]]; then
    warn "ANTHROPIC_API_KEY is not set or is placeholder."
    warn "The AI pipeline will fail without a real key."
    warn "Edit backend/.env and set ANTHROPIC_API_KEY=sk-ant-..."
  fi
}

# ── Backend ───────────────────────────────────────────────────────────────────

start_backend() {
  info "Starting backend (FastAPI + LangGraph) on http://localhost:8000"
  info "  ENV=local | DEV_AUTH_BYPASS=true | SQLite"

  cd "$BACKEND_DIR"

  # Install if node_modules equivalent (site-packages) missing
  if ! python -c "import fastapi" 2>/dev/null; then
    info "Installing Python dependencies..."
    pip install -e ".[dev]" --quiet
  fi

  exec uvicorn src.api.main:app --reload --port 8000 --host 0.0.0.0
}

# ── Frontend ──────────────────────────────────────────────────────────────────

start_frontend() {
  info "Starting frontend (Next.js 15) on http://localhost:3000"

  cd "$FRONTEND_DIR"

  if [[ ! -d "node_modules" ]]; then
    info "Installing Node dependencies..."
    npm install
  fi

  exec npm run dev
}

# ── Tests ─────────────────────────────────────────────────────────────────────

run_backend_tests() {
  info "Running backend unit + integration tests..."
  cd "$BACKEND_DIR"
  python -m pytest tests/ -v --tb=short
}

run_e2e_tests() {
  info "Running Playwright E2E tests..."
  info "Ensure the backend is running on :8000 first."
  cd "$FRONTEND_DIR"
  npx playwright test
}

# ── Main ──────────────────────────────────────────────────────────────────────

load_env
check_api_key

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
    info "Backend  → http://localhost:8000"
    info "Frontend → http://localhost:3000"
    info "API docs → http://localhost:8000/docs"
    info ""
    info "Press Ctrl+C to stop both."
    echo ""

    # Start backend in background
    (cd "$BACKEND_DIR" && uvicorn src.api.main:app --reload --port 8000 --host 0.0.0.0 2>&1 | sed 's/^/[backend] /') &
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

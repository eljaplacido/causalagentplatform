#!/usr/bin/env bash
# The full local gate for this repository. `.github/workflows/ci.yml` is meant to
# call this; until it does, this is the one command that decides "does it pass".
#
#   .gnomon/ci.sh            run every step, print a per-step summary, exit 1 on any FAIL
#
# THREE RULES THIS FILE HOLDS ITSELF TO (all taken from gnomon's own ci.sh):
#
#   1. A gate that inspected nothing may not report success. Every step reports
#      a count of what it looked at -- tests run, files linted, files typechecked
#      -- and a count of zero is a FAIL, never a pass.
#   2. Counts are read back out of the tools that produced them. Nothing here
#      asserts a hard-coded total; that would be a number nothing checks.
#   3. Never silent. A step that cannot run because its tool -- or the config
#      the tool requires -- is ABSENT is SKIPPED, by name, with the reason, in
#      the summary. Absent is the only reason a step may skip: a tool that is
#      present and crashes is a FAIL.
#
# Every step runs even after an earlier one fails, so one run shows everything
# that is wrong. Logs land in .gnomon-out/ci/<step>.log (outside the hashed
# surface, git-ignored); a failing step prints its tail.
#
# The numbers the ratchets hold are in scripts/coverage-floor.json and
# scripts/lint-baseline.json; each says when and against what it was measured.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

LOG_DIR="${CI_LOG_DIR:-$ROOT/.gnomon-out/ci}"
rm -rf "$LOG_DIR"
mkdir -p "$LOG_DIR"

if [ -t 1 ]; then RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'
else RED=""; GREEN=""; YELLOW=""; NC=""; fi

STEP_NAMES=(); STEP_STATUS=(); STEP_DETAIL=(); STEP_SECS=()
record() {  # name status detail seconds
    STEP_NAMES+=("$1"); STEP_STATUS+=("$2"); STEP_DETAIL+=("$3"); STEP_SECS+=("$4")
    local colour="$GREEN"
    [ "$2" = "FAIL" ] && colour="$RED"
    [ "$2" = "SKIPPED" ] && colour="$YELLOW"
    printf '%s%-8s%s %-20s %s\n' "$colour" "$2" "$NC" "$1" "$3"
}
show_tail() { echo "   ── last lines of $1 ──"; tail -n "${2:-25}" "$1" | sed 's/^/   | /'; }
header() { echo ""; echo "═══ $1 ═══"; }

# ── Toolchains ───────────────────────────────────────────────────────────────
# Backend: a bin/ directory holding python, pytest, ruff and mypy. BACKEND_VENV
# wins; then backend/.venv; then, in a git worktree (which has no venv of its
# own), the main checkout's backend/.venv -- derived from git, never written down;
# last, whatever python on PATH has pytest (CI installs into the runner's Python).
resolve_pybin() {
    local c common
    common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    for c in "${BACKEND_VENV:-}" "backend/.venv" "${common:+$(dirname "$common")/backend/.venv}"; do
        if [ -n "$c" ] && [ -x "$c/bin/python" ] && [ -x "$c/bin/pytest" ]; then
            (cd "$c/bin" && pwd); return 0
        fi
    done
    if command -v pytest >/dev/null 2>&1; then dirname "$(command -v pytest)"; return 0; fi
    return 1
}
PYBIN="$(resolve_pybin || true)"
if [ -n "$PYBIN" ]; then PY="$PYBIN/python"; [ -x "$PY" ] || PY="$(command -v python3)"
else PY="$(command -v python3 || true)"; fi

echo "ci.sh at $(git rev-parse --short HEAD 2>/dev/null || echo 'no-git')$(git diff --quiet 2>/dev/null || echo '-dirty')"
echo "backend toolchain: ${PYBIN:-ABSENT}"
echo "frontend toolchain: $([ -d frontend/node_modules ] && echo "frontend/node_modules present" || echo ABSENT)"
echo "logs: $LOG_DIR"

# ── 1. Backend test suite + coverage floor ───────────────────────────────────
# The FULL suite (unit + integration + guardian), once, with coverage measured
# on that run. Coverage is judged on the full suite only -- see
# scripts/coverage-floor.json for why the old global --cov-fail-under went.
header "backend: pytest (full suite) + coverage floor"
COV_JSON="$LOG_DIR/coverage.json"
if [ -z "$PYBIN" ]; then
    record backend-pytest SKIPPED "no Python toolchain with pytest (set BACKEND_VENV)" 0
    record backend-coverage SKIPPED "no Python toolchain with pytest" 0
else
    t0=$SECONDS
    ( cd backend && "$PYBIN/pytest" -q -p no:cacheprovider --cov=src \
        --cov-report="json:$COV_JSON" ) > "$LOG_DIR/backend-pytest.log" 2>&1
    rc=$?
    summary="$(grep -E '^=+ .*(passed|failed|error).* in [0-9.]+s' "$LOG_DIR/backend-pytest.log" | tail -1 | sed -E 's/=+//g; s/^ +| +$//g')"
    passed="$(echo "$summary" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
    passed="${passed:-0}"
    if [ "$rc" -eq 0 ] && [ "$passed" -gt 0 ]; then
        record backend-pytest PASS "$passed passed (backend/tests: unit+integration+guardian) -- $summary" $((SECONDS - t0))
    else
        show_tail "$LOG_DIR/backend-pytest.log" 40
        record backend-pytest FAIL "exit $rc, ${passed} passed -- ${summary:-no pytest summary line}" $((SECONDS - t0))
    fi

    t0=$SECONDS
    "$PY" scripts/coverage_gate.py "$COV_JSON" > "$LOG_DIR/backend-coverage.log" 2>&1
    rc=$?
    cat "$LOG_DIR/backend-coverage.log" | sed 's/^/   /'
    detail="$(grep -oE 'coverage [0-9.]+% \([0-9]+/[0-9]+ statements\) >= floor [0-9.]+%' "$LOG_DIR/backend-coverage.log" | head -1)"
    if [ "$rc" -eq 0 ]; then
        record backend-coverage PASS "line ${detail#coverage }" $((SECONDS - t0))
    else
        record backend-coverage FAIL "$(grep -m1 FAIL "$LOG_DIR/backend-coverage.log" | cut -c1-160)" $((SECONDS - t0))
    fi
fi

# ── 2. Lint and type ratchets ────────────────────────────────────────────────
# Counts may not rise above scripts/lint-baseline.json, and the committed maxima
# may only fall. The args each tool runs with live in that file, next to the
# number they produced.
ratchet_step() {  # step-name entry [binary]
    local name="$1" entry="$2" bin="${3:-}" t0=$SECONDS rc detail
    "$PY" scripts/lint_ratchet.py "$entry" ${bin:+--bin "$bin"} > "$LOG_DIR/$name.log" 2>&1
    rc=$?
    sed 's/^/   /' "$LOG_DIR/$name.log"
    if [ "$rc" -eq 0 ]; then
        detail="$(grep -m1 -oE '[0-9]+ findings over [0-9]+ files \(max [0-9]+\)' "$LOG_DIR/$name.log")"
        record "$name" PASS "$detail" $((SECONDS - t0))
    else
        record "$name" FAIL "$(grep -m1 FAIL "$LOG_DIR/$name.log" | cut -c6-170)" $((SECONDS - t0))
    fi
}

header "backend: ruff ratchet"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/ruff" ]; then
    ratchet_step backend-ruff ruff "$PYBIN/ruff"
    ratchet_step scripts-ruff ruff-scripts "$PYBIN/ruff"
else
    record backend-ruff SKIPPED "ruff not installed in the backend toolchain" 0
    record scripts-ruff SKIPPED "ruff not installed in the backend toolchain" 0
fi

header "backend: mypy --strict ratchet"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/mypy" ]; then
    ratchet_step backend-mypy mypy "$PYBIN/mypy"
else
    record backend-mypy SKIPPED "mypy not installed in the backend toolchain" 0
fi

# ── 3. Frontend ──────────────────────────────────────────────────────────────
# Runs only when frontend/node_modules exists (npm workspaces hoist most of it to
# the root node_modules). --no-install everywhere: without it npx FETCHES a
# missing tool, which turns a 2s gate into a network call against whatever
# version the registry serves today.
header "frontend: tsc / vitest / eslint"
if [ ! -d frontend/node_modules ]; then
    for s in frontend-tsc frontend-vitest frontend-eslint; do
        record "$s" SKIPPED "frontend/node_modules absent -- run npm ci at the repo root" 0
    done
else
    t0=$SECONDS
    ( cd frontend && npx --no-install tsc --noEmit --listFiles ) > "$LOG_DIR/frontend-tsc.log" 2>&1
    rc=$?
    # --listFiles prints every file in the program; the project's own are the
    # ones outside node_modules. That count is what "typechecked" means here.
    n="$(grep '^/' "$LOG_DIR/frontend-tsc.log" | grep -vc node_modules || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -gt 0 ]; then
        record frontend-tsc PASS "$n project files typecheck (tsc --noEmit)" $((SECONDS - t0))
    else
        grep -v '^/' "$LOG_DIR/frontend-tsc.log" | tail -25 | sed 's/^/   | /'
        record frontend-tsc FAIL "exit $rc over ${n:-0} project files" $((SECONDS - t0))
    fi

    t0=$SECONDS
    ( cd frontend && npx --no-install vitest run ) > "$LOG_DIR/frontend-vitest.log" 2>&1
    rc=$?
    ESC=$(printf '\033')
    plain="$(sed -E "s/${ESC}\[[0-9;]*[A-Za-z]//g" "$LOG_DIR/frontend-vitest.log")"
    n="$(echo "$plain" | grep -E '^ *Tests +' | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || true)"
    files="$(echo "$plain" | grep -E '^ *Test Files +' | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -gt 0 ]; then
        record frontend-vitest PASS "${n} tests passed in ${files:-?} files" $((SECONDS - t0))
    else
        show_tail "$LOG_DIR/frontend-vitest.log" 30
        record frontend-vitest FAIL "exit $rc, ${n:-0} passed" $((SECONDS - t0))
    fi

    # eslint 9 refuses to run without a flat config and exits 2 before
    # inspecting a single file; frontend/ has none, so the CI lint step has never
    # linted anything. A rule set is a project decision, not something a gate
    # invents in order to have a number -- so this is SKIPPED, with the reason,
    # until someone commits a config (and an `eslint` entry in
    # scripts/lint-baseline.json in the same change).
    ESLINT_CFG="$(ls frontend/eslint.config.{js,mjs,cjs,ts,mts,cts} 2>/dev/null | head -1)"
    if [ -z "$ESLINT_CFG" ]; then
        record frontend-eslint SKIPPED "frontend has no eslint.config.* -- eslint 9 cannot run without one (none invented here)" 0
    else
        ratchet_step frontend-eslint eslint
    fi
fi

# ── 4. Python packages under packages/ ─────────────────────────────────────
# Every packages/<name>/pyproject.toml gets its own suite run here. The set is
# derived by listing the directory, not written down, so a package is gated the
# day it lands -- a hand-kept list is the selector bug gate_scope exists for.
# packages/causal-core (the causal kernel, with its pre-registered T0 correctness
# gate, benchmarks/t0/PRE-REGISTRATION.md) is always named in the summary: RAN,
# or NOT RUN with the reason -- never nothing.
#
# Each runs in ITS OWN environment, in this order: $PKG_VENV_<NAME> (upper case,
# dashes as underscores), packages/<name>/.venv here, the main checkout's in a
# git worktree (derived from git), then causal-core's environment -- the kernel's
# venv is where its dependents (causal-mcp) are installed. The backend's venv is
# deliberately not a fallback: it lacks the suites' test dependencies
# (hypothesis, ...), so it would report a missing module as a failing package.
# PYTHONPATH puts THIS tree's src/ first (the package's, then causal-core's), so
# a venv whose editable install targets another checkout still tests the code
# here.
#
# Counts come from the JUnit report (tests, failures, errors, skipped), not from
# scraping the summary line: causal-core's addopts set -q, and pytest's quiet
# levels drop that line.
junit_counts() {  # python junit.xml -> "tests failures errors skipped"
    "$1" - "$2" <<'PYEOF'
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse(sys.argv[1]).getroot()
except Exception:
    print("0 0 0 0")
    sys.exit(0)
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
tot = [0, 0, 0, 0]
for s in suites:
    for i, k in enumerate(("tests", "failures", "errors", "skipped")):
        tot[i] += int(s.get(k, 0))
print(*tot)
PYEOF
}
pkg_python() {  # name -> path to a python for that package, or nothing
    local name="$1" var c common
    var="PKG_VENV_$(echo "$name" | tr 'a-z-' 'A-Z_')"
    common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    for c in "${!var:-}" "packages/$name/.venv" "${common:+$(dirname "$common")/packages/$name/.venv}" \
             "packages/causal-core/.venv" "${common:+$(dirname "$common")/packages/causal-core/.venv}"; do
        if [ -n "$c" ] && [ -x "$c/bin/python" ]; then echo "$(cd "$c/bin" && pwd)/python"; return 0; fi
    done
    return 1
}
header "Python packages under packages/"
if [ ! -f packages/causal-core/pyproject.toml ]; then
    record causal-core SKIPPED "NOT RUN: packages/causal-core/pyproject.toml is not in this tree" 0
fi
# causal-core first: the others depend on it.
PKG_ORDER="$( { [ -f packages/causal-core/pyproject.toml ] && echo causal-core
               for f in packages/*/pyproject.toml; do [ -f "$f" ] && basename "$(dirname "$f")"; done
             } | awk '!seen[$0]++' )"
for name in $PKG_ORDER; do
    t0=$SECONDS
    if [ ! -d "packages/$name/tests" ]; then
        record "$name" FAIL "packages/$name has a pyproject.toml but no tests/ -- nothing to run is not a pass" 0
        continue
    fi
    PKG_PY="$(pkg_python "$name" || true)"
    if [ -z "$PKG_PY" ]; then
        record "$name" SKIPPED "NOT RUN: no environment for packages/$name -- python -m venv packages/$name/.venv && pip install -e 'packages/$name[dev]'" 0
        continue
    fi
    ( cd "packages/$name" && PYTHONPATH="$ROOT/packages/$name/src:$ROOT/packages/causal-core/src" \
        "$PKG_PY" -m pytest -p no:cacheprovider --junitxml="$LOG_DIR/$name-junit.xml" ) \
        > "$LOG_DIR/$name.log" 2>&1
    rc=$?
    read -r n_tests n_fail n_err n_skip < <(junit_counts "$PKG_PY" "$LOG_DIR/$name-junit.xml")
    n_pass=$(( n_tests - n_fail - n_err - n_skip ))
    extra=""; [ "$name" = "causal-core" ] && extra=", incl. the T0 gate"
    if [ "$rc" -eq 0 ] && [ "$n_pass" -gt 0 ]; then
        record "$name" PASS "RAN: ${n_pass} passed, ${n_skip} skipped${extra} (env: ${PKG_PY%/bin/python})" $((SECONDS - t0))
    else
        show_tail "$LOG_DIR/$name.log" 30
        record "$name" FAIL "RAN: exit $rc -- ${n_pass} passed, ${n_fail} failed, ${n_err} errors, ${n_skip} skipped" $((SECONDS - t0))
    fi
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "═══ ci.sh summary ═══"
fails=0; skips=0
printf '%-20s %-8s %5s  %s\n' STEP STATUS SECS DETAIL
for i in "${!STEP_NAMES[@]}"; do
    printf '%-20s %-8s %5s  %s\n' "${STEP_NAMES[$i]}" "${STEP_STATUS[$i]}" "${STEP_SECS[$i]}" "${STEP_DETAIL[$i]}"
    [ "${STEP_STATUS[$i]}" = "FAIL" ] && fails=$((fails + 1))
    [ "${STEP_STATUS[$i]}" = "SKIPPED" ] && skips=$((skips + 1))
done
echo ""
if [ "$fails" -gt 0 ]; then
    echo "${RED}❌ ${fails} step(s) FAILED${NC}, ${skips} skipped, $((${#STEP_NAMES[@]} - fails - skips)) passed"
    exit 1
fi
echo "${GREEN}✅ all steps that ran passed${NC} -- ${skips} skipped (named above), $((${#STEP_NAMES[@]} - skips)) passed"

#!/usr/bin/env bash
# The full gate for this repository: the one command that decides "does it pass".
# `.github/workflows/ci.yml` runs it, and so does `septacore check` (through
# scripts/verify.sh, declared in .septacore.json).
#
#   .gnomon/ci.sh            run every step, print a per-step summary, and exit
#                            0 every step ran and passed   1 a step FAILED
#                            2 a step was SKIPPED (not measured)
#                            3 an ADVISORY step reported (docs owed a re-read)
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
# The numbers the ratchets hold are in scripts/coverage-floor.json,
# scripts/lint-baseline.json and scripts/gate-scope-floors.json; each says when
# and against what it was measured. What the docs may claim is held by
# conformance/declared_not_implemented.json and docs/CURRENT_STAGE.md.
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
    [ "$2" = "ADVISORY" ] && colour="$YELLOW"
    printf '%s%-8s%s %-20s %s\n' "$colour" "$2" "$NC" "$1" "$3"
}
show_tail() { echo "   ── last lines of $1 ──"; tail -n "${2:-25}" "$1" | sed 's/^/   | /'; }
header() { echo ""; echo "═══ $1 ═══"; }
# Run a command, print its output indented, return ITS status (not sed's).
indented() { "$@" > "$LOG_DIR/.indented" 2>&1; local rc=$?; sed 's/^/   /' "$LOG_DIR/.indented"; return $rc; }
# A smoke alarm for scope collapse: the count a step inspected must not fall
# under its floor in scripts/gate-scope-floors.json (scripts/gate_scope.py).
scope_floor() { indented "$PY" scripts/gate_scope.py floor "$1" "$2"; }
# Counts from a pytest JUnit report, read back from the tool rather than scraped.
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
# The frontend's toolchain is present when its tools resolve from frontend/ to a copy
# inside this repository. Not "frontend/node_modules exists": npm workspaces hoist
# everything they can to the root node_modules, so after an install whose versions all
# hoist that directory is never created (CI then skipped the frontend) -- and a Vite
# cache can create it with no tools in it (a local false "present").
frontend_toolchain() {
    ( cd frontend && node -e '
        const root = require("path").resolve("..") + require("path").sep;
        for (const m of ["typescript", "vitest", "eslint", "next"]) {
            if (!require.resolve(m + "/package.json").startsWith(root)) process.exit(1);
        }' ) >/dev/null 2>&1
}
echo "frontend toolchain: $(frontend_toolchain && echo "typescript, vitest, eslint, next resolve inside the repo" || echo ABSENT)"
echo "logs: $LOG_DIR"

# ── 1. Backend test suite + coverage floor ───────────────────────────────────
# The FULL suite (unit + integration + guardian) on SQLite, once, with coverage
# measured on that run. Coverage is judged on the full suite only -- see
# scripts/coverage-floor.json for why the old global --cov-fail-under went.
header "backend: pytest (full suite) + coverage floor"
COV_JSON="$LOG_DIR/coverage.json"
if [ -z "$PYBIN" ]; then
    record backend-pytest SKIPPED "no Python toolchain with pytest (set BACKEND_VENV)" 0
    record backend-coverage SKIPPED "no Python toolchain with pytest" 0
else
    t0=$SECONDS
    # SQLite, always: TEST_DATABASE_URL would move this run to PostgreSQL, which is
    # the next step's job (1b), and then nothing would run the SQLite schema.
    ( cd backend && env -u TEST_DATABASE_URL "$PYBIN/pytest" -q -p no:cacheprovider --cov=src \
        --cov-report="json:$COV_JSON" --junitxml="$LOG_DIR/pytest-junit.xml" ) \
        > "$LOG_DIR/backend-pytest.log" 2>&1
    rc=$?
    summary="$(grep -E '^=+ .*(passed|failed|error).* in [0-9.]+s' "$LOG_DIR/backend-pytest.log" | tail -1 | sed -E 's/=+//g; s/^ +| +$//g')"
    passed="$(echo "$summary" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
    passed="${passed:-0}"
    if [ "$rc" -eq 0 ] && [ "$passed" -gt 0 ]; then
        # Scope: the count must clear its floor, and EVERY backend/tests/**/test_*.py
        # must have reported a test (identity, read from the JUnit report) -- so a
        # test file that stops being collected fails here instead of vanishing.
        if scope_floor backend-tests "$passed" \
           && indented "$PY" scripts/gate_scope.py pytest-files --junit "$LOG_DIR/pytest-junit.xml"; then
            files="$(grep -oE 'pytest-files: [0-9]+/[0-9]+' "$LOG_DIR/.indented" | cut -d' ' -f2)"
            record backend-pytest PASS "$passed passed, ${files:-?} test files reported -- $summary" $((SECONDS - t0))
        else
            record backend-pytest FAIL "$passed passed, but the run's scope check failed (above)" $((SECONDS - t0))
        fi
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

# ── 1b. Backend test suite on PostgreSQL ─────────────────────────────────────
# The same full suite with TEST_DATABASE_URL set (postgresql+asyncpg://...):
# tests/conftest.py then builds a fresh database on that server from
# backend/src/db/migrations/*.sql, in order, instead of SQLITE_DDL. The step above
# runs only on SQLite, whose loose typing accepted what PostgreSQL refuses (a JSON
# string into TEXT[], an ISO string into TIMESTAMPTZ): the Guardian could not
# write audit_log on the production schema, and session_state and incidents had
# no migration at all. The full suite rather than integration/ + guardian/ alone:
# unit/ reaches the database too (interventions, the registry, triage), and the
# whole run takes about a minute. Coverage is judged once, on the step above.
#
# Unset is SKIPPED ("no TEST_DATABASE_URL"): not measured, so the gate exits 2.
# A skipped test is a FAIL here: in this run a skip is a PostgreSQL test that did
# not run (tests/integration/test_postgres_schema_parity.py). The URL is never
# printed -- it carries a password.
header "backend: pytest on PostgreSQL (TEST_DATABASE_URL)"
if [ -z "$PYBIN" ]; then
    record backend-pytest-postgres SKIPPED "no Python toolchain with pytest (set BACKEND_VENV)" 0
elif [ -z "${TEST_DATABASE_URL:-}" ]; then
    record backend-pytest-postgres SKIPPED "no TEST_DATABASE_URL" 0
else
    t0=$SECONDS
    ( cd backend && "$PYBIN/pytest" -q -p no:cacheprovider --no-cov -rs \
        --junitxml="$LOG_DIR/pytest-postgres-junit.xml" ) \
        > "$LOG_DIR/backend-pytest-postgres.log" 2>&1
    rc=$?
    read -r n_tests n_fail n_err n_skip < <(junit_counts "$PY" "$LOG_DIR/pytest-postgres-junit.xml")
    n_pass=$(( n_tests - n_fail - n_err - n_skip ))
    if [ "$rc" -eq 0 ] && [ "$n_pass" -gt 0 ] && [ "$n_skip" -eq 0 ]; then
        if scope_floor backend-tests "$n_pass" \
           && indented "$PY" scripts/gate_scope.py pytest-files --junit "$LOG_DIR/pytest-postgres-junit.xml"; then
            files="$(grep -oE 'pytest-files: [0-9]+/[0-9]+' "$LOG_DIR/.indented" | cut -d' ' -f2)"
            record backend-pytest-postgres PASS "$n_pass passed, 0 skipped, ${files:-?} test files reported -- schema from the migrations" $((SECONDS - t0))
        else
            record backend-pytest-postgres FAIL "$n_pass passed, but the run's scope check failed (above)" $((SECONDS - t0))
        fi
    elif [ "$rc" -eq 0 ] && [ "$n_pass" -gt 0 ]; then
        grep -E '^SKIPPED' "$LOG_DIR/backend-pytest-postgres.log" | head -5 | sed 's/^/   | /'
        record backend-pytest-postgres FAIL "$n_pass passed but $n_skip skipped -- a skip on PostgreSQL is a test that did not run" $((SECONDS - t0))
    else
        show_tail "$LOG_DIR/backend-pytest-postgres.log" 40
        record backend-pytest-postgres FAIL "exit $rc -- $n_pass passed, $n_fail failed, $n_err errors, $n_skip skipped" $((SECONDS - t0))
    fi
fi

# ── 2. Lint and type ratchets ────────────────────────────────────────────────
# Counts may not rise above scripts/lint-baseline.json, and the committed maxima
# may only fall. The args each tool runs with live in that file, next to the
# number they produced.
ratchet_step() {  # step-name entry [binary] [floor-id]
    local name="$1" entry="$2" bin="${3:-}" floor_id="${4:-}" t0=$SECONDS rc detail nfiles
    "$PY" scripts/lint_ratchet.py "$entry" ${bin:+--bin "$bin"} > "$LOG_DIR/$name.log" 2>&1
    rc=$?
    sed 's/^/   /' "$LOG_DIR/$name.log"
    if [ "$rc" -eq 0 ]; then
        detail="$(grep -m1 -oE '[0-9]+ findings over [0-9]+ files \(max [0-9]+\)' "$LOG_DIR/$name.log")"
        nfiles="$(echo "$detail" | grep -oE 'over [0-9]+' | grep -oE '[0-9]+')"
        if [ -n "$floor_id" ] && ! scope_floor "$floor_id" "${nfiles:-x}"; then
            record "$name" FAIL "$detail -- but under its scope floor (above)" $((SECONDS - t0))
            return
        fi
        record "$name" PASS "$detail" $((SECONDS - t0))
    else
        record "$name" FAIL "$(grep -m1 FAIL "$LOG_DIR/$name.log" | cut -c6-170)" $((SECONDS - t0))
    fi
}

header "backend: ruff ratchet"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/ruff" ]; then
    ratchet_step backend-ruff ruff "$PYBIN/ruff" backend-ruff-files
    ratchet_step scripts-ruff ruff-scripts "$PYBIN/ruff"
else
    record backend-ruff SKIPPED "ruff not installed in the backend toolchain" 0
    record scripts-ruff SKIPPED "ruff not installed in the backend toolchain" 0
fi

header "backend: mypy --strict ratchet"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/mypy" ]; then
    ratchet_step backend-mypy mypy "$PYBIN/mypy" backend-mypy-files
else
    record backend-mypy SKIPPED "mypy not installed in the backend toolchain" 0
fi

# ── 3. Frontend ──────────────────────────────────────────────────────────────
# Runs only when the frontend toolchain resolves (frontend_toolchain, above). --no-install everywhere: without it npx FETCHES a
# missing tool, which turns a 2s gate into a network call against whatever
# version the registry serves today.
header "frontend: tsc / vitest / eslint"
if ! frontend_toolchain; then
    for s in frontend-tsc frontend-vitest frontend-eslint; do
        record "$s" SKIPPED "frontend toolchain absent (typescript/vitest/eslint/next do not resolve inside the repo) -- run npm ci at the repo root" 0
    done
else
    # packages/semantic-types is a workspace dependency of the frontend, and its
    # package.json points at dist/. npm ci does not build workspaces, so on a fresh
    # checkout that directory is absent and the frontend's imported types resolve to
    # something else -- which is how tsc passed here for months on a stale local dist
    # and failed on CI's first run. Build it from source every time, so the gate
    # typechecks the same inputs everywhere.
    t0=$SECONDS
    ( cd packages/semantic-types && npx --no-install tsup src/index.ts --format cjs,esm --dts ) \
        > "$LOG_DIR/semantic-types-build.log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ] && [ -f packages/semantic-types/dist/index.d.ts ]; then
        record semantic-types-build PASS "dist/index.d.ts built from src (the frontend's types)" $((SECONDS - t0))
    else
        tail -15 "$LOG_DIR/semantic-types-build.log" | sed 's/^/   | /'
        record semantic-types-build FAIL "exit $rc -- the frontend typecheck below reads dist/" $((SECONDS - t0))
    fi

    t0=$SECONDS
    ( cd frontend && npx --no-install tsc --noEmit --listFiles ) > "$LOG_DIR/frontend-tsc.log" 2>&1
    rc=$?
    # --listFiles prints every file in the program; the project's own are the
    # ones outside node_modules. That count is what "typechecked" means here.
    n="$(grep '^/' "$LOG_DIR/frontend-tsc.log" | grep -vc node_modules || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -gt 0 ] && scope_floor frontend-tsc-files "$n"; then
        record frontend-tsc PASS "$n project files typecheck (tsc --noEmit)" $((SECONDS - t0))
    else
        grep -v '^/' "$LOG_DIR/frontend-tsc.log" | tail -25 | sed 's/^/   | /'
        record frontend-tsc FAIL "exit $rc over ${n:-0} project files" $((SECONDS - t0))
    fi

    t0=$SECONDS
    # The JSON report is what the per-file identity check reads: the default
    # reporter prints only totals when not on a terminal.
    ( cd frontend && npx --no-install vitest run --reporter=default --reporter=json \
        --outputFile.json="$LOG_DIR/vitest.json" ) > "$LOG_DIR/frontend-vitest.log" 2>&1
    rc=$?
    ESC=$(printf '\033')
    plain="$(sed -E "s/${ESC}\[[0-9;]*[A-Za-z]//g" "$LOG_DIR/frontend-vitest.log")"
    n="$(echo "$plain" | grep -E '^ *Tests +' | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || true)"
    files="$(echo "$plain" | grep -E '^ *Test Files +' | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -gt 0 ] && scope_floor frontend-tests "$n" \
       && indented "$PY" scripts/gate_scope.py vitest-files --json "$LOG_DIR/vitest.json"; then
        record frontend-vitest PASS "${n} tests passed in ${files:-?} files (every test file reported)" $((SECONDS - t0))
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
# levels drop that line (junit_counts, with the helpers at the top).
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
    if [ "$rc" -eq 0 ] && [ "$n_pass" -gt 0 ] && [ "$n_skip" -gt 0 ]; then
        # Every skip these suites can produce is an absent input to a registered
        # gate -- y0 for T0's cross-check, the T2/T3 data, the decision extra --
        # so a skipped test here is a gate that did not run, and green would say
        # it had. (Step-level SKIPPED stays for a whole toolchain being absent.)
        grep -E '^SKIPPED|SKIPPED \[' "$LOG_DIR/$name.log" | head -5 | sed 's/^/   | /'
        record "$name" FAIL "RAN: ${n_pass} passed but ${n_skip} skipped -- a skipped test here is a gate that did not run (pytest -rs for reasons)" $((SECONDS - t0))
    elif [ "$rc" -eq 0 ] && [ "$n_pass" -gt 0 ]; then
        record "$name" PASS "RAN: ${n_pass} passed, 0 skipped${extra} (env: ${PKG_PY%/bin/python})" $((SECONDS - t0))
    else
        show_tail "$LOG_DIR/$name.log" 30
        record "$name" FAIL "RAN: exit $rc -- ${n_pass} passed, ${n_fail} failed, ${n_err} errors, ${n_skip} skipped" $((SECONDS - t0))
    fi
    # Strict typing, per package, held at the count in scripts/lint-baseline.json.
    if grep -q "\"mypy-$name\"" scripts/lint-baseline.json; then
        if [ -x "$(dirname "$PKG_PY")/mypy" ]; then
            ratchet_step "$name-mypy" "mypy-$name" "$(dirname "$PKG_PY")/mypy"
        else
            record "$name-mypy" SKIPPED "mypy not installed in ${PKG_PY%/bin/python}" 0
        fi
    else
        record "$name-mypy" FAIL "no mypy-$name entry in scripts/lint-baseline.json -- an untyped package is not a pass" 0
    fi
done

# ── 5. Security: SAST, secrets, dependency advisories ────────────────────────
# bandit-*       bandit over backend/src + backend/mcp_server (with backend/pyproject.toml's
#                [tool.bandit]) and over every packages/*/src, through the lint ratchet:
#                MEDIUM and HIGH findings count, held at scripts/lint-baseline.json (0).
# secret-scan    scripts/secret_scan.py over every file git would commit from this working
#                tree (never the history). A finding prints a fingerprint, never the value;
#                test fixtures go in scripts/secret-scan-allowlist.json with a reason.
# pip-audit-*    scripts/dep_audit.py: pip-audit over the environments THIS run tests (the
#                backend toolchain above, and each distinct package environment), and
# npm-audit      npm audit over package-lock.json. Any advisory not excused by a reviewed,
#                unexpired entry in scripts/audit-allowlist.json FAILS. The advisory
#                databases are remote: unreachable is SKIPPED (not measured), never PASS.
header "security: bandit / secret scan / dependency audits"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/bandit" ]; then
    ratchet_step bandit-backend bandit-backend "$PYBIN/bandit"
    ratchet_step bandit-packages bandit-packages "$PYBIN/bandit"
else
    record bandit-backend SKIPPED "bandit not installed in the backend toolchain (backend[dev])" 0
    record bandit-packages SKIPPED "bandit not installed in the backend toolchain (backend[dev])" 0
fi

t0=$SECONDS
if indented "$PY" scripts/secret_scan.py; then
    detail="$(grep -m1 -oE 'secret-scan: .*' "$LOG_DIR/.indented" | sed 's/^secret-scan: //' | cut -c1-200)"
    n="$(echo "$detail" | grep -oE '[0-9]+ files scanned' | grep -oE '[0-9]+' | head -1)"
    if scope_floor secret-scan-files "${n:-0}"; then  # (scope_floor overwrites .indented)
        record secret-scan PASS "$detail" $((SECONDS - t0))
    else
        record secret-scan FAIL "${n:-0} files scanned -- under its scope floor (above)" $((SECONDS - t0))
    fi
else
    record secret-scan FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

audit_step() {  # step-name, then scripts/dep_audit.py's arguments
    local name="$1" t0=$SECONDS rc; shift
    indented "$PY" scripts/dep_audit.py "$@"
    rc=$?
    local line
    if [ "$rc" -eq 0 ]; then
        line="$(grep -m1 -oE '(pip-audit|npm audit)[^:]*: .*' "$LOG_DIR/.indented" | cut -c1-220)"
        record "$name" PASS "$line" $((SECONDS - t0))
    elif [ "$rc" -eq 2 ]; then
        record "$name" SKIPPED "$(grep -m1 -oE 'NOT MEASURED .*' "$LOG_DIR/.indented" | sed 's/^NOT MEASURED //' | cut -c1-180)" $((SECONDS - t0))
    else
        line="$(grep -m1 -oE 'FAIL (pip-audit|npm audit)[^:]*: .*' "$LOG_DIR/.indented" | sed 's/^FAIL //')"
        record "$name" FAIL "${line:-$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//')}" $((SECONDS - t0))
    fi
}
if [ -n "$PYBIN" ] && [ -x "$PYBIN/pip-audit" ]; then
    audit_step pip-audit-backend pip --pip-audit "$PYBIN/pip-audit" --python "$PY" --label backend
    # Each distinct package environment once (in CI all three packages share causal-core's).
    PKG_ENVS="$(for name in $PKG_ORDER; do pkg_python "$name" || true; done | awk 'NF && !seen[$0]++')"
    if [ -z "$PKG_ENVS" ]; then
        record pip-audit-packages SKIPPED "no package environment to audit (see the package rows above)" 0
    else
        i=0
        for env_py in $PKG_ENVS; do
            i=$((i + 1)); step=pip-audit-packages; [ "$i" -gt 1 ] && step="pip-audit-packages-$i"
            audit_step "$step" pip --pip-audit "$PYBIN/pip-audit" --python "$env_py" --label packages
        done
    fi
else
    record pip-audit-backend SKIPPED "pip-audit not installed in the backend toolchain (backend[dev])" 0
    record pip-audit-packages SKIPPED "pip-audit not installed in the backend toolchain (backend[dev])" 0
fi
audit_step npm-audit npm

# ── 6. Scope, status claims, documents ───────────────────────────────────────
# gate-scope     identity checks derived from the code: every skill module is
#                imported by a test, every registered MCP tool and every
#                state-changing route is referenced by one, every decorated route
#                is mounted. Today's gaps are listed by name in
#                scripts/gate-scope-floors.json; a NEW gap fails, and so does a
#                listed gap that has been closed but not struck off.
# status-claims  docs/CURRENT_STAGE.md and conformance/declared_not_implemented.json
#                agree with each other and with the tree (scripts/status_claims.py).
# contract-parity  the shared enumerations mean the same thing in the backend,
#                the published TypeScript contract and the frontend. tsc cannot
#                see this: a union redeclared in a second file is a second type,
#                so a frontend narrowing on a value the backend never sends
#                typechecks and matches nothing. Today's divergences are named in
#                scripts/contract-parity.json; a NEW one fails, and so does a
#                listed one that has been fixed but not struck off.
# doc-reconciliation  which documents are owed a re-read because the code they
#                describe changed. REPORTED, not failed -- a hard gate on every
#                code change would be switched off within a month (gnomon's
#                reasoning, kept). It fails only when its own manifest is wrong:
#                an untracked doc, or a subject path that matches nothing.
header "gate scope / status claims / doc reconciliation"
t0=$SECONDS
if [ -z "$PYBIN" ]; then
    record gate-scope SKIPPED "needs the backend toolchain (it loads the live MCP server and FastAPI app)" 0
elif indented "$PY" scripts/gate_scope.py identity-all; then
    detail="$(grep -oE '(skills-imported|mcp-tools|kernel-mcp-tools|routes-tested|routes-mounted): [0-9]+/[0-9]+ covered(, [0-9]+ known gap\(s\) listed)?' "$LOG_DIR/.indented" \
        | sed -E 's/ covered//; s/, ([0-9]+) known gap\(s\) listed/ (\1 known gaps)/' | paste -sd ';' - | sed 's/;/; /g')"
    record gate-scope PASS "$detail" $((SECONDS - t0))
else
    record gate-scope FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

t0=$SECONDS
if indented "$PY" scripts/status_claims.py; then
    record status-claims PASS "$(grep -m1 -oE 'register: .*' "$LOG_DIR/.indented" | cut -c1-230)" $((SECONDS - t0))
else
    record status-claims FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

t0=$SECONDS
if indented "$PY" scripts/contract_parity.py; then
    record contract-parity PASS "$(grep -m1 -oE 'contract-parity: .*' "$LOG_DIR/.indented" | sed 's/^contract-parity: //' | cut -c1-200)" $((SECONDS - t0))
else
    record contract-parity FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

# doc-figures: every benchmark number the docs quote matches the value in that
# benchmark's STANDING run (scripts/doc-figures.json binds each one). status-claims
# checks what a doc says a capability's status is; this checks the numbers.
t0=$SECONDS
if indented "$PY" scripts/doc_figures.py; then
    record doc-figures PASS "$(grep -m1 -oE 'doc figures: .*' "$LOG_DIR/.indented" | sed 's/^doc figures: //' | cut -c1-200)" $((SECONDS - t0))
else
    record doc-figures FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

t0=$SECONDS
if indented "$PY" scripts/doc_reconciliation.py; then
    tracked="$(grep -oE '[0-9]+ document\(s\) tracked' "$LOG_DIR/.indented" | grep -oE '[0-9]+')"
    moved="$(grep -oE '[0-9]+ owed a re-read' "$LOG_DIR/.indented" | grep -oE '[0-9]+' || true)"
    never="$(grep -oE '[0-9]+ never read against the code' "$LOG_DIR/.indented" | grep -oE '[0-9]+' || true)"
    owed=$(( ${moved:-0} + ${never:-0} ))
    if [ "$owed" -gt 0 ]; then
        # Drift, not failure: the code a document describes moved since it was last read.
        # ADVISORY makes the gate exit 3 ("regressed") so it is acted on, not merely listed.
        record doc-reconciliation ADVISORY "${tracked:-?} docs tracked; owed a re-read: ${moved:-0} (code moved) + ${never:-0} (never read) -- read them, then scripts/doc_reconciliation.py --bless" $((SECONDS - t0))
    else
        record doc-reconciliation PASS "${tracked:-?} docs tracked; every one read against the code it describes" $((SECONDS - t0))
    fi
else
    record doc-reconciliation FAIL "$(grep -m1 FAIL "$LOG_DIR/.indented" | sed 's/^ *FAIL *//' | cut -c1-150)" $((SECONDS - t0))
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "═══ ci.sh summary ═══"
fails=0; skips=0; advisories=0
printf '%-20s %-8s %5s  %s\n' STEP STATUS SECS DETAIL
for i in "${!STEP_NAMES[@]}"; do
    printf '%-20s %-8s %5s  %s\n' "${STEP_NAMES[$i]}" "${STEP_STATUS[$i]}" "${STEP_SECS[$i]}" "${STEP_DETAIL[$i]}"
    [ "${STEP_STATUS[$i]}" = "FAIL" ] && fails=$((fails + 1))
    [ "${STEP_STATUS[$i]}" = "SKIPPED" ] && skips=$((skips + 1))
    [ "${STEP_STATUS[$i]}" = "ADVISORY" ] && advisories=$((advisories + 1))
done
echo ""
# The exit contract (SeptaCore SPEC-0012 DG-7, which took it from this operator's own
# verify.sh): 0 every step ran and passed; 1 a blocking step failed; 2 could not measure
# -- a step was SKIPPED because its toolchain is absent, and an unmeasured step is not a
# passed one; 3 an advisory regressed (documents drifted from the code they describe).
passed=$(( ${#STEP_NAMES[@]} - fails - skips - advisories ))
if [ "$fails" -gt 0 ]; then
    echo "${RED}❌ ${fails} step(s) FAILED${NC}, ${skips} not measured, ${advisories} advisory, ${passed} passed"
    exit 1
fi
if [ "$skips" -gt 0 ]; then
    echo "${YELLOW}⚠ NOT MEASURED: ${skips} step(s) could not run (named above)${NC} -- ${passed} passed, ${advisories} advisory"
    exit 2
fi
if [ "$advisories" -gt 0 ]; then
    echo "${YELLOW}△ REGRESSED: ${advisories} advisory step(s) (named above)${NC} -- ${passed} passed"
    exit 3
fi
echo "${GREEN}✅ every step ran and passed${NC} -- ${passed} passed"

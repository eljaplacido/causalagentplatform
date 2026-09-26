#!/usr/bin/env bash
# Fast post-write check for this repository -- the [verify] command in policy.toml.
#
# gnomon runs it after a turn that wrote or edited a file, through the ordinary
# bash tool (so the role's bash_deny / bash_allow and the tool timeout apply).
# Two properties matter more than coverage here:
#
#   FAST. It runs after every writing turn. Everything slow -- the full suite
#   with coverage, mypy --strict, the frontend suites -- is in .gnomon/ci.sh.
#
#   NON-RECURSIVE. It must never invoke gnomon, .gnomon/ci.sh, or anything that
#   runs a gnomon turn, or a write would re-enter the gate that judges it. Nothing
#   under backend/tests starts gnomon; keep it that way.
#
# WHY IT COUNTS WHAT IT CHECKED. gnomon measured its own verify.sh passing in 2ms
# in a tree where it matched nothing: every loop iteration `continue`d, the fail
# flag never moved, and `gnomon task` read exit 0 as "the edit typechecks". A gate
# that inspected nothing may not report success, so this one sums what it
# actually looked at and exits 1 when that sum is zero. The last line names the
# counts, so the pass is never larger than the evidence.
#
# What it checks:
#   1. Python files changed in the working tree (tracked + untracked, under
#      backend/, scripts/, packages/): ruff's hard-error rules only -- syntax
#      errors and undefined names (E9, F63, F7, F82). Not the full rule set:
#      ~450 style findings pre-exist, and a check that fails on code the turn did
#      not write teaches the model to fight the gate instead of the bug.
#   2. The ruff ratchet over all of backend/ (scripts/lint_ratchet.py): the
#      finding count may not rise above scripts/lint-baseline.json.
#   3. The backend unit + guardian suites, no coverage, stop at first failure.
#   4. `tsc --noEmit` on the frontend, only if the turn changed a .ts/.tsx file
#      there AND typescript resolves from frontend/ inside the repo (not "frontend/
#      node_modules exists": npm workspaces hoist it to the root). Said out loud when skipped.
#
# NOT here, deliberately: mypy (10s+), the integration suite, coverage, vitest,
# eslint, gate_scope, doc reconciliation. .gnomon/ci.sh runs all of them.
set -uo pipefail
cd "$(dirname "$0")/.."

# The backend virtualenv. BACKEND_VENV wins; otherwise backend/.venv here; and in
# a git worktree (which has no venv of its own) the main checkout's backend/.venv,
# derived from git rather than written down -- an absolute path in the surface
# would be machine-scoped configuration.
resolve_venv() {
    local c
    for c in "${BACKEND_VENV:-}" "backend/.venv" \
             "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo /nonexistent/.git)")/backend/.venv"; do
        if [ -n "$c" ] && [ -x "$c/bin/python" ]; then
            (cd "$c" && pwd)
            return 0
        fi
    done
    return 1
}

# That borrowed venv's editable installs of causal_core / causal_decision point at the
# main checkout; every packages/*/src of THIS tree goes first so the suites below test
# the code here, not there (the same rule as .gnomon/ci.sh and its import-origin step).
PKG_SRC_PATH="$(for d in "$(pwd)"/packages/*/src; do [ -d "$d" ] && printf '%s:' "$d"; done)"
export PYTHONPATH="${PKG_SRC_PATH%:}${PYTHONPATH:+:$PYTHONPATH}"

if ! VENV="$(resolve_venv)"; then
    echo "❌ verify: no backend virtualenv (tried \$BACKEND_VENV, backend/.venv, the main checkout's)."
    echo "   Nothing was checked, and a gate that checked nothing does not pass."
    exit 1
fi

fail=0
py_changed=0
ratchet_files=0
tests_passed=0
ts_files=0

# ── 1. hard errors in changed Python ─────────────────────────────────────────
# A single `*` in a git pathspec crosses `/`, so 'backend/*.py' reaches
# backend/src/skills/x.py. (`**/` would REQUIRE an intermediate directory and
# miss files one level down -- the pathspec bug gnomon's gate_scope exists for.)
mapfile -t CHANGED_PY < <(
    { git diff --name-only HEAD -- 'backend/*.py' 'scripts/*.py' 'packages/*.py' 2>/dev/null
      git ls-files --others --exclude-standard -- 'backend/*.py' 'scripts/*.py' 'packages/*.py' 2>/dev/null
    } | sort -u | while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done
)
if [ "${#CHANGED_PY[@]}" -gt 0 ]; then
    if "$VENV/bin/ruff" check --select E9,F63,F7,F82 --output-format=concise "${CHANGED_PY[@]}"; then
        py_changed=${#CHANGED_PY[@]}
        echo "✅ ruff hard-error rules: ${py_changed} changed Python file(s) clean"
    else
        echo "❌ ruff hard-error rules failed on changed Python (above)"
        fail=1
    fi
else
    echo "   no changed Python files for the hard-error check"
fi

# ── 2. ruff ratchet ──────────────────────────────────────────────────────────
RATCHET_OUT="$("$VENV/bin/python" scripts/lint_ratchet.py ruff --bin "$VENV/bin/ruff" 2>&1)"
RATCHET_RC=$?
echo "$RATCHET_OUT" | grep -v '^note monotonic' || true
if [ "$RATCHET_RC" -eq 0 ]; then
    ratchet_files=$(echo "$RATCHET_OUT" | grep -oE 'over [0-9]+ files' | grep -oE '[0-9]+' | head -1)
    ratchet_files=${ratchet_files:-0}
else
    fail=1
fi

# ── 3. unit + guardian suites ────────────────────────────────────────────────
PYTEST_LOG="$(mktemp)"
trap 'rm -f "$PYTEST_LOG"' EXIT
( cd backend && "$VENV/bin/pytest" tests/unit tests/guardian -q -x --no-cov -p no:cacheprovider ) \
    > "$PYTEST_LOG" 2>&1
PYTEST_RC=$?
tests_passed=$(grep -oE '[0-9]+ passed' "$PYTEST_LOG" | tail -1 | grep -oE '[0-9]+' || true)
tests_passed=${tests_passed:-0}
if [ "$PYTEST_RC" -eq 0 ] && [ "$tests_passed" -gt 0 ]; then
    echo "✅ pytest tests/unit tests/guardian: ${tests_passed} passed"
else
    tail -30 "$PYTEST_LOG"
    echo "❌ pytest tests/unit tests/guardian failed (exit ${PYTEST_RC}, ${tests_passed} passed)"
    fail=1
fi

# ── 4. frontend typecheck, only when the turn touched frontend TypeScript ────
mapfile -t CHANGED_TS < <(
    { git diff --name-only HEAD -- 'frontend/*.ts' 'frontend/*.tsx' 'packages/semantic-types/*.ts' 2>/dev/null
      git ls-files --others --exclude-standard -- 'frontend/*.ts' 'frontend/*.tsx' 'packages/semantic-types/*.ts' 2>/dev/null
    } | sort -u
)
if [ "${#CHANGED_TS[@]}" -gt 0 ]; then
    if ( cd frontend && node -e 'const r=require("path").resolve("..")+require("path").sep; if (!require.resolve("typescript/package.json").startsWith(r)) process.exit(1)' ) >/dev/null 2>&1; then
        TSC_LOG="$(mktemp)"
        if ( cd frontend && npx --no-install tsc --noEmit --listFiles ) > "$TSC_LOG" 2>&1; then
            ts_files=$(grep '^/' "$TSC_LOG" | grep -vc 'node_modules' || true)
            echo "✅ tsc --noEmit: ${ts_files} project file(s) typecheck"
        else
            grep -v '^/' "$TSC_LOG" | tail -30
            echo "❌ tsc --noEmit failed"
            fail=1
        fi
        rm -f "$TSC_LOG"
    else
        echo "⏭  SKIPPED tsc: ${#CHANGED_TS[@]} frontend TypeScript file(s) changed but typescript does not resolve inside the repo (run npm ci) -- NOT typechecked"
    fi
fi

checked=$((py_changed + ratchet_files + tests_passed + ts_files))
echo "verify: ${py_changed} changed .py hard-checked · ruff ratchet over ${ratchet_files} file(s) · ${tests_passed} test(s) passed · ${ts_files} TS file(s) typechecked"
if [ "$checked" -eq 0 ]; then
    echo "❌ verify inspected nothing -- refusing to report success"
    exit 1
fi
exit "$fail"

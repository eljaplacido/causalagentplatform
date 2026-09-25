+++
name = "verifying changes"
description = "Which command decides pass/fail in this repository, what its ratchets and gates mean, and how to move one honestly"
match = '\b(verify|verifying|ci|ci\.sh|gate|ratchet|baseline|coverage|ruff|mypy|lint|done|passing|green|pytest|t0|t2|t3|t4|d0|parity)\b'
roles = ["implementor", "verifier", "implement", "critique", "coordinator"]
+++

**`.gnomon/ci.sh` is the one command that decides.** It runs every step even
after one fails and ends with a per-step table: PASS, FAIL or SKIPPED, each
with a count of what it inspected. Paste that table; do not summarise it.

- A step that inspected nothing is a FAIL, never a pass.
- SKIPPED means the tool, or the config it needs, is absent -- and says which.
  A SKIPPED step checked nothing; never report it as passing.
- `.gnomon/verify.sh` is the fast check gnomon runs after a write. Passing it
  is not passing ci.sh.

**What it runs**, in order: the backend suite with coverage; the ruff and
mypy ratchets; the frontend (tsc, vitest, eslint -- SKIPPED without
`frontend/node_modules`); one row per Python package under `packages/`
(causal-core first, then causal-decision and causal-mcp -- the set is derived
from the directory, not listed); then gate-scope, status-claims,
contract-parity and doc-reconciliation.

**The pre-registered gates run inside the package rows.** A plain `pytest` in
the package runs every marker:

| Gate | Where | Marker | Pre-registration |
|---|---|---|---|
| T0 analytic SCMs | packages/causal-core | `t0` | benchmarks/t0/ |
| T2 IHDP (real covariates) | packages/causal-core | `real_data` | benchmarks/t2-ihdp/ |
| T3 LaLonde (real data) | packages/causal-core | `real_data` | benchmarks/t3-lalonde/ |
| T4 declared segments | packages/causal-core | `t4` | benchmarks/t4-segments/ |
| D0 decision table | packages/causal-decision | `d0` | benchmarks/d0-decision-policy/ |

T2 and T3 SKIP unless their data is cached (`~/.cache/causal-core`) or
`CAUSAL_FETCH=1`; a skipped real-data gate is not a passed one -- the row's
`skipped` count says so. Each gate's seeded-defect tests are part of its
marker.

**The ratchets.** Two numbers are committed, each with a comment saying when
and against which commit it was measured:

| File | Holds | Direction |
|---|---|---|
| `scripts/coverage-floor.json` | backend line coverage, full suite | may only go UP |
| `scripts/lint-baseline.json` | max ruff / mypy findings | may only go DOWN |

When your change improves a number, raise (or lower) the committed value in
the same change -- the scripts print the exact value to write. Moving one the
wrong way fails against the merge base with main unless the commit message
carries `Ratchet-Exempt: <why>`. That line is for a real reason (a tool
upgrade that changed what it counts), never to make a red gate green.

**Four more checks guard what the tree and the docs say:**

- `scripts/gate_scope.py identity-all` -- every skill module imported by a
  test, every MCP tool and state-changing route referenced by one. Today's
  gaps are named in `scripts/gate-scope-floors.json` (`known_gaps`), next to
  the `floors` each step's count must clear; closing a gap means striking it
  off in the same change, or the gate fails.
- `scripts/status_claims.py` -- `docs/CURRENT_STAGE.md` and
  `conformance/declared_not_implemented.json` agree with each other and with
  the tree. If you change a line the register cites, re-cite it (see
  register-hygiene).
- `scripts/contract_parity.py` -- the shared enumerations say the same thing
  in the backend, `packages/semantic-types` and the frontend. Today's real
  divergences are named in `scripts/contract-parity.json`; a new one fails,
  and so does a listed one that has been fixed but not removed.
- `scripts/doc_reconciliation.py` -- which docs are owed a re-read. Reported,
  not failed. After actually reading one against the code:
  `--bless <doc> --by '<who>'`.

**Run pieces directly** when iterating -- implementor and implement only.
The verifier's bash_allow refuses a language runtime, so the python-script
gates above are the verifier's through `.gnomon/ci.sh`, not one by one.

    # backend (from the repo root)
    backend/.venv/bin/python scripts/lint_ratchet.py ruff --bin backend/.venv/bin/ruff
    backend/.venv/bin/python scripts/lint_ratchet.py mypy --bin backend/.venv/bin/mypy
    cd backend && .venv/bin/pytest tests/unit -q --no-cov

    # packages: all three run in the kernel's venv
    cd packages/causal-core && .venv/bin/pytest -m "not t0 and not t4 and not slow"
    cd packages/causal-core && .venv/bin/pytest -m t0
    cd packages/causal-decision && ../causal-core/.venv/bin/pytest
    cd packages/causal-mcp && ../causal-core/.venv/bin/pytest

    # cross-cutting (the verifier gets these from ci.sh)
    python3 scripts/status_claims.py
    python3 scripts/contract_parity.py
    backend/.venv/bin/python scripts/gate_scope.py identity-all

In a git worktree there is no `backend/.venv` or `packages/causal-core/.venv`;
ci.sh and verify.sh find the main checkout's through git on their own, or
honour `BACKEND_VENV` / `PKG_VENV_CAUSAL_CORE`. Run by hand there, a venv
whose editable install points at the main checkout tests THAT tree's code
unless `PYTHONPATH` puts this tree's `src/` first -- ci.sh does that for you.

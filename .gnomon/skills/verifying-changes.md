+++
name = "verifying changes"
description = "Which command decides pass/fail in this repository, what its ratchets mean, and how to move one honestly"
match = '\b(verify|verifying|ci|ci\.sh|gate|ratchet|baseline|coverage|ruff|mypy|lint|done|passing|green)\b'
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

**The ratchets.** Three numbers are committed, each with a comment saying when
and against which commit it was measured:

| File | Holds | Direction |
|---|---|---|
| `scripts/coverage-floor.json` | backend line coverage, full suite | may only go UP |
| `scripts/lint-baseline.json` | max ruff / mypy findings | may only go DOWN |

When your change improves a number, lower (or raise) the committed value in the
same change -- the scripts print the exact value to write. Moving one the wrong
way fails against the merge base with main unless the commit message carries
`Ratchet-Exempt: <why>`. That line is for a real reason (a tool upgrade that
changed what it counts), never to make a red gate green.

**Three more checks guard what the docs say:**

- `scripts/gate_scope.py identity-all` -- every skill module imported by a
  test, every MCP tool and state-changing route referenced by one. Today's
  gaps are named in `scripts/gate-scope-floors.json`; closing one means
  striking it off in the same change, or the gate fails.
- `scripts/status_claims.py` -- `docs/CURRENT_STAGE.md` and
  `conformance/declared_not_implemented.json` agree with each other and with
  the tree. If you change a line the register cites, re-cite it.
- `scripts/doc_reconciliation.py` -- which docs are owed a re-read. After
  actually reading one against the code: `--bless <doc> --by '<who>'`.

**Run pieces directly** when iterating (from the repo root):

    backend/.venv/bin/python scripts/lint_ratchet.py ruff --bin backend/.venv/bin/ruff
    backend/.venv/bin/python scripts/lint_ratchet.py mypy --bin backend/.venv/bin/mypy
    cd backend && .venv/bin/pytest tests/unit -q --no-cov

In a git worktree there is no `backend/.venv`; both gate scripts find the main
checkout's venv through git on their own, or honour `BACKEND_VENV`.

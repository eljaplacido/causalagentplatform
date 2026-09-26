# Contributing to the Causal Platform

Thanks for considering a contribution. The platform makes causal claims that people act on, so the
bar leans toward "correct, measured and honest about its limits" rather than "fast and small". This
document explains how to clear it. Coding agents: start at [`AGENTS.md`](AGENTS.md), which states
the same rules in brief.

## Layout

| Directory | What lives there |
|---|---|
| `packages/causal-core` | The causal kernel: identification, estimation, refutation, sensitivity, declared segments. No web or LLM dependencies. |
| `packages/causal-decision` | Evidence → verdict (`act`, `act_with_approval`, `experiment`, `refuse`), the hash-chained decision ledger, outcome follow-up and calibration. |
| `packages/causal-mcp` | The kernel's MCP server, the repository's one MCP server. |
| `packages/semantic-types` | The published TypeScript contract for the semantic layer. |
| `backend/` | FastAPI platform: governance (Guardian), semantic layer, API, agents. Its causal skills delegate to the kernel. It serves no MCP tools. |
| `frontend/` | Next.js UI. |
| `benchmarks/` | Pre-registered correctness gates and every run's result (see [`benchmarks/README.md`](benchmarks/README.md)). |
| `conformance/` | The claim contract's published JSON Schemas, and `declared_not_implemented.json`, the register of what the docs claim and the code does not do. |
| `.gnomon/` | The agent harness surface: roles, tools, skills, and `ci.sh`, the gate. |
| `scripts/` | The gate's own checks: ratchets, gate scope, status claims, contract parity, doc reconciliation. |

Before a change, read [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md): what is implemented (each
row links the test that pins it) and what is not. [`docs/AGENTS.md`](docs/AGENTS.md) and the other
docs under `docs/` are specifications; where they describe more than the code does, the register
says so.

## Setting up

Python 3.12 and Node 24 LTS, as CI uses. Two Python environments, one per lock, as CI builds them: the
backend's, and the kernel's (the three kernel packages, with causal-mcp on MCP SDK v2 and the kernel
suites' own test dependencies):

Each installs from a hash-pinned lock in `requirements/` (every distribution pinned, every file
checked against its sha256, nothing else resolved), then this checkout's own packages, which are
not on an index and so not in the lock, with `--no-deps` (they pull in nothing) and
`--no-build-isolation` (they build with the `hatchling` the lock installed):

```bash
# Backend (backend/.venv) -- the kernel and decision layer are editable installs
python -m venv backend/.venv
backend/.venv/bin/python -m pip install --require-hashes --no-deps -r requirements/backend.txt
backend/.venv/bin/python -m pip install --no-deps --no-build-isolation \
  -e packages/causal-core -e packages/causal-decision -e "backend[dev]"

# Kernel (packages/causal-core/.venv) -- also runs causal-decision and causal-mcp
python -m venv packages/causal-core/.venv
packages/causal-core/.venv/bin/python -m pip install --require-hashes --no-deps -r requirements/kernel.txt
packages/causal-core/.venv/bin/python -m pip install --no-deps --no-build-isolation \
  -e "packages/causal-core[dev,crosscheck,ml]" \
  -e "packages/causal-decision[dev]" \
  -e "packages/causal-mcp[dev,http,decision]"

# Frontend (npm workspaces, from the repo root)
npm ci
```

These are the steps `.github/workflows/ci.yml` runs. Running and launching the platform:
[`docs/QUICKSTART.md`](docs/QUICKSTART.md).

### Adding or changing a Python dependency

Edit the `pyproject.toml`, re-lock, and commit both in the same change:

```bash
python3 scripts/py_lock.py relock      # needs uv (python3 -m pip install uv); --upgrade moves every pin
```

`relock` rewrites `requirements/*.txt` from `requirements/*.in` and records what they were made
from in `requirements/lock-inputs.json`. The gate's `python-lock` step fails a dependency edit
that was not re-locked, and also requires your environments to equal their locks; after a re-lock,
or a pull that brought one, `python3 scripts/py_lock.py sync backend --python backend/.venv/bin/python`
(and `sync kernel --python packages/causal-core/.venv/bin/python`) brings a venv back in line.
Details: [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md) §5.4.

### Dependabot PRs

The gate decides: a bot PR is merged only when CI is green, and only after you have read what
moved. A semver-major is a ledger item, not a bot PR (`septacore create --provenance
"dependabot:#<n>"`), and the bot is told to ignore it until that item is done. The pins the
ratchets depend on (ruff, mypy, bandit, pip-audit, each pinned to a minor) are never moved by
the bot: `.github/dependabot.yml` ignores them, and moving one means re-measuring its baseline
by hand. How to handle a red npm PR, whose lock is often the problem rather than the update, is
in [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md) §5.4.

## The gate

```bash
bash .gnomon/ci.sh        # or: septacore check  (the same gate, declared in .septacore.json)
```

This is the one command that decides whether a change passes, and it is what CI runs. It runs every
step even after one fails and ends with a per-step table: the backend suite and its coverage floor,
the same suite on PostgreSQL (set `TEST_DATABASE_URL`; unset, that step is skipped), the ruff and
mypy ratchets, the frontend (tsc, vitest, eslint), each package's suite including the
pre-registered gates, the Python locks, the security scans (bandit, the secret scan, pip-audit and npm audit), gate
scope, status claims, contract parity, doc figures, skill references and doc reconciliation. The
step names and what each checks are in
[`.gnomon/skills/verifying-changes.md`](.gnomon/skills/verifying-changes.md) and
[`docs/TESTING_CICD.md`](docs/TESTING_CICD.md). A step that inspected nothing fails; a SKIPPED step names what was absent. It exits
0 (every step ran and passed), 1 (a step failed), 2 (a step was skipped: not measured, which is not
a pass) or 3 (docs are owed a re-read). Only 0 is done; CI fails on 1 and 2 and warns on 3. Put the
summary table in your PR description.

What to work on comes from the ledger: `septacore ready` lists `.beads/issues.jsonl` by priority.

The ratchets (`scripts/coverage-floor.json`, `scripts/lint-baseline.json`) only move one way. If
your change improves a number, move the committed value in the same change; moving one the wrong
way needs a `Ratchet-Exempt: <why>` line in the commit message and a real reason.

## Rules that are specific to this repository

### Kernel first

A new causal capability goes into `packages/causal-core` (or `packages/causal-decision`), with:

1. a **typed contract** (a contract change bumps `CONTRACT_VERSION` and regenerates the schemas in
   `conformance/causal-claims/`);
2. **influence-function standard errors**, not a constant or a convenience bootstrap;
3. a **pre-registered benchmark** (below);
4. a **seeded-defect negative control**: a plausible wrong implementation that the gate is shown
   to fail on.

Do not add modules to `backend/src/skills/`, and do not add an MCP server to `backend/`. The
backend reaches new methods through the kernel; agents reach them through `packages/causal-mcp`.
The working recipe is [`.gnomon/skills/adding-an-estimator.md`](.gnomon/skills/adding-an-estimator.md).

### Pre-register before you measure

- `benchmarks/<gate>/PRE-REGISTRATION.md`, with its pass bars in a TOML block, is committed
  **before** the capability produces a result. The gate test reads its bars from that block.
- Results are committed as `benchmarks/<gate>/results/<date>-v<version>-run<N>.json`,
  **including failing runs**. A failing run is never deleted or overwritten.
- A bar changes only with a version bump and a dated changelog entry in the pre-registration.
- Tests of causal numbers assert against a known truth (a simulated structural model whose effect
  you set, or an experimental benchmark), never against what the code returned last time.

Details: [`.gnomon/skills/benchmark-discipline.md`](.gnomon/skills/benchmark-discipline.md).

### Keep the claims true

- `conformance/declared_not_implemented.json` lists capabilities the docs claim and the code does
  not deliver. Each citation is `file:line` plus a verbatim quote, checked by
  `scripts/status_claims.py` on every run.
- If you edit a line the register cites, re-cite it in the same change.
- If you find a new overclaim, add an entry (and its `dni:<id>` row on `docs/CURRENT_STAGE.md`).
- An entry leaves the list only by being implemented -- move it to `left_the_list` with `fixed_in`
  (the commit) and `pinned_by` (the test) -- or by the claim being retracted. Never by deletion.
- Do not mark anything done on `docs/CURRENT_STAGE.md` without a test that proves it.

Details: [`.gnomon/skills/register-hygiene.md`](.gnomon/skills/register-hygiene.md).

### Governance

- Every new write path to the semantic layer goes through the Guardian and writes an audit entry.
  No UPDATE or DELETE on `audit_log`.
- An agent must never be able to grant itself an approval; approval is not an MCP tool.

## Branches, commits and pull requests

- Branch from `main` with a descriptive prefix (`feat/`, `fix/`, `docs/`, `chore/`, `refactor/`,
  `test/`), one branch and one PR per piece of work. A change that needs another unmerged one is
  a stacked PR, based on that branch and retargeted to `main` when it merges.
- Commit SHAs are cited: a register entry's `fixed_in`, a benchmark result's `git`. So history
  that has been cited is never rewritten. Rebase a branch onto `main` only before anything cites
  one of its commits, never force-push `main`, and merge PRs with a **merge commit** -- never
  squash or rebase-merge, which would orphan every SHA the branch recorded.
  `scripts/status_claims.py` fails a `fixed_in` that is not an ancestor-reachable commit.
- A PR merges when CI's gate is green (exit 0; exit 3 only with the owed readings named).
- Commit subjects follow `<type>(<scope>): <short summary>`, with `<type>` one of `feat`, `fix`,
  `refactor`, `docs`, `test`, `chore`, `perf`, `build`, `ci`. The body explains *why*.
- A PR says what changed, why, and how it was verified, and includes the `ci.sh` summary table.
- A [Developer Certificate of Origin](https://developercertificate.org/) sign-off
  (`git commit -s`) is welcome but not required; nothing checks for it.

## What kind of contributions we want

**High value**

- Kernel capabilities that close a register entry, with the four things listed under *Kernel first*.
- Stronger gates: a seeded defect the current suite cannot see, or a case that tests a documented
  limit.
- Backend code moved off heuristic skills and onto the kernel.
- Connectors in `backend/src/connectors/`.
- Documentation that brings a claim in line with the code, or retracts it.

**Discuss first**

- Changes to the claim contract, the `EpistemicState` shape or the orchestration graph.
- Replacing established dependencies (FastAPI, LangGraph, the MCP SDK, Next.js).
- Performance work without a benchmark showing a real bottleneck.

**Out of scope**

- Code that bypasses the Guardian, or a refusal, to make something pass.
- Mock data in production code paths (mock fallback must be env-gated).
- Vendor-locked integrations, such as a hard-coded LLM provider.

## Style

**Python.** `ruff` and `mypy --strict` (config in each `pyproject.toml`; the backend is held by the
ratchets, the packages are strict). Type hints on every public function. `structlog.get_logger`
for new backend modules, and never structlog-style keyword arguments on a stdlib logger. Short
docstrings; say what a function refuses to do as well as what it does.

**TypeScript.** Strict mode. Named exports. Canonical types come from
`@causalplatform/semantic-types`; UI-only extensions go in `frontend/src/lib/types.ts`, and
`scripts/contract_parity.py` fails when a shared name diverges. Network calls go through
`frontend/src/lib/api.ts`.

## Reporting bugs

Open a GitHub issue with what you expected, what happened, a minimal reproduction, and your Python,
Node and OS versions. A wrong causal number is a bug even when nothing crashes: include the data (or
a generator for it) and the graph.

For a **security** issue, use the process in [`SECURITY.md`](SECURITY.md) instead.

## Code of conduct and licence

Participation is governed by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). By contributing, you agree
that your contributions are licensed under the [Apache License, Version 2.0](LICENSE).

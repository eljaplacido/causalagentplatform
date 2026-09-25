# Testing, CI/CD & Engineering Best Practices

> **Scope:** What is tested, what blocks a merge, and how CI and releases run, for the Python backend, the Python packages under `packages/`, and the React frontend.
>
> **Read against the code 2026-09-25.** This page describes the gate that runs. The earlier
> version of this page described a ten-job pipeline, performance suites and zero-error gates,
> none of which were built. It is kept in
> [`history/TESTING_CICD-target-2026-09-25.md`](history/TESTING_CICD-target-2026-09-25.md)
> for its design intent.

---

## 1. Testing Philosophy

The platform makes causal claims. A bug in a recommendation engine is not just a software defect—it is a potentially wrong causal attribution that an operator acts on. Testing must therefore go beyond functional correctness to validate **causal correctness, governance integrity, and epistemic reliability**.

### Testing Priorities (in order)

1. **Guardian integrity** — If the Guardian can be bypassed, nothing else matters.
2. **Causal correctness** — DAG structural validity, identification assumptions, estimation accuracy.
3. **Audit trail immutability** — Append-only contracts must never be broken.
4. **Agent behavior contracts** — EpistemicState in/out types, skill output schemas.
5. **UX contract** — Critical UX invariants (thinking indicator latency, approval flow).
6. **Integration correctness** — Connector data flows, semantic layer writes.
7. **Performance** — no performance suite exists yet (§6).

Two rules the gate holds itself to: **a check that inspected nothing may not pass**, and
**counts are read back from the tools, never written down**. So this page gives no test
counts or coverage figures. `.gnomon/ci.sh` prints them on every run, and the ratchet files in
§4 record each measurement with its date and commit.

---

## 2. The gate: `.gnomon/ci.sh`

One script decides "does it pass". Developers run it locally, and CI runs the same script.
Every step runs even after an earlier one fails. A step whose toolchain is absent is
**SKIPPED** by name with the reason. A tool that is present and crashes is a **FAIL**.

| Step | What it runs | Passes when |
|---|---|---|
| `backend-pytest` | The full backend suite (`backend/tests`: unit, integration, guardian) once, with coverage | Exit 0, a non-zero pass count above its floor (`scripts/gate-scope-floors.json`), and every `backend/tests/**/test_*.py` reported a test in the JUnit report |
| `backend-coverage` | `scripts/coverage_gate.py` on that run's coverage | Line coverage ≥ the floor in `scripts/coverage-floor.json` |
| `backend-ruff`, `scripts-ruff` | `scripts/lint_ratchet.py` (ruff over `backend/`, and over `scripts/`) | Findings ≤ `max_findings` in `scripts/lint-baseline.json` (`scripts/` is held at 0) |
| `backend-mypy` | `mypy src/ --strict` via the ratchet | Findings ≤ its `max_findings` |
| `semantic-types-build` | Builds `packages/semantic-types/dist` from source | `dist/index.d.ts` exists (the frontend typecheck reads it) |
| `frontend-tsc` | `tsc --noEmit` in `frontend/` | Exit 0 over at least its floor of project files |
| `frontend-vitest` | `vitest run` in `frontend/` | Exit 0, pass count above its floor, every test file reported |
| `frontend-eslint` | The eslint ratchet, once a config exists | **SKIPPED today**: `frontend/` has no `eslint.config.*`, and eslint 9 refuses to run without one |
| one step per `packages/*/pyproject.toml` | That package's own `pytest`, in its own environment | Exit 0 and at least one test passed. `causal-core` is always named in the summary, RAN or NOT RUN |
| `gate-scope` | `scripts/gate_scope.py identity-all` | Every skill module is imported by a test, every MCP tool and state-changing route is referenced by one, and every route is mounted, apart from the known gaps named in `scripts/gate-scope-floors.json` |
| `status-claims` | `scripts/status_claims.py` | `docs/CURRENT_STAGE.md` and `conformance/declared_not_implemented.json` agree with each other and with the tree |
| `contract-parity` | `scripts/contract_parity.py` | The shared enumerations agree across `models.py`, `packages/semantic-types` and `frontend/src/lib/types.ts`, apart from the divergences named in `scripts/contract-parity.json` |
| `doc-reconciliation` | `scripts/doc_reconciliation.py` | Its manifest is sound. Which docs are owed a re-read is **reported, not gated** |

The frontend steps need `frontend/node_modules` (`npm ci` at the repository root). In a git
worktree, the script falls back to the main checkout's backend and package virtualenvs. It
does not fall back for `node_modules`, so the frontend steps are SKIPPED there unless you install
them.

---

## 3. What the tests are

### 3.1 Backend (`backend/tests`)

| Directory | What it holds |
|---|---|
| `unit/` | Skills, agents, the registry, the DB factory, connectors (HTTP mocked), MCP tool wrappers, provenance and contract models, planning, evaluation |
| `integration/` | FastAPI routes through `httpx`: governance approval flow, rejection verdicts, recorded reasons, intervention roles, session API and store, the decision ledger and follow-ups, and the MCP server over stdio |
| `guardian/` | Guardian bypass prevention, verdict binding (signed, subject-bound, single-use), drafts that cannot approve themselves, the `audit_write` skill, and `audit_log` refusing UPDATE and DELETE |

The backend suite runs against the **SQLite** schema (`SQLITE_DDL` in
`backend/src/db/factory.py`). No test applies the PostgreSQL migrations (register
`postgres-schema-missing-session-incidents`). Neo4j code paths are tested only with mocks
(register `neo4j-graph-store`). No backend test asserts a causal estimate against ground truth:
the backend delegates estimation to `packages/causal-core`, and that is where estimates are
gated (§3.2).

### 3.2 Causal correctness: the pre-registered benchmarks

Each benchmark has a pre-registration committed *before* the run it governs: the question, the
data, the metric, the pass threshold and the seeded defects the suite must catch.

| Suite | Pre-registration | System under test | Runs |
|---|---|---|---|
| T0: analytic SCMs with closed-form truth | [`benchmarks/t0/PRE-REGISTRATION.md`](../benchmarks/t0/PRE-REGISTRATION.md) | `causal_core.analyze` | Every PR (`pytest -m t0` in `packages/causal-core`, inside its package step) |
| T2: IHDP, semi-synthetic | [`benchmarks/t2-ihdp/PRE-REGISTRATION.md`](../benchmarks/t2-ihdp/PRE-REGISTRATION.md) | `causal_core.analyze` | When the data is cached or `CAUSAL_FETCH=1` (CI sets it); also on demand via `python -m causal_core.bench.ihdp` |
| T3: LaLonde, real data with an experimental benchmark | [`benchmarks/t3-lalonde/PRE-REGISTRATION.md`](../benchmarks/t3-lalonde/PRE-REGISTRATION.md) | `causal_core.analyze` | As T2; `python -m causal_core.bench.lalonde` |
| T4: effects within declared segments | [`benchmarks/t4-segments/PRE-REGISTRATION.md`](../benchmarks/t4-segments/PRE-REGISTRATION.md) | `causal_core.segments` | Every PR (`pytest -m t4`) |
| D0: the decision procedure | [`benchmarks/d0-decision-policy/PRE-REGISTRATION.md`](../benchmarks/d0-decision-policy/PRE-REGISTRATION.md) | `causal_decision.decide` | Every PR (`pytest -m d0` in `packages/causal-decision`) |

The seeded-defect tests (`tests/t0/test_seeded_defects.py`, `tests/t4/…`, `tests/d0/…`) break
the system under test on purpose and assert that the gate fails. A gate that cannot see a
known defect does not count as a gate. The benchmark results, and the limits they found, are on
the register (for example `kernel-triangulation-moderate-n`).

Counterfactuals over an SCM, causal discovery and Rosenbaum bounds have no correctness tests
because they are not implemented (registers `scm-counterfactuals`, `causal-discovery`,
`sensitivity-analysis`).

### 3.3 Frontend (`frontend/`)

- **Vitest + React Testing Library** (`src/**/__tests__/*.test.tsx`). Covers the
  `ThinkingIndicator` latency contract, `ApprovalPanel`, `InterventionCard`, and the governance
  and incidents pages. The page tests pin the fail-closed contract. The governance page shows
  only backend-confirmed decisions and audit rows, and the incidents page leaves a decision
  unchanged and shows the error when the API call fails.
  There is no coverage threshold.
- **Playwright** (`frontend/e2e/platform.spec.ts`, config `frontend/playwright.config.ts`). It
  runs against a dev server on `localhost:3000` and is **not part of the gate or of CI**. Run it
  by hand with `npx playwright test` in `frontend/`.

---

## 4. The ratchets

The gate compares against committed numbers instead of absolute targets such as "zero lint
errors". Each number only moves in the safe direction:

| File | Holds | Rule |
|---|---|---|
| `scripts/coverage-floor.json` | Backend line-coverage floor (full suite) | Fails below the floor. Lowering it needs a `Ratchet-Exempt: <why>` commit trailer |
| `scripts/lint-baseline.json` | Maximum ruff and mypy findings, with the exact args | Fails above the maximum. Raising it needs `Ratchet-Exempt`. When a run finds fewer, lower the number in the same change |
| `scripts/gate-scope-floors.json` | Minimum counts per step, and the named known gaps of `gate-scope` | A new gap fails, and so does a listed gap that has been closed but not struck off |
| `scripts/contract-parity.json` | Known enumeration divergences | The list only shrinks |

Each file's `_comment` records when, and at which commit, its numbers were measured.

---

## 5. CI and release

### 5.1 CI (`.github/workflows/ci.yml`)

One job, on every push and on pull requests to `main`:

1. Check out with full history. The register cites some claims at the revision where they were
   made (`file@rev:line`), and a shallow clone cannot verify those.
2. Set up Python 3.12 and Node 20; cache the benchmark data (keyed on the pre-registrations).
3. Build `backend/.venv` (backend plus `causal-core` and `causal-decision`) and
   `packages/causal-core/.venv` (the three packages with their test extras); `npm ci`.
4. **Licence gate**: `check_licenses.py` for `causal-core`, `causal-mcp` and `causal-decision`
   (runtime dependencies must be permissive).
5. Run `.gnomon/ci.sh`, teeing its log.
6. **No unexpected skips**: fail if the summary shows any SKIPPED step other than
   `frontend-eslint`. In CI a skip would mean the environment is broken.
7. Upload the gate log as an artifact.

### 5.2 Release (`.github/workflows/release.yml`)

On a `v*` tag: re-run the three packages' suites, including the T0 and D0 gates and the licence
check, then build sdists and wheels, then publish `causal-core`, `causal-decision` and
`causal-mcp` to PyPI with Trusted Publishing (OIDC, no stored token). The backend and frontend
are not published or deployed. The repository has no deployment pipeline and no staging
environment.

### 5.3 Not in CI

Security scanning (Bandit, pip-audit, container scans: register `security-scanning-not-in-ci`),
TLC model checking of `specs/guardian_policy.tla` (register `tla-spec-not-checked`), Playwright,
eslint, and any performance or load test.

---

## 6. Performance

There is no performance suite. The backend declares a `performance` pytest marker that no test
uses. The latency targets that DATA_LAYER.md used to list were never measured, and are in
[`history/DATA_LAYER-targets-2026-09-25.md`](history/DATA_LAYER-targets-2026-09-25.md).

---

## 7. Code quality configuration

- **Python:** ruff and mypy (`--strict`) are configured in `backend/pyproject.toml` and run as
  ratchets (§4). pytest runs with `asyncio_mode = "auto"`. There is no `--cov-fail-under`: it
  applied to every invocation, so partial runs always failed, and the coverage floor replaced
  it.
- **TypeScript:** `tsc --noEmit` is the frontend's type gate. ESLint is not configured, so it
  does not run.
- **Rust:** none. The repository contains no Rust code.

---

## 8. Formal methods

`specs/guardian_policy.tla` (with `guardian_policy.cfg`) is a TLA+ model of the Guardian's
safety properties: verdict immutability, no write without an APPROVED verdict, an append-only
audit log, and the circuit breaker. **It is not model-checked.** TLC does not run in CI, and the
spec is not runnable as written (register `tla-spec-not-checked`). No Z3 or other solver is
used anywhere.

---

## 9. Observability

The backend logs structured events with `structlog`: `auth.*`, `governance.*`, `registry.*`,
`plan_ahead.*`, `drift_sensor.*`, and so on. Every Guardian verdict is a row in `audit_log`.
There is no metrics pipeline, dashboard or alerting on these. The verdict signing key's
absence is logged at CRITICAL (`guardian.signing_key.ephemeral`).

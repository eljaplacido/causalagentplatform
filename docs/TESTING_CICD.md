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
`scripts/verify.sh` only execs it, and `.septacore.json` declares that file as the repository's
one blocking step, so `septacore check` runs the same gate. Every step runs even after an
earlier one fails. A step whose toolchain is absent is **SKIPPED** by name with the reason, and
so is a dependency audit whose remote advisory database cannot be reached. A
tool that is present and crashes is a **FAIL**. A step that inspected something and found drift
it does not block on is **ADVISORY** (today only `doc-reconciliation`).

| Step | What it runs | Passes when |
|---|---|---|
| `backend-pytest` | The full backend suite (`backend/tests`: unit, integration, guardian) once, with coverage | Exit 0, a non-zero pass count above its floor (`scripts/gate-scope-floors.json`), and every `backend/tests/**/test_*.py` reported a test in the JUnit report |
| `backend-coverage` | `scripts/coverage_gate.py` on that run's coverage | Line coverage ≥ the floor in `scripts/coverage-floor.json` |
| `backend-ruff`, `scripts-ruff` | `scripts/lint_ratchet.py` (ruff over `backend/`, and over `scripts/`) | Findings ≤ `max_findings` in `scripts/lint-baseline.json` (`scripts/` is held at 0) |
| `backend-mypy` | `mypy src/ --strict` via the ratchet | Findings ≤ its `max_findings` |
| `semantic-types-build` | Builds `packages/semantic-types/dist` from source | `dist/index.d.ts` exists (the frontend typecheck reads it) |
| `frontend-tsc` | `tsc --noEmit` in `frontend/` | Exit 0 over at least its floor of project files |
| `frontend-vitest` | `vitest run` in `frontend/` | Exit 0, pass count above its floor, every test file reported |
| `frontend-eslint` | The eslint ratchet over `frontend/src/` with `frontend/eslint.config.mjs` (Next's core-web-vitals and TypeScript rule sets) | Findings ≤ its `eslint` entry in `scripts/lint-baseline.json`, which is 0. Without a config file the step is SKIPPED |
| one step per `packages/*/pyproject.toml` | That package's own `pytest`, in its own environment | Exit 0, at least one test passed, and **no test skipped**: every skip these suites can produce is a registered gate that did not run, so a skip fails the step. `causal-core` is always named in the summary, RAN or NOT RUN |
| `<package>-mypy`, one per package | mypy with the package's own strict config, via the ratchet | Findings ≤ its `mypy-<package>` entry in `scripts/lint-baseline.json`, which is 0 for all three; a package with no entry fails |
| `bandit-backend`, `bandit-packages` | bandit via the ratchet: `backend/src` and `backend/mcp_server` with `backend/pyproject.toml`'s `[tool.bandit]` (which skips B101 and B608), and every `packages/*/src` with bandit's defaults | MEDIUM and HIGH findings ≤ the `bandit-*` entries in `scripts/lint-baseline.json`, which are 0. LOW is reported, not counted |
| `secret-scan` | `scripts/secret_scan.py` over every file git would commit from the working tree (tracked, plus untracked and not ignored; never the history) | No private key, provider token or high-entropy credential assignment outside `scripts/secret-scan-allowlist.json`, no stale allowlist entry, and at least its floor of files scanned. Findings print a fingerprint, never the value |
| `pip-audit-backend`, `pip-audit-packages` | `scripts/dep_audit.py pip`: pip-audit over the site-packages of the environments this run tests (the backend toolchain, and each distinct package environment) | No advisory outside `scripts/audit-allowlist.json`, whose entries carry a reason and a `review_by` date; an expired or stale entry fails. SKIPPED when PyPI's advisory service cannot be reached |
| `npm-audit` | `scripts/dep_audit.py npm`: `npm audit` over `package-lock.json` (every workspace) | As above, for npm. SKIPPED when the registry cannot be reached |
| `gate-scope` | `scripts/gate_scope.py identity-all` | Every skill module is imported by a test, every tool of both MCP servers (the legacy one live, causal-mcp's from its `TOOLS` table) and every state-changing route is referenced by one, and every route is mounted, apart from the known gaps named in `scripts/gate-scope-floors.json` |
| `status-claims` | `scripts/status_claims.py` | `docs/CURRENT_STAGE.md` and `conformance/declared_not_implemented.json` agree with each other and with the tree; every citation quotes its line verbatim, and every `fixed_in` commit is in HEAD's history (a squash or rebase that orphaned it fails) |
| `contract-parity` | `scripts/contract_parity.py` | The shared enumerations agree across `models.py`, `packages/semantic-types` and `frontend/src/lib/types.ts`, apart from the divergences named in `scripts/contract-parity.json` |
| `doc-figures` | `scripts/doc_figures.py` | Every benchmark figure the docs quote, as bound in `scripts/doc-figures.json`, matches the value in that benchmark's standing run |
| `doc-reconciliation` | `scripts/doc_reconciliation.py` | Its manifest is sound (FAIL otherwise). When a tracked doc is owed a re-read, because the code it describes moved or it was never read, the step is **ADVISORY** rather than PASS |

The frontend steps need the frontend's toolchain: `typescript`, `vitest`, `eslint` and `next`
must resolve from `frontend/` to a copy inside the repository (`npm ci` at the repository root).
The script does not look for a `frontend/node_modules` directory: npm workspaces hoist what they
can to the root `node_modules`, so after some installs that directory is never created, and a
Vite cache can create it with no tools in it. In a git worktree, the script falls back to the
main checkout's backend and package virtualenvs. It does not fall back for `node_modules`, so the
frontend steps are SKIPPED there unless you install them.

The exit code sums the table:

| Exit | Meaning |
|---|---|
| 0 | Every step ran and passed |
| 1 | At least one step FAILED |
| 2 | No step failed, but at least one was SKIPPED: it was not measured, which is not a pass |
| 3 | Every step ran and none failed, but an ADVISORY step reported drift: docs are owed a re-read |

Only 0 is done. `.septacore.json` maps the same codes to `passed`, `failed`,
`could-not-measure` and `regressed`.

---

## 3. What the tests are

### 3.1 Backend (`backend/tests`)

| Directory | What it holds |
|---|---|
| `unit/` | Skills, agents, the registry, the DB factory, connectors (HTTP mocked), MCP tool wrappers, provenance and contract models, planning, evaluation |
| `integration/` | FastAPI routes through `httpx`: governance approval flow, rejection verdicts, recorded reasons, intervention roles, the role matrix and domain isolation (`test_rbac_matrix.py`: every state-changing route has a row, and is refused without a granted role), session API and store, the decision ledger (checkpoints and anchored verification included) and follow-ups, the assumption registry (`/api/v1/models`), and the MCP server over stdio |
| `guardian/` | Guardian bypass prevention, verdict binding (signed, subject-bound, single-use), drafts that cannot approve themselves, a risk score and cycle count the caller cannot supply, the `audit_write` skill, and `audit_log` refusing UPDATE and DELETE |

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
| T5: staggered difference-in-differences | [`benchmarks/t5-did/PRE-REGISTRATION.md`](../benchmarks/t5-did/PRE-REGISTRATION.md) | `causal_core.panel` | Every PR (`pytest -m t5`) |
| T6: anytime-valid monitoring of watched rollouts | [`benchmarks/t6-sequential/PRE-REGISTRATION.md`](../benchmarks/t6-sequential/PRE-REGISTRATION.md) | `causal_core.sequential` | Every PR (`pytest -m t6`) |
| T7: cluster-randomized designs | [`benchmarks/t7-cluster/PRE-REGISTRATION.md`](../benchmarks/t7-cluster/PRE-REGISTRATION.md) | `causal_core.cluster` | Every PR (`pytest -m t7`) |
| T8: switchback designs | [`benchmarks/t8-switchback/PRE-REGISTRATION.md`](../benchmarks/t8-switchback/PRE-REGISTRATION.md) | `causal_core.cluster`, through `causal_core.analyze` | Every PR (`pytest -m t8`) |
| D0: the decision procedure | [`benchmarks/d0-decision-policy/PRE-REGISTRATION.md`](../benchmarks/d0-decision-policy/PRE-REGISTRATION.md) | `causal_decision.decide` | Every PR (`pytest -m d0` in `packages/causal-decision`) |
| A0: whether a graph revision invalidates an estimate | [`benchmarks/a0-assumption-impact/PRE-REGISTRATION.md`](../benchmarks/a0-assumption-impact/PRE-REGISTRATION.md) | `causal_decision.assumptions.impact` | Every PR (`pytest -m a0` in `packages/causal-decision`) |

`packages/causal-core/tests/test_benchmark_results.py` holds the registrations to their runs:
for every `benchmarks/*/PRE-REGISTRATION.md`, a committed result must record a passing run
against that file's exact bytes (`prereg_sha256`), so a bar cannot move after the last run
without a new one. Failing runs stay on file beside it.

The seeded-defect tests (`tests/t0/test_seeded_defects.py`, `tests/t4/…`, `tests/t5/…`, `tests/t6/…`, `tests/t7/…`, `tests/t8/…`,
`tests/d0/…`, `tests/a0/…`) break the system under test on purpose and assert that the gate fails. A gate that cannot see a
known defect does not count as a gate. The benchmark results, and the limits they found, are on
the register (for example `kernel-triangulation-moderate-n`).

Counterfactuals over an SCM, causal discovery and Rosenbaum bounds have no correctness tests
because they are not implemented (registers `scm-counterfactuals`, `causal-discovery`,
`sensitivity-analysis`).

### 3.3 Frontend (`frontend/`)

- **Vitest + React Testing Library** (`src/**/__tests__/*.test.tsx`). Covers the
  `ThinkingIndicator` latency contract, `ApprovalPanel`, `InterventionCard`, and the governance
  and incidents pages. The page tests pin the fail-closed contract. They mock the API, so the governance page's
  request bodies are also pinned against the real endpoints by a backend integration test
  (`test_the_governance_pages_own_requests_approve_an_object`). The governance page shows
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
| `scripts/lint-baseline.json` | Maximum ruff, mypy, eslint and bandit findings, with the exact args and tool version: backend ruff and mypy, `scripts/` ruff (0), mypy for each package (0), eslint over `frontend/src/` (0), and bandit MEDIUM+ over the backend and the packages (0 each) | Fails above the maximum. Raising it needs `Ratchet-Exempt`. When a run finds fewer, lower the number in the same change |
| `scripts/gate-scope-floors.json` | Minimum counts per step, and the named known gaps of `gate-scope` | A new gap fails, and so does a listed gap that has been closed but not struck off |
| `scripts/contract-parity.json` | Known enumeration divergences | The list only shrinks |

Each file's `_comment` records when, and at which commit, its numbers were measured.

---

## 5. CI and release

### 5.1 CI (`.github/workflows/ci.yml`)

One job, on every push, on pull requests to `main`, and weekly on a schedule (so an advisory
published since the last push fails the dependency audits without waiting for one):

1. Check out with full history. The register cites some claims at the revision where they were
   made (`file@rev:line`), and a shallow clone cannot verify those.
2. Set up Python 3.12 and Node 20; cache the benchmark data (keyed on the pre-registrations).
3. Build `backend/.venv` (backend plus `causal-core` and `causal-decision`) and
   `packages/causal-core/.venv` (the three packages with their test extras), each after
   upgrading pip, which the gate's pip-audit audits with the rest; `npm ci`.
4. **Licence gate**: `check_licenses.py` for `causal-core`, `causal-mcp` and `causal-decision`
   (runtime dependencies must be permissive).
5. Run `.gnomon/ci.sh`, teeing its log, and act on its exit code (§2): 1 fails the job, and so
   does 2, because in CI a step that could not be measured means the environment above is
   broken, or an advisory database was unreachable and the dependency audit did not happen. 3 (docs owed a re-read) is annotated as a warning and does not block a merge.
6. Upload the gate log as an artifact.

The job has a 75-minute timeout.

### 5.2 Release (`.github/workflows/release.yml`)

On a `v*` tag: re-run the three packages' full suites (with `CAUSAL_FETCH=1`, so T2 and T3 run;
T0 and D0 as their own steps) and the licence check, then build sdists and wheels, then publish `causal-core`, `causal-decision` and
`causal-mcp` to PyPI with Trusted Publishing (OIDC, no stored token). The backend and frontend
are not published or deployed. The repository has no deployment pipeline and no staging
environment.

### 5.3 Not in CI

Container image scans, a release SBOM, DAST and hash-pinned Python installs (register
`supply-chain-scanning-not-in-ci`; bandit, the secret scan and the dependency audits are in the
gate, §2), TLC model checking of `specs/guardian_policy.tla` (register `tla-spec-not-checked`), Playwright,
and any performance or load test.

---

## 6. Performance

There is no performance suite. The backend declares a `performance` pytest marker that no test
uses. The latency targets that DATA_LAYER.md used to list were never measured, and are in
[`history/DATA_LAYER-targets-2026-09-25.md`](history/DATA_LAYER-targets-2026-09-25.md).

---

## 7. Code quality configuration

- **Python:** ruff and mypy (`--strict`) are configured in `backend/pyproject.toml` and in each
  package's own `pyproject.toml`, pinned to one minor version each (a ratchet's count is only
  comparable within a version), and run as ratchets (§4). pytest runs with `asyncio_mode = "auto"`. There is no `--cov-fail-under`: it
  applied to every invocation, so partial runs always failed, and the coverage floor replaced
  it.
- **TypeScript:** `tsc --noEmit` is the frontend's type gate. ESLint is configured in
  `frontend/eslint.config.mjs` (Next's core-web-vitals and TypeScript rule sets) and runs as a
  ratchet held at zero (§4).
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

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
| `import-origin` | `scripts/import_origin.py`, once per interpreter the later steps use (the backend toolchain, each package environment), under the `PYTHONPATH` that step gets. The script exports every `packages/*/src` first on `PYTHONPATH` and `MYPYPATH` for every step | Every package under `packages/*/src` resolves inside this tree. In a worktree the borrowed venvs' editable installs point at the main checkout; without this, the backend and package suites tested that checkout's kernel |
| `backend-pytest` | The full backend suite (`backend/tests`: unit, integration, guardian) once, on SQLite (`TEST_DATABASE_URL` is unset for it), with coverage | Exit 0, a non-zero pass count above its floor (`scripts/gate-scope-floors.json`), and every `backend/tests/**/test_*.py` reported a test in the JUnit report |
| `backend-pytest-postgres` | The same full suite with `TEST_DATABASE_URL` set: the test database is built on that PostgreSQL server from `backend/src/db/migrations/*.sql` (§3.1). No coverage (judged once, above) | As `backend-pytest`, and **no test skipped**: in this run a skip is a PostgreSQL test that did not run. SKIPPED with "no TEST_DATABASE_URL" when the variable is unset, so a local run without a database exits 2, not 0 |
| `backend-coverage` | `scripts/coverage_gate.py` on that run's coverage | Line coverage ≥ the floor in `scripts/coverage-floor.json` |
| `backend-ruff`, `scripts-ruff` | `scripts/lint_ratchet.py` (ruff over `backend/`, and over `scripts/`) | Findings ≤ `max_findings` in `scripts/lint-baseline.json` (`scripts/` is held at 0) |
| `backend-mypy` | `mypy src/ --strict` via the ratchet | Findings ≤ its `max_findings` |
| `semantic-types-build` | Builds `packages/semantic-types/dist` from source | `dist/index.d.ts` exists (the frontend typecheck reads it) |
| `frontend-tsc` | `tsc --noEmit` in `frontend/` | Exit 0 over at least its floor of project files |
| `frontend-vitest` | `vitest run` in `frontend/` | Exit 0, pass count above its floor, every test file reported |
| `frontend-eslint` | The eslint ratchet over `frontend/src/` with `frontend/eslint.config.mjs` (Next's core-web-vitals and TypeScript rule sets) | Findings ≤ its `eslint` entry in `scripts/lint-baseline.json`, which is 0. Without a config file the step is SKIPPED |
| one step per `packages/*/pyproject.toml` | That package's own `pytest`, in its own environment, with the `junit_markers` plugin (`scripts/ci_pytest_plugins/`) writing each test's markers into the JUnit report | Exit 0, at least one test passed, and **no test skipped**: every skip these suites can produce is a registered gate that did not run, so a skip fails the step. And two identity checks on the report, run even when pytest failed: `gate_scope.py package-files` (every `packages/<name>/tests/**/test_*.py` reported a test) and `gate_scope.py gate-markers` (every pre-registered gate marker of the package, from the gate table in `.gnomon/skills/verifying-changes.md` and its `pyproject.toml`, has a passed test; `real_data` only when the T2/T3 data is cached). `causal-core` is always named in the summary, RAN or NOT RUN |
| `<package>-mypy`, one per package | mypy with the package's own strict config, via the ratchet | Findings ≤ its `mypy-<package>` entry in `scripts/lint-baseline.json`, which is 0 for all three; a package with no entry fails |
| `python-lock` | `scripts/py_lock.py check` over the hash-pinned locks `requirements/{backend,kernel,image,release}.txt` (§5.4), with the environments this run tests | Every lock pins each distribution `==` with its sha256 hashes; every lock is current (the fingerprint of its inputs, the `.in` file and the dependency tables of each `pyproject.toml` it names, equals the one `relock` recorded in `requirements/lock-inputs.json`, so a dependency edit without a re-lock fails); every lock satisfies its local packages' requirements; and the backend toolchain and each package environment **equal** their lock (`backend.txt`, `kernel.txt`: the same distributions at the same versions, nothing missing or extra) with `pip check` clean. SKIPPED without the backend toolchain or a package environment |
| `bandit-backend`, `bandit-packages` | bandit via the ratchet: `backend/src` with `backend/pyproject.toml`'s `[tool.bandit]` (which skips no rule; B608, SQL built from strings, is enforced), and every `packages/*/src` with bandit's defaults | MEDIUM and HIGH findings ≤ the `bandit-*` entries in `scripts/lint-baseline.json`, which are 0. LOW is reported, not counted |
| `secret-scan` | `scripts/secret_scan.py` over every file git would commit from the working tree (tracked, plus untracked and not ignored; never the history) | No private key, provider token or high-entropy credential assignment outside `scripts/secret-scan-allowlist.json`, no stale allowlist entry, and at least its floor of files scanned. Findings print a fingerprint, never the value |
| `pip-audit-backend`, `pip-audit-packages`, `pip-audit-image` | `scripts/dep_audit.py pip --requirement`: pip-audit over the locks `requirements/backend.txt`, `kernel.txt` and `image.txt` as written (`--require-hashes --disable-pip`: nothing resolved), the pins whose markers hold here. `python-lock` holds the tested environments equal to the first two, so this audits what the tests ran on; the third is what `backend/Dockerfile` installs | No advisory outside `scripts/audit-allowlist.json`, whose entries carry a reason and a `review_by` date; an expired or stale entry fails. SKIPPED when PyPI's advisory service cannot be reached |
| `npm-audit` | `scripts/dep_audit.py npm`: `npm audit` over `package-lock.json` (every workspace) | As above, for npm. SKIPPED when the registry cannot be reached |
| `gate-scope` | `scripts/gate_scope.py identity-all` | Every skill module is imported by a test, every tool of the MCP server (`packages/causal-mcp`, read from its `TOOLS` table) and every state-changing route is referenced by one, and every route is mounted, apart from the known gaps named in `scripts/gate-scope-floors.json` |
| `status-claims` | `scripts/status_claims.py` | `docs/CURRENT_STAGE.md` and `conformance/declared_not_implemented.json` agree with each other and with the tree; every citation quotes its line verbatim, and every `fixed_in` commit is in HEAD's history (a squash or rebase that orphaned it fails) |
| `contract-parity` | `scripts/contract_parity.py` | The shared enumerations agree across `models.py`, `packages/semantic-types` and `frontend/src/lib/types.ts`, apart from the divergences named in `scripts/contract-parity.json` |
| `doc-figures` | `scripts/doc_figures.py` | Every benchmark figure the docs quote, as bound in `scripts/doc-figures.json`, matches the value in that benchmark's standing run: the latest passing run against the current pre-registration whose `git` is a 40-hex commit, not `-dirty`, that is an ancestor of HEAD |
| `skill-refs` | `scripts/skill_refs.py` over the agent-facing instructions: `.gnomon/skills/*.md`, `AGENTS.md`, `CONTRIBUTING.md`, and the prose in `.gnomon/roles.toml`, `tools.toml` and `policy.toml` | Every repository path they name resolves (in code spans, code blocks, links, and anchored paths in bare prose; globs must match something); every command they tell an agent to run resolves (the script exists, a `septacore` subcommand is in the pinned list in `scripts/skill-refs.json`, a `.venv/bin/<tool>` is installed by that directory's `pyproject.toml`, a `pytest -m` marker is declared in the package it runs in); `verifying-changes.md`'s gate table and `benchmarks/*/PRE-REGISTRATION.md` match both ways; that skill names every step `.gnomon/ci.sh` records; a list of three or more gates names all of them; every skill is listed in `AGENTS.md`; and each skill's front matter parses with a `name`, `description` and compiling `match`. Deliberate non-paths are listed in `scripts/skill-refs.json` with a reason (a stale entry fails), and zero references checked is a FAIL |
| `doc-reconciliation` | `scripts/doc_reconciliation.py` | Its manifest is sound (FAIL otherwise). When a tracked doc is owed a re-read, because the code it describes moved or it was never read, the step is **ADVISORY** rather than PASS |

The frontend steps need the frontend's toolchain: `typescript`, `vitest`, `eslint` and `next`
must resolve from `frontend/` to a copy inside the repository (`npm ci` at the repository root).
The script does not look for a `frontend/node_modules` directory: npm workspaces hoist what they
can to the root `node_modules`, so after some installs that directory is never created, and a
Vite cache can create it with no tools in it. In a git worktree, the script falls back to the
main checkout's backend and package virtualenvs; `PYTHONPATH` puts this tree's `packages/*/src`
ahead of their editable installs, and `import-origin` fails if it did not. It does not fall back for `node_modules`, so the
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
| `unit/` | Skills, agents, the registry, the DB factory, connectors (HTTP mocked), provenance and contract models, planning, evaluation, the incidents API's decision merge (a human decision beside the Guardian verdict, `test_incident_decision_merge.py`), and negative controls for the gate's own scripts (`test_contract_parity.py`, `test_secret_scan.py`, `test_dep_audit.py`, `test_skill_refs.py`, `test_doc_figures.py`, `test_import_origin.py`, `test_gate_scope_package_rows.py`), and for the supply-chain checks outside it (`test_py_lock.py`, `test_image_scan.py`, `test_sbom_check.py`) |
| `integration/` | FastAPI routes through `httpx`: draft submission end to end (`test_draft_submission_api.py`: a declared or session domain is judged, nothing declared is PR-006, CHAOTIC is PR-003), governance approval flow, the reject authority (an APPROVED verdict for `GOVERNANCE_REJECT`, and the governance page's own reject bodies end to end, `test_governance_reject_verdict.py`), recorded reasons, the review queue's risk tiers and submitters (`test_review_queue_risk.py`), intervention roles, the role matrix and domain isolation (`test_rbac_matrix.py`: every route but the liveness probe has a row, reads included, and is refused without a granted role), session ownership (`test_session_ownership.py`), incident overwrites (`test_incident_overwrite.py`), intervention decisions by exact id (`test_intervention_decision_identity.py`), the database's own domain isolation on PostgreSQL (`test_rls_domain_isolation.py`), session API and store (a caller-declared Cynefin domain refused: `test_session_domain_not_declared.py`), the analysis endpoints (`test_analysis_api.py`: each example's stated true effect inside the returned interval, the hidden-confounder example refused), the decision ledger (checkpoints and anchored verification included) and follow-ups, the assumption registry (`/api/v1/models`), provenance written by the running flows and kept append-only with erasure as a tombstone (`test_provenance_flow.py`, `test_provenance_append_only.py`), and the MCP server over stdio |
| `guardian/` | Guardian bypass prevention, verdict binding (signed, subject-bound, single-use, and for approval bound to `GOVERNANCE_APPROVE`), drafts that cannot approve themselves, PR-007 (no edge approved without a sensitivity result the platform recorded under a `RECORD_SENSITIVITY` verdict; a drafter's own result, or a forged verdict reference, does not count) and the kernel's sensitivity analysis recorded where it reads it, PR-010 (no approval of a plan with no safe action), a risk score and cycle count the caller cannot supply, the `audit_write` skill, and `audit_log` refusing UPDATE and DELETE |

The backend suite runs against one of two schemas, chosen by `TEST_DATABASE_URL`
(`backend/tests/conftest.py`, fixture `db_engine`):

- **Unset (the default): SQLite.** A fresh in-memory database per test, built from `SQLITE_DDL`
  in `backend/src/db/factory.py`.
- **Set to a `postgresql+asyncpg://` URL: PostgreSQL.** Once per run the fixture creates a
  database named `causal_test_<random>` on that server (it connects to the URL's database only
  to do so), applies `backend/src/db/migrations/*.sql` in order, and drops it at the end. Before
  each test it TRUNCATEs every table and re-enables any trigger a tamper test disabled. The
  URL's role needs to create databases, roles and extensions (001 creates both), so use a
  superuser on a disposable server.
  That role owns the tables and seeds them (`db_session`); the app's own queries (`db_engine`,
  and everything through `CloudSqlFactory`) connect as `causal_app`, as the deployed backend
  does, so row-level security binds the whole suite. The login is a per-run role,
  `causal_test_login_<random>`, a member of `causal_app` whose sessions run as it, with a random
  password generated by the fixture; it is dropped with the database, and `causal_app` itself is
  never altered, so two runs on one server do not collide. A test that calls the registry or a
  store directly has no request and no user; `db_engine` declares a service scope for it
  (every domain), and each request a test makes runs under its caller's own scope.

The gate runs both (`backend-pytest` and `backend-pytest-postgres`). SQLite's loose typing
accepts what PostgreSQL refuses, so code that binds values must use the bind types in
`backend/src/db/types.py` (`TIMESTAMP`, `TEXT_ARRAY`, `UUID_ARRAY`, `BOOLEAN`) and read back
through `timestamp_text` and `json_value`; until both ran, the Guardian could not write
`audit_log` on PostgreSQL and no test said so. `tests/integration/test_postgres_schema_parity.py`
holds the two schemas to the same tables and columns, and
`tests/integration/test_rls_domain_isolation.py` holds the database's domain isolation (PostgreSQL
only, so skipped on SQLite: with negative controls that drop a policy, disable RLS, or connect as
the owner). `tests/unit/test_registry_versioning.py` holds semantic-object versioning on both
engines: each engine's triggers (014 and their SQLite rendering in `SQLITE_DDL`) must refuse an
update of a version row and a head update that skips the revision rule, which is why a test that
seeds a semantic row directly and then changes it must go through the registry, or, to play a
writer with database access, through the revision rule itself (`_write_directly` in
`tests/guardian/test_pr007_sensitivity_gate.py`). Recording an edge's sensitivity result is a
revision too (`test_recording_a_sensitivity_result_writes_the_edges_next_revision`). Its PostgreSQL-only
cases check the commit-time trigger, `causal_app`'s grants on the history, and that 014's backfill
is idempotent. `tests/integration/test_lineage_chain.py` drives draft, check and approval through
the API (for an edge, with the recorded sensitivity analysis PR-007 needs in between) and reads the
lineage chain back. To run the PostgreSQL half locally, with a
throwaway server bound to localhost:

```bash
docker run -d --name causal-pg-test -p 127.0.0.1:5433:5432 \
  -e POSTGRES_PASSWORD=<a throwaway password> postgres:16-alpine
export TEST_DATABASE_URL='postgresql+asyncpg://postgres:<that password>@127.0.0.1:5433/postgres'
cd backend && .venv/bin/pytest -q --no-cov      # the whole suite, on PostgreSQL
bash .gnomon/ci.sh                               # or the gate, with both backend steps
docker rm -f causal-pg-test
```

The optional Neo4j code paths (experience buffer, drift-alert storage) are tested only with a
mocked driver; Neo4j is not the causal graph store (`neo4j-graph-store` was retracted). No backend test asserts a causal estimate against ground truth:
the backend delegates estimation to `packages/causal-core`, and that is where estimates are
gated (§3.2); the narration tests check the kernel's interval covers
the effect their simulated data were built with only as a premise, before testing what the LLM
layer did with the number.

**LLM calls.** No backend test needs a model or the network. `tests/conftest.py` clears the
provider variables (`OLLAMA_BASE_URL`, the cloud keys and their `_FILE` forms) for every test, so a
developer's own configuration cannot reach an agent test; tests that exercise the LLM layer
(`unit/test_llm_routing_used.py`, `unit/test_prompt_templates_loaded.py`,
`integration/test_decision_narrative.py`) configure Ollama and substitute LangChain's
deterministic fake chat model for the client. One test is marked `llm_live` and calls a real local
Ollama; it is **deselected** (not skipped, which `backend-pytest-postgres` would fail) unless
`LLM_LIVE=1`, and the gate never sets it. Run it with `LLM_LIVE=1 OLLAMA_BASE_URL=...
OLLAMA_CAUSAL_MODEL=... pytest -m llm_live -s --no-cov tests/unit/test_llm_routing_used.py`.

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
| T9: Rosenbaum sensitivity | [`benchmarks/t9-rosenbaum/PRE-REGISTRATION.md`](../benchmarks/t9-rosenbaum/PRE-REGISTRATION.md) | `causal_core.sensitivity`, directly and through `causal_core.analyze` | Every PR (`pytest -m t9`) |
| T10: positivity and overlap | [`benchmarks/t10-positivity/PRE-REGISTRATION.md`](../benchmarks/t10-positivity/PRE-REGISTRATION.md) | `causal_core.design` and `causal_core.overlap`, through `causal_core.analyze` | Every PR (`pytest -m t10`) |
| D0: the decision procedure | [`benchmarks/d0-decision-policy/PRE-REGISTRATION.md`](../benchmarks/d0-decision-policy/PRE-REGISTRATION.md) | `causal_decision.decide` | Every PR (`pytest -m d0` in `packages/causal-decision`) |
| A0: whether a graph revision invalidates an estimate | [`benchmarks/a0-assumption-impact/PRE-REGISTRATION.md`](../benchmarks/a0-assumption-impact/PRE-REGISTRATION.md) | `causal_decision.assumptions.impact` | Every PR (`pytest -m a0` in `packages/causal-decision`) |
| C0: whether the calibration report is honest about what follow-ups can say (simulated streams) | [`benchmarks/c0-calibration/PRE-REGISTRATION.md`](../benchmarks/c0-calibration/PRE-REGISTRATION.md) | `causal_decision.outcome.report` | Every PR (`pytest -m c0` in `packages/causal-decision`) |

`packages/causal-core/tests/test_benchmark_results.py` holds the registrations to their runs:
for every `benchmarks/*/PRE-REGISTRATION.md`, a committed result must record a passing run
against that file's exact bytes (`prereg_sha256`), so a bar cannot move after the last run
without a new one, recorded from a clean tree: its `git` a 40-hex commit, not `-dirty`, that is
an ancestor of HEAD (`unknown` once passed as clean; `t8-switchback` v2 run1 carries it and is
kept, but does not stand). A result's `passed` must agree with what it stores, though the
numeric bars are not re-judged: a pass with a failure on file, or without every seeded defect
the registration requires recorded as caught, fails the test. Failing runs stay on file beside it. The `git` a bench module writes is the
commit of the checkout its code was imported from, not the working directory's.

The seeded-defect tests (`tests/t0/test_seeded_defects.py`, `tests/t4/…`, `tests/t5/…`, `tests/t6/…`, `tests/t7/…`, `tests/t8/…`,
`tests/t9/…`, `tests/t10/…`, `tests/d0/…`, `tests/a0/…`, `tests/c0/…`) break the system under test on purpose and assert that the gate fails. A gate that cannot see a
known defect does not count as a gate. The benchmark results, and the limits they found, are on
the register (for example `kernel-overlap-detection-limits`), and leave it only when a gate closes them
(`kernel-triangulation-moderate-n`, closed by T0 v4).

Counterfactuals over an SCM and causal discovery have no correctness tests because they are not
implemented (registers `scm-counterfactuals`, `causal-discovery`). Rosenbaum bounds are gated by
T9 and pinned to a published example by `backend/tests/unit/test_rosenbaum_bounds.py`.

### 3.3 Frontend (`frontend/`)

- **Vitest + React Testing Library** (`src/**/__tests__/*.test.{ts,tsx}`). Covers the
  `ThinkingIndicator` latency contract, `CynefinBadge`, `ApprovalPanel`, `InterventionCard`, the
  API client's errors (`src/lib/__tests__/api.test.ts`: the backend's `error` code, 422 field
  errors, 401/403 as not authorised, a Guardian block's structured detail), `FragmentCard`'s badge,
  `SiteNav` and the navigating persona dropdown, the 404 page, the kernel number format
  (`src/lib/__tests__/utils.test.ts`, against `causal_core.claims.format_number`'s outputs), and
  the governance, incidents, analyst, workbench (fragment drafting), decision-ledger,
  assumption-registry, executive and engineer pages (the engineer test prerenders the page at one
  time and hydrates it at another, and fails on a hydration mismatch).
  The page tests pin the fail-closed contract. The ledger and registry tests use responses the
  kernel produced (`src/test-fixtures/kernel.ts`, from data simulated with a set effect) and check
  that the pages show those numbers: the registry verbatim, the ledger in the kernel's own
  4-significant-digit format with the exact value as the tooltip, an absent value as "—", and a
  calibration rate only where the report states one. They mock the API, so the governance page's
  request bodies are also pinned against the real endpoints by a backend integration test
  (`test_the_governance_pages_own_requests_approve_an_object`). The governance page shows
  only backend-confirmed decisions and audit rows, and the incidents page leaves a decision
  unchanged and shows the error when the API call fails. They also pin that each page renders
  data shaped as the backend sends it and invents none: registry rows with a business domain,
  stored incidents with P0–P3 or seeded SEV1–SEV4 severities and no effect estimate, and a
  session result with only a proposed-edge count.
  There is no coverage threshold.
- **Playwright** (`frontend/e2e/`, config `frontend/playwright.config.ts`). **Not part of the
  gate or of CI**; run by hand with `npx playwright test` in `frontend/`. `platform.spec.ts`
  expects a live backend. `workbench-authoring.spec.ts` drives the real frontend in Chromium with
  the backend stubbed at the network boundary (`page.route`), so it checks the drafting surface,
  not that the backend accepts a draft. `E2E_PORT` moves the server off 3000, and `E2E_PROD=1`
  serves a production build (`next build` first) instead of the dev server, which fails on a host
  at its file-watch limit.

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

Two jobs, on pushes to `main`, on pull requests to `main`, and weekly on a schedule (so an
advisory or a fix published since the last push surfaces without waiting for one). A branch push
does not run CI on its own; its pull request does.

**Where jobs run.** `runs-on` reads the repository variable `CI_RUNS_ON`, a JSON runner label or
list; unset, jobs run on GitHub's `ubuntu-latest`. While hosted minutes are unavailable it names
self-hosted runners (label `causalplatform-ci`), which run one job each, so several can share a
host: the database service takes a loopback port Docker chooses, and image tags carry the run
id. Deleting the variable returns every job to hosted runners. A job on a self-hosted runner runs
on that host as the runner's user, dependency updates included.

The gate job:

1. Check out with full history. The register cites some claims at the revision where they were
   made (`file@rev:line`), and a shallow clone cannot verify those.
2. Set up Python 3.12 and Node 24 (LTS; 20 reached end of life in April 2026); cache the benchmark data (keyed on the pre-registrations).
   A `postgres:16-alpine` service container (health-checked with `pg_isready`, a CI-only
   throwaway password, listening on loopback only) runs beside the job for
   `backend-pytest-postgres`.
3. Build `backend/.venv` (backend plus `causal-core` and `causal-decision`) and
   `packages/causal-core/.venv` (the three packages with their test extras) from their
   hash-pinned locks (§5.4): `pip install --require-hashes --no-deps -r requirements/<lock>.txt`,
   then this checkout's packages with `--no-deps --no-build-isolation -e ...`, then `pip check`;
   `npm ci`.
4. **Licence gate**: `check_licenses.py` for `causal-core`, `causal-mcp` and `causal-decision`
   (runtime dependencies must be permissive).
5. Run `.gnomon/ci.sh` with `TEST_DATABASE_URL` pointing at that service, teeing its log, and
   act on its exit code (§2): 1 fails the job, and so
   does 2, because in CI a step that could not be measured means the environment above is
   broken, or an advisory database was unreachable and the dependency audit did not happen. 3 (docs owed a re-read) is annotated as a warning and does not block a merge.
6. Upload the gate log as an artifact.

The job has a 75-minute timeout.

The **images** job (one run per image, `backend` and `frontend`, beside the gate job):

1. Build `backend/Dockerfile` and `frontend/Dockerfile`, both from the repository root. Both
   base images are pinned by digest. The backend image installs from `requirements/image.txt`
   with `--require-hashes` and keeps no pip at run time; the frontend image installs
   `package-lock.json` with `npm ci` and runs Next's standalone server without npm.
2. Scan each image with Trivy 0.74.0, run as the `aquasec/trivy` container pinned by digest,
   for HIGH and CRITICAL vulnerabilities, with every package listed.
3. `scripts/image_scan.py` judges the report. A HIGH or CRITICAL finding with a published fix
   fails the job unless `scripts/image-scan-allowlist.json` excuses it with a `reason` and a
   `review_by` date. An expired or stale entry fails, and so does a report that lists no
   package. A finding with **no fixed version published** is listed in the log and counted
   in the summary line, but does not fail the job: an upgrade cannot fix it (§5.3).
4. Upload the report and a CycloneDX SBOM of the image (Trivy's) as the `image-<name>` artifact.

The job was added on 2026-09-26 and its steps were run locally with the same images and scanner;
it had not yet run on GitHub.

To run it locally (with Docker; the scanner container reads the image through the Docker
socket, and publishes no port):

```bash
docker build -f backend/Dockerfile -t causal-platform-backend:ci .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/scan:/out" \
  aquasec/trivy:0.74.0 image --scanners vuln --severity HIGH,CRITICAL --list-all-pkgs \
  --format json --output /out/trivy-backend.json causal-platform-backend:ci
python3 scripts/image_scan.py scan/trivy-backend.json --image backend
```

### 5.2 Release (`.github/workflows/release.yml`)

On a `v*` tag: re-run the three packages' full suites (with `CAUSAL_FETCH=1`, so T2 and T3 run;
T0 and D0 as their own steps) in the environment of `requirements/kernel.txt`, installed as CI
installs it, and the licence check. Then build sdists and wheels (with `build`, `twine` and
`cyclonedx-bom` at pinned versions), install the wheels into a fresh environment that holds only
them and `requirements/release.txt` (their runtime dependencies, hash-pinned), and write a
**CycloneDX 1.6 SBOM** of that environment. `cyclonedx-py` validates it against the schema as it
writes, and `scripts/sbom_check.py` then checks it: it parses, and lists every locked pin at its
version, the three packages, and nothing else. Then publish `causal-core`, `causal-decision` and
`causal-mcp` to PyPI with Trusted Publishing (OIDC, no stored token), and attach the SBOM to the
tag's GitHub release (creating the release if the tag has none). A manual run does all of this
except publishing and attaching; the SBOM is kept as the `sbom` artifact either way. The backend and frontend
are not published or deployed. The repository has no deployment pipeline and no staging
environment.

### 5.3 Not in CI

- **DAST.** Nothing runs a dynamic scanner such as OWASP ZAP against a running API, and there is
  no staging environment to point one at (register `dast-not-built`).
- **Image findings with no fix published** are listed by the images job, not gated. On
  2026-09-26 the backend image had 44 HIGH ones, all in Debian 13 base packages, and the
  frontend image none.
- TLC model checking of `specs/guardian_policy.tla` (register `tla-spec-not-checked`),
  Playwright, and any performance or load test.

### 5.4 Python locks, and how to add a dependency

Every Python environment CI builds installs from a lock in `requirements/`: `backend.txt`
(`backend/.venv`), `kernel.txt` (`packages/causal-core/.venv`, and release.yml's verify job),
`image.txt` (`backend/Dockerfile`) and `release.txt` (the published wheels' runtime dependencies,
for the SBOM). Each is made from its `.in` file by `uv pip compile --universal --generate-hashes`
(one lock for every platform, markers where a dependency is platform-specific), and pins every
distribution `==` with the sha256 of each of its files. This repository's own packages are named
in the `.in` files and resolved from their `pyproject.toml`, but left out of the lock: they are
not on an index, so there is nothing to hash, and they install from the checkout in a second step:

```bash
python -m pip install --require-hashes --no-deps -r requirements/backend.txt
python -m pip install --no-deps --no-build-isolation \
    -e packages/causal-core -e packages/causal-decision -e "backend[dev]"
python -m pip check
```

The first step is the only one that fetches anything. `--no-deps` on the second keeps pip from
resolving anything the lock does not pin, and `--no-build-isolation` builds the local packages
with the `hatchling` (and `editables`) the lock installed, so no unhashed build backend is
fetched.

**To add or change a dependency:** edit the `pyproject.toml`, re-lock, and commit both:

```bash
python3 scripts/py_lock.py relock              # needs uv; keeps existing pins where they fit
python3 scripts/py_lock.py relock --upgrade    # moves every pin to the newest that fits
```

`relock` writes the four locks and `requirements/lock-inputs.json`, the fingerprint the gate's
`python-lock` step compares. It passes uv `--refresh-package` for each local package the `.in`
file names, so a version bump of one (causal-core 0.10 to 0.11) is resolved from the source, not
from uv's cached build of the old version; index packages keep their cache. A dependency edit without a re-lock fails that step, naming the
file and the command. The step also requires your local environments to equal their locks, so
after a re-lock (or a pull that brought one), bring them back in line:

```bash
python3 scripts/py_lock.py sync backend --python backend/.venv/bin/python
python3 scripts/py_lock.py sync kernel --python packages/causal-core/.venv/bin/python
```

`sync` installs the lock and the local packages as above, uninstalls whatever the lock does not
list, and re-runs the check.

#### Dependabot PRs

Dependabot (`.github/dependabot.yml`) opens weekly PRs for the locks (pins inside the ranges),
the `pyproject.toml` ranges (only when a release falls outside one), npm, the GitHub Actions and
the two base images. How they are handled:

- **The gate decides.** A bot PR runs the same CI as any other: `.gnomon/ci.sh` and the image
  job (build, Trivy). Green is necessary, not sufficient: read what moved. A PR that changes a
  range is red until someone re-locks on its branch, which is the review it needs. A red npm PR
  is often the bot's lock, not the update: re-apply the `package.json` change on a branch and
  regenerate `package-lock.json` there (`npx npm@11 install --package-lock-only`; npm 10's
  audit fix crashes on this workspace). Then check that `npm ls react react-dom` shows exactly
  one copy of each.
- **Majors are ledger items.** A semver-major is ignored by the bot, in pyprojects and in npm
  alike: each needs its own migration and review, and a round of npm majors once held every CI
  runner (#32-#36). A major is taken as a ledger item that records what it needs (next and
  eslint-config-next 16: septa-57; TypeScript 7: septa-58; the npm majors round: septa-82). The node base image stays on an LTS
  line; the rule ignoring newer lines names septa-59.
- **Pins the ratchets depend on are never moved by the bot.** ruff, mypy, bandit and pip-audit
  are pinned to a minor because the lint and security ratchets (§4) count findings per version.
  The pyproject blocks ignore them entirely. Moving one is done by hand, with the baseline
  re-measured in the same change.

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
`plan_ahead.*`, `drift_monitor.*`, and so on. Every Guardian verdict is a row in `audit_log`.
There is no metrics pipeline, dashboard or alerting on these. With `ENV=local`, the verdict
signing key's absence is logged at CRITICAL (`guardian.signing_key.ephemeral`); with any other
`ENV` it stops start-up instead.

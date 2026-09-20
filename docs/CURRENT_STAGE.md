# Platform Current Stage

> **Last reconciled:** 2026-09-19, against the code at `d2a5e6a` plus the change that introduced this page.
> **How to read it:** every row has one of four statuses. `implemented` rows link to the test that pins the behaviour. Every other row names its entry in [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json), which cites where the claim was made, says in one line what the code does instead, and names what must exist for the entry to leave the list.
> **Checked, not asserted:** `scripts/status_claims.py` (run by `.gnomon/ci.sh`) fails if a row's status disagrees with the register, an `implemented` row's test is missing, an open register entry is missing from this page, or a cited claim or piece of code has changed under the register.

This page replaces an earlier version that marked "Phase 1" finished. A September 2026 audit found that most of the causal and governance rows marked done were heuristic, constant or unreachable, so the phase framing has been withdrawn. The earlier version is still in git history (`4e036a0`).

## Summary

- **Causal numbers the platform serves.** They come from `packages/causal-core`. The backend's `effect_estimate` and `identification_check` skills delegate to it, and so do the API and the legacy MCP server. Without observational data they return no estimate and say why; the old graph-heuristic numbers and the constant interval are gone. Ground truth is gated by the pre-registered T0, LaLonde and IHDP benchmarks.
- **What is still heuristic, and labelled so.** `counterfactual_reason` and `intervention_estimate` return triage scores with `heuristic: true` and a caveat. No counterfactual is computed. Rosenbaum bounds and discovery are not implemented.
- **The kernel's own limits** are on the register too: triangulation at moderate n, overlap detection and general-ID estimation.
- **LLMs.** No LLM is called anywhere. The agents are rule-based code.
- **Governance.** The registry refuses writes that lack an APPROVED verdict. Verdicts are not tied to one object, can be reused, and are not signed. `POST /api/governance/check` can never return APPROVED, so the approval flow cannot complete through the API.
- **Gates.** The local gate `.gnomon/ci.sh` passes. The GitHub Actions workflow now runs that same gate, but has never run on GitHub because nothing has been pushed.

## Status

### Causal inference

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Backend effect estimates (`effect_estimate` skill, API, legacy MCP tool) | `implemented` | [backend/tests/unit/test_effect_estimate.py](../backend/tests/unit/test_effect_estimate.py) | Delegates to causal-core; refuses without data |
| Confidence intervals | `implemented` | [backend/tests/unit/test_effect_estimate.py](../backend/tests/unit/test_effect_estimate.py) | Kernel intervals; coverage gated by T0 |
| Frontdoor estimation (linear) | `implemented` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) | Strategy label comes from the certificate |
| Identification check | `implemented` | [backend/tests/unit/test_effect_estimate.py](../backend/tests/unit/test_effect_estimate.py) | causal-core identification on the fragment graph |
| Counterfactuals over a structural causal model | `declared-not-implemented` | [dni:scm-counterfactuals](../conformance/declared_not_implemented.json) | Skill is a labelled heuristic (`NOT_ASSESSED`) |
| Causal discovery from data | `declared-not-implemented` | [dni:causal-discovery](../conformance/declared_not_implemented.json) | One edge between `uuid4()` nodes |
| Sensitivity analysis (E-value, Rosenbaum bounds) | `partial` | [dni:sensitivity-analysis](../conformance/declared_not_implemented.json) | E-values right; no Rosenbaum bounds |
| EconML / PyMC estimation backends | `declared-not-implemented` | [dni:econml-pymc](../conformance/declared_not_implemented.json) | Not dependencies |
| Rust crates for DAG operations and belief tracking | `declared-not-implemented` | [dni:rust-crates](../conformance/declared_not_implemented.json) | No Rust in this repository |
| `packages/causal-core`: identification catalog, estimation bias and interval coverage, refuter and triangulation error rates, per its pre-registered T0 gate | `implemented` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) | The backend skills call it |
| `causal-core`: ATT estimand (doubly robust, regression and weighting), one-sided overlap | `implemented` | [packages/causal-core/tests/test_pipeline.py](../packages/causal-core/tests/test_pipeline.py) | T0 v3 heterogeneous-effect cases |
| `causal-core`: no confident wrong answer on real data (LaLonde NSW vs PSID/CPS) and ATT coverage on IHDP-100 | `implemented` | [packages/causal-core/tests/bench/test_real_data.py](../packages/causal-core/tests/bench/test_real_data.py) | Pre-registered T3/T2; data fetched by pinned SHA-256, not committed |
| `causal-core`: runtime dependencies permissively licensed | `implemented` | [packages/causal-core/tests/test_licenses.py](../packages/causal-core/tests/test_licenses.py) | Transitive, platform markers evaluated |
| `causal-core`: triangulation detects moderate misspecification at moderate n | `partial` | [dni:kernel-triangulation-moderate-n](../conformance/declared_not_implemented.json) | Power 0.035 at n=2,000; 1.00 at n=50,000 |
| `causal-core`: overlap violations detected before estimation | `partial` | [dni:kernel-overlap-detection-limits](../conformance/declared_not_implemented.json) | IHDP's built-in violation not detected |
| `causal-core`: estimation of general ID functionals (napkin) | `declared-not-implemented` | [dni:kernel-general-id-estimation](../conformance/declared_not_implemented.json) | Identified, then refused `NO_ESTIMATOR` |
| `causal-core`: nonparametric front-door, discovery, time series, sequential tests, interference | `planned` | [dni:kernel-methods-roadmap](../conformance/declared_not_implemented.json) | |

### Governance

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Registry writes refused without an APPROVED `audit_log` verdict | `implemented` | [backend/tests/guardian/test_guardian_bypass.py](../backend/tests/guardian/test_guardian_bypass.py) | Existence and status only; see next row |
| Verdicts bound to one object, single-use, unforgeable | `declared-not-implemented` | [dni:guardian-verdict-binding](../conformance/declared_not_implemented.json) | Replayable and forgeable |
| API approval flow (`/check`, then `/approve`) | `declared-not-implemented` | [dni:guardian-check-always-rejects](../conformance/declared_not_implemented.json) | `/check` always REJECTED (PR-006) |
| Approval notes and rejection reasons stored as text | `declared-not-implemented` | [dni:governance-reasons-not-stored](../conformance/declared_not_implemented.json) | Only a payload hash survives |
| `/reject` validates the verdict it is given | `declared-not-implemented` | [dni:governance-reject-verdict-unchecked](../conformance/declared_not_implemented.json) | Any UUID accepted |
| Review queue risk tier and submitter | `declared-not-implemented` | [dni:review-queue-risk-tier](../conformance/declared_not_implemented.json) | UI shows MEDIUM / "system" |
| Audit log bounded by the client's `limit` | `declared-not-implemented` | [dni:audit-log-limit-ignored](../conformance/declared_not_implemented.json) | Endpoint reads `page_size` |
| Governance UI shows only backend-confirmed decisions and audit rows | `implemented` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) | Fixed in `85fe619` |
| Provenance records written by real flows | `declared-not-implemented` | [dni:provenance-store-no-callers](../conformance/declared_not_implemented.json) | Writer has no callers |
| TLA+ model of the Guardian policy, checked by TLC | `declared-not-implemented` | [dni:tla-spec-not-checked](../conformance/declared_not_implemented.json) | Not runnable as written |

### Agents, MCP and API

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Backend MCP server (`backend/mcp_server`): 7 causal tools over stdio, stdout reserved for protocol frames | `implemented` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) | Outputs are only as real as the causal rows above |
| `packages/causal-mcp`: causal-core over MCP (check graph, identify, analyze, verify narrative) | `implemented` | [packages/causal-mcp/tests/test_stdio_flow.py](../packages/causal-mcp/tests/test_stdio_flow.py) | Separate server; wired into `.gnomon/tools.toml` as `causal_core` |
| LLM capability routing used by the agents | `declared-not-implemented` | [dni:llm-routing-no-callers](../conformance/declared_not_implemented.json) | `get_llm` has no callers |
| Session orchestration API (`/api/v1/session/run`, `/incident`) | `implemented` | [backend/tests/integration/test_session_api.py](../backend/tests/integration/test_session_api.py) | Agents are rule-based code |
| Intervention feedback endpoint | `implemented` | [backend/tests/unit/test_interventions.py](../backend/tests/unit/test_interventions.py) | |
| Health endpoint | `implemented` | [backend/tests/unit/test_health_api.py](../backend/tests/unit/test_health_api.py) | |

### Data and infrastructure

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Prometheus connector (queries, response parsing) | `implemented` | [backend/tests/unit/test_connectors.py](../backend/tests/unit/test_connectors.py) | HTTP is mocked in the tests |
| Neo4j as the causal graph store | `declared-not-implemented` | [dni:neo4j-graph-store](../conformance/declared_not_implemented.json) | Graph lives in SQL; Neo4j paths tested only with mocks |
| Kafka event streaming | `declared-not-implemented` | [dni:kafka-streaming](../conformance/declared_not_implemented.json) | No client; broker unused |

### Frontend

| Capability | Status | Evidence | Note |
|---|---|---|---|
| DAG Workbench (unified graph authoring) | `planned` | [dni:dag-workbench](../conformance/declared_not_implemented.json) | Not built |

### Delivery

| Capability | Status | Evidence | Note |
|---|---|---|---|
| GitHub Actions quality gates that block a merge | `partial` | [dni:ci-pipeline-green](../conformance/declared_not_implemented.json) | Workflow runs `.gnomon/ci.sh`; never run on GitHub (not pushed) |

## Fixed items (moved off the register)

| Was | Fixed in | Pinned by |
|---|---|---|
| 2 of 7 MCP tools crashed at INFO; logs went to stdout, the stdio protocol channel | `6768cc6` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) |
| Governance UI showed approvals and audit rows no backend call had confirmed | `85fe619` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) |
| `packages/causal-core` was planned, not built | `06fd061` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) |
| Backend effect estimates unpinned; constant (−0.5, 0.5) interval; heuristic identification; front-door label on a back-door estimate | backend rewire onto causal-core | [backend/tests/unit/test_effect_estimate.py](../backend/tests/unit/test_effect_estimate.py) |

## Gates

`.gnomon/ci.sh` is the command that decides whether a change passes. It prints PASS, FAIL or SKIPPED for each step, with a count of what the step inspected. The numbers it holds are ratchets. Each file records when and against which commit its number was measured:

| Gate | Committed baseline | Measured 2026-09-19 |
|---|---|---|
| Backend tests (unit, integration, guardian) | at least 500 tests; every test file reports ([`scripts/gate-scope-floors.json`](../scripts/gate-scope-floors.json)) | 560 passed |
| Backend line coverage, full suite | floor 80.0%, may only rise ([`scripts/coverage-floor.json`](../scripts/coverage-floor.json)) | 80.01% |
| ruff (backend/) | at most 451 findings, may only fall ([`scripts/lint-baseline.json`](../scripts/lint-baseline.json)) | 451 |
| mypy --strict (backend/src) | at most 46 errors, may only fall | 46 |
| Frontend tsc / vitest | must pass | 46 files, 47 tests |
| Frontend eslint | none: `frontend/` has no eslint config | SKIPPED |
| Every Python package under `packages/` | its suite must pass, in the package's environment | causal-core 100 passed (T0 gate and real-data benchmarks included); causal-mcp 10 passed |
| Skill modules imported by a test | 6 known gaps, may only shrink | 4 of 10 |
| State-changing routes called by a test | 6 known gaps, may only shrink | 5 of 11 |
| MCP tools referenced by a test | no gaps allowed | 7 of 7 |

`scripts/doc_reconciliation.py` reports which documents in `docs/` are owed a re-read because the code they describe has changed. Only this page and the root README had been read against the code when tracking began.

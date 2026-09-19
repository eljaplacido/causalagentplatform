# Platform Current Stage

> **Last reconciled:** 2026-09-19, against the code at `2ba6328` plus the change that introduced this page.
> **How to read it:** every row has one of four statuses. `implemented` rows link to the test that pins the behaviour. Every other row names its entry in [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json), which cites where the claim was made, says in one line what the code does instead, and names what must exist for the entry to leave the list.
> **Checked, not asserted:** `scripts/status_claims.py` (run by `.gnomon/ci.sh`) fails if a row's status disagrees with the register, an `implemented` row's test is missing, an open register entry is missing from this page, or a cited claim or piece of code has changed under the register.

This page replaces an earlier version that marked "Phase 1" finished. A September 2026 audit found that most of the causal and governance rows marked done were heuristic, constant or unreachable, so the phase framing has been withdrawn. The earlier version is still in git history (`3e57d58`).

## Summary

- **Causal numbers the platform serves.** In the backend skills and the MCP tools, DoWhy's backdoor linear-regression and IV point estimates are the only causal numbers a real estimator computes, and no test checks them against a known true effect. Confidence intervals, identification, frontdoor estimation, counterfactuals, discovery and sensitivity analysis are heuristics or constants.
- **The new kernel.** `packages/causal-core` identifies before it estimates, reports influence-function intervals, refuses with a typed reason, and passes its pre-registered T0 correctness gate. A separate MCP server, `packages/causal-mcp`, exposes it as four tools. Nothing in `backend/` calls it, and the backend's own MCP server (`backend/mcp_server`) still serves the heuristic skills, so the causal rows below stay open until the platform uses the kernel.
- **LLMs.** No LLM is called anywhere. The agents are rule-based code.
- **Governance.** The registry refuses writes that lack an APPROVED verdict. Verdicts are not tied to one object, can be reused, and are not signed. `POST /api/governance/check` can never return APPROVED, so the approval flow cannot complete through the API.
- **Gates.** The local gate `.gnomon/ci.sh` passes. The GitHub Actions pipeline does not.

## Status

### Causal inference

| Capability | Status | Evidence | Note |
|---|---|---|---|
| ATE point estimate: DoWhy backdoor linear regression, IV | `partial` | [dni:effect-estimate-unpinned](../conformance/declared_not_implemented.json) | Real DoWhy call; no ground-truth test |
| Confidence intervals | `declared-not-implemented` | [dni:confidence-interval-constant](../conformance/declared_not_implemented.json) | Constant (-0.5, 0.5) |
| Frontdoor estimation | `declared-not-implemented` | [dni:frontdoor-estimation](../conformance/declared_not_implemented.json) | Runs backdoor, labelled frontdoor |
| Identification check | `declared-not-implemented` | [dni:identification-heuristic](../conformance/declared_not_implemented.json) | Assumption flags over dummy columns |
| Counterfactuals over a structural causal model | `declared-not-implemented` | [dni:scm-counterfactuals](../conformance/declared_not_implemented.json) | Formula over edge confidences |
| Causal discovery from data | `declared-not-implemented` | [dni:causal-discovery](../conformance/declared_not_implemented.json) | One edge between `uuid4()` nodes |
| Sensitivity analysis (E-value, Rosenbaum bounds) | `declared-not-implemented` | [dni:sensitivity-analysis](../conformance/declared_not_implemented.json) | Scale-free formulas |
| EconML / PyMC estimation backends | `declared-not-implemented` | [dni:econml-pymc](../conformance/declared_not_implemented.json) | Not dependencies |
| Rust crates for DAG operations and belief tracking | `declared-not-implemented` | [dni:rust-crates](../conformance/declared_not_implemented.json) | No Rust in this repository |
| `packages/causal-core` (standalone): identification catalog, estimation bias and interval coverage, refuter and triangulation error rates, per its pre-registered T0 gate | `implemented` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) | Not called by `backend/` |

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
| Governance UI shows only backend-confirmed decisions and audit rows | `implemented` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) | Fixed in `17a394d` |
| Provenance records written by real flows | `declared-not-implemented` | [dni:provenance-store-no-callers](../conformance/declared_not_implemented.json) | Writer has no callers |
| TLA+ model of the Guardian policy, checked by TLC | `declared-not-implemented` | [dni:tla-spec-not-checked](../conformance/declared_not_implemented.json) | Not runnable as written |

### Agents, MCP and API

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Backend MCP server (`backend/mcp_server`): 7 causal tools over stdio, stdout reserved for protocol frames | `implemented` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) | Outputs are only as real as the causal rows above |
| `packages/causal-mcp`: causal-core over MCP (check graph, identify, analyze, verify narrative) | `implemented` | [packages/causal-mcp/tests/test_stdio_flow.py](../packages/causal-mcp/tests/test_stdio_flow.py) | Separate server; not what `.gnomon/tools.toml` wires in |
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
| GitHub Actions quality gates that block a merge | `declared-not-implemented` | [dni:ci-pipeline-green](../conformance/declared_not_implemented.json) | `.gnomon/ci.sh` is the working gate |

## Fixed items (moved off the register)

| Was | Fixed in | Pinned by |
|---|---|---|
| 2 of 7 MCP tools crashed at INFO; logs went to stdout, the stdio protocol channel | `3aecd16` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) |
| Governance UI showed approvals and audit rows no backend call had confirmed | `17a394d` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) |
| `packages/causal-core` was planned, not built | `a0e4951` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) |

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
| Every Python package under `packages/` | its suite must pass, in the package's environment | causal-core 93 passed (T0 gate included); causal-mcp 10 passed |
| Skill modules imported by a test | 6 known gaps, may only shrink | 4 of 10 |
| State-changing routes called by a test | 6 known gaps, may only shrink | 5 of 11 |
| MCP tools referenced by a test | no gaps allowed | 7 of 7 |

`scripts/doc_reconciliation.py` reports which documents in `docs/` are owed a re-read because the code they describe has changed. Only this page and the root README had been read against the code when tracking began.

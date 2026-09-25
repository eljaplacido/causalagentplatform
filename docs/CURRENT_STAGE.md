# Platform Current Stage

> **Last reconciled:** 2026-09-25, on branch `chore/alignment-and-cleanup` at `4b908bb` (difference-in-differences, the assumption registry, ledger checkpoints and the legacy MCP server's deprecation included).
> **How to read it:** every row has one of four statuses. `implemented` rows link to the test that pins the behaviour. Every other row names its entry in [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json), which cites where the claim was made, says in one line what the code does instead, and names what must exist for the entry to leave the list.
> **Checked, not asserted:** `scripts/status_claims.py` (run by `.gnomon/ci.sh`) fails if a row's status disagrees with the register, an `implemented` row's test is missing, an open register entry is missing from this page, or a cited claim or piece of code has changed under the register.

This page replaces an earlier version that marked "Phase 1" finished. A September 2026 audit found that most of the causal and governance rows marked done were heuristic, constant or unreachable, so the phase framing has been withdrawn. The earlier version is still in git history (`4e036a0`).

## Summary

- **Segments.** The kernel answers "for whom does this work?" for subgroups the caller declares in advance, with an interval per segment, a wider family-wise interval that accounts for how many were looked at, and one heterogeneity test before any two are compared. It does not search for subgroups. The decision layer now reads them: an action that materially harms a declared segment cannot be automated whatever the pooled effect says, and segments the analysis could not estimate are not treated as cleared. Declaring no subgroups adds no checks — that is not evidence the effect is uniform, only that nobody asked.
- **Causal numbers the platform serves.** They come from `packages/causal-core`. The backend's `effect_estimate` and `identification_check` skills delegate to it, and so do the API and the legacy MCP server. Without observational data they return no estimate and say why; the old graph-heuristic numbers and the constant interval are gone. Ground truth is gated by the pre-registered T0, LaLonde (T3), IHDP (T2), segments (T4) and difference-in-differences (T5) benchmarks.
- **Staggered rollouts.** `design="difference_in_differences"` with `unit` and `time` columns estimates group-time effects against never-treated units, with unit-clustered intervals and an event study. It is tier C: the result rests on parallel trends, which data cannot prove, and a failed pre-trends test makes it `contested`. Unbalanced panels and treatments that switch off are refused with a typed reason; covariates, not-yet-treated controls and anticipation are not offered (`did-extensions` on the register).
- **Whether an agent may act on those numbers.** `packages/causal-decision` turns an outcome into one of four verdicts (`act`, `act_with_approval`, `experiment`, `refuse`) from a policy the caller sets, and returns the experiment that would settle an unsettled question. It is deterministic and gated by the pre-registered D0 suite. It decides; it does not execute, and nothing yet checks whether the decisions were *right* — that is the calibration row below.
- **What was decided is kept.** `POST /api/v1/decisions` analyses and rules in one call — nothing can change the evidence in between — and seals the result into an append-only hash chain with the digests it rested on. `/api/v1/decisions/verify` recomputes the chain from the stored rows and names the first entry that fails. That detects tampering inside this platform's trust boundary. `/api/v1/decisions/checkpoint` returns a head to publish elsewhere and `/api/v1/decisions/verify-anchored` checks the chain against such checkpoints; nothing publishes one yet, so anchoring is `partial` on the register.
- **The assumptions a verdict rested on are kept, and a revision says what it breaks.** A causal model is registered once (`/api/v1/models`) and revised into immutable, content-addressed versions: the kernel's graph, each edge with its origin and review status, plus the assumptions a graph cannot state. A revision may propose edges but not approve them; approving is a review, role-gated, and nobody reviews an edge they introduced. A decision can name a version instead of sending a graph, the ledger records which, and `GET /api/v1/models/{name}/impact` answers, for every decision made on an older version, whether its estimate still stands if the new graph is true — gated by the pre-registered A0 suite against an oracle that shares no code with the kernel.
- **What is still heuristic, and labelled so.** `counterfactual_reason` and `intervention_estimate` return triage scores with `heuristic: true` and a caveat. No counterfactual is computed. Rosenbaum bounds and discovery are not implemented.
- **The kernel's own limits** are on the register too: triangulation at moderate n, overlap detection and general-ID estimation.
- **LLMs.** No LLM is called anywhere. The agents are rule-based code.
- **Governance.** A Guardian verdict now authorises exactly one write: it is signed by the issuer, names the object it was issued for, and is spent on use, so it cannot be forged by inserting a row, replayed against a second write, or pointed at a different object. The `/check` → `/approve` flow completes through the API when the caller classifies the situation; an unclassified one is still refused by PR-006. Approving or rejecting records what the person said, as text, in a row that is deliberately unsigned so a record of a decision can never be spent as authority for the next one. Deciding an intervention now needs a governance role, and the `audit_write` skill records a request as PENDING instead of letting an agent write down that it passed.
- **Gates.** `.gnomon/ci.sh` is the gate, and GitHub Actions runs that same script: green on `main` (run 35504722068, 13 steps passed, 1 named skip). The workflow fails on any skipped step it has not named, so a broken environment cannot read as a pass.

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
| `causal-core`: effects within declared segments, with family-wise correction and a heterogeneity test, per its pre-registered T4 gate | `implemented` | [packages/causal-core/tests/t4/test_t4_gate.py](../packages/causal-core/tests/t4/test_t4_gate.py) | Per-segment coverage 0.93–0.97, family-wise 0.945–0.98, 4% false discovery with every segment null |
| `causal-core`: the T4 gate catches every seeded defect, including a family-wise interval that is quietly unadjusted | `implemented` | [packages/causal-core/tests/t4/test_seeded_defects.py](../packages/causal-core/tests/t4/test_seeded_defects.py) | Each defect must fail the bar it was registered against, not merely fail something |
| `causal-core`: staggered difference-in-differences with unit-clustered intervals and an event study, per its pre-registered T5 gate | `implemented` | [packages/causal-core/tests/t5/test_t5_gate.py](../packages/causal-core/tests/t5/test_t5_gate.py) | Group-time effects against never-treated units; tier C; a failed pre-trends test makes the result `contested` |
| `causal-core`: the T5 gate catches every seeded defect, including two-way fixed effects and already-treated controls | `implemented` | [packages/causal-core/tests/t5/test_seeded_defects.py](../packages/causal-core/tests/t5/test_seeded_defects.py) | Each fails the bar it was registered against |
| Difference-in-differences with covariates, not-yet-treated controls, anticipation, or parallel-trends sensitivity | `planned` | [dni:did-extensions](../conformance/declared_not_implemented.json) | Refused with a typed reason, not approximated |
| Finding which subgroups an effect differs across | `planned` | [dni:segment-discovery](../conformance/declared_not_implemented.json) | Declared subgroups only; discovery needs its own guarantees |
| The decision layer accounts for heterogeneity across segments | `implemented` | [packages/causal-decision/tests/test_segments.py](../packages/causal-decision/tests/test_segments.py) | A declared segment the action materially harms blocks `act`, whatever the pooled effect says; heterogeneity alone caps at approval |
| `causal-core`: nonparametric front-door, discovery, time series, sequential tests, interference | `planned` | [dni:kernel-methods-roadmap](../conformance/declared_not_implemented.json) | |

### Decision assurance

The layer between an agent's proposed action and the system it would change: given the evidence,
may the agent act? Separate package, separate gate, and it executes nothing -- it returns a verdict.

| Capability | Status | Evidence | Note |
|---|---|---|---|
| `causal-decision`: one verdict (`act`, `act_with_approval`, `experiment`, `refuse`) from an outcome, a request and a policy, per its pre-registered D0 gate | `implemented` | [packages/causal-decision/tests/d0/test_d0_gate.py](../packages/causal-decision/tests/d0/test_d0_gate.py) | 17 scenarios (two with declared segments), 8 properties over a 400-case sweep; the expected verdicts are registered in `benchmarks/d0-decision-policy/PRE-REGISTRATION.md` (v2), not in the code |
| `causal-decision`: the gate catches every seeded defect, including reading a contested result as supported | `implemented` | [packages/causal-decision/tests/d0/test_seeded_defects.py](../packages/causal-decision/tests/d0/test_seeded_defects.py) | 7 defects, each named in the pre-registration with the check that must catch it |
| `causal-decision`: a decision is a pure function of its inputs and carries digests of all three | `implemented` | [packages/causal-decision/tests/test_policy.py](../packages/causal-decision/tests/test_policy.py) | Replayable; a verdict names the policy it followed |
| `causal-decision`: an unsettled question returns the experiment that would settle it (randomized units, size, duration, guardrails) | `implemented` | [packages/causal-decision/tests/test_policy.py](../packages/causal-decision/tests/test_policy.py) | Sized from the outcome's spread and the minimum worthwhile effect |
| `causal-decision`: every verdict's prose verifies against the analysis that produced it | `implemented` | [packages/causal-decision/tests/test_render.py](../packages/causal-decision/tests/test_render.py) | Unverifiable prose raises rather than prints |
| `causal_decide` over MCP: analysis and verdict in one call, so the evidence cannot change in between | `implemented` | [packages/causal-mcp/tests/test_decision_tool.py](../packages/causal-mcp/tests/test_decision_tool.py) | Optional extra `causal-mcp[decision]`; annotated read-only |
| `causal_impact` over MCP: whether a revised graph breaks an earlier answer | `implemented` | [packages/causal-mcp/tests/test_decision_tool.py](../packages/causal-mcp/tests/test_decision_tool.py) | Same extra; stateless over `causal_decision.assumptions.impact` (A0) |
| `causal-decision`: a decision is sealed into a hash chain with the evidence and approval behind it, and an edited, removed or reordered entry is detected and located | `implemented` | [packages/causal-decision/tests/test_ledger.py](../packages/causal-decision/tests/test_ledger.py) | Detects tampering; does not prevent it — anchoring the head digest is a deployment decision |
| Decisions made, recorded and served by the platform (`POST/GET /api/v1/decisions`, `/verify`) | `implemented` | [backend/tests/integration/test_decision_ledger.py](../backend/tests/integration/test_decision_ledger.py) | Analyse and rule in one call; the chain verifies from the stored rows, and an edited row is caught |
| The chain's head digest anchored outside the writer's control | `partial` | [dni:decision-ledger-not-anchored](../conformance/declared_not_implemented.json) | Checkpoints and verification against them exist; publishing one somewhere the writer cannot edit is the deployment's step, and nothing does it yet |
| Outcome capture: a decision records what would have to be re-measured, and the platform measures it | `implemented` | [backend/tests/integration/test_decision_followup.py](../backend/tests/integration/test_decision_followup.py) | `POST /api/v1/decisions/{entry_digest}/followup` re-runs the sealed plan's own analysis; `GET /api/v1/decisions/calibration` aggregates |
| Calibration: were the actions taken under `act` right? | `partial` | [dni:decision-thresholds-uncalibrated](../conformance/declared_not_implemented.json) | The loop produces the numbers; no follow-up has been taken on a real operation, so every rate is null |
| A decision is measured once: a second follow-up cannot outvote the first | `implemented` | [backend/tests/integration/test_decision_followup.py](../backend/tests/integration/test_decision_followup.py) | 409 from the API, a unique index beneath it, and `report()` refuses duplicates |

### Assumption registry

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| `causal-decision`: causal models as immutable, content-addressed versions; no revision approves an edge, and nobody reviews an edge they introduced | `implemented` | [packages/causal-decision/tests/test_assumptions.py](../packages/causal-decision/tests/test_assumptions.py) | The rule holds across versions: each version carries who introduced each edge |
| `causal-decision`: whether a revision invalidates a verdict's estimate, per its pre-registered A0 gate | `implemented` | [packages/causal-decision/tests/a0/test_a0_gate.py](../packages/causal-decision/tests/a0/test_a0_gate.py) | 800 back-door cases, 800 agree with an independent linear-Gaussian oracle; front-door/IV/proximal hold only on an identical certificate, else `unverified` |
| `causal-decision`: the A0 gate catches every seeded defect, including "still back-door" and "the new set is valid" | `implemented` | [packages/causal-decision/tests/a0/test_seeded_defects.py](../packages/causal-decision/tests/a0/test_seeded_defects.py) | 4 defects; the v1 run failed its class-balance floor and is kept |
| Models registered, revised, reviewed and served (`/api/v1/models`), decisions naming a version, and `impact` over the recorded decisions | `implemented` | [backend/tests/integration/test_models_api.py](../backend/tests/integration/test_models_api.py) | Reviews need a governance role; versions and decision references are append-only |

### Governance

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Registry writes refused without an APPROVED `audit_log` verdict | `implemented` | [backend/tests/guardian/test_guardian_bypass.py](../backend/tests/guardian/test_guardian_bypass.py) | Existence and status only; see next row |
| Verdicts bound to one object, single-use, unforgeable | `implemented` | [backend/tests/guardian/test_verdict_binding.py](../backend/tests/guardian/test_verdict_binding.py) | HMAC-signed claim naming the subject; spent by insert into `verdict_consumption` |
| API approval flow (`/check`, then `/approve`) | `implemented` | [backend/tests/integration/test_governance_approval_flow.py](../backend/tests/integration/test_governance_approval_flow.py) | A classified, compliant payload is APPROVED; unclassified is still PR-006 REJECTED |
| Intervention approval restricted to governance roles | `implemented` | [backend/tests/integration/test_intervention_approval_role.py](../backend/tests/integration/test_intervention_approval_role.py) | `governance_officer`, `platform_engineer` or `sre`; reading still needs only auth |
| Agents cannot choose their own Guardian verdict | `implemented` | [backend/tests/guardian/test_audit_write_skill.py](../backend/tests/guardian/test_audit_write_skill.py) | `audit_write` records as PENDING and unsigned; rulings come from `guardian_check` |
| Approval notes and rejection reasons stored as text | `implemented` | [backend/tests/integration/test_governance_reasons_recorded.py](../backend/tests/integration/test_governance_reasons_recorded.py) | `audit_log.notes`, readable from `/audit-log`; the record is unsigned so it cannot be spent |
| `/reject` validates the verdict it is given | `implemented` | [backend/tests/integration/test_governance_reject_verdict.py](../backend/tests/integration/test_governance_reject_verdict.py) | Same four checks as approval, against a REJECTED verdict |
| Review queue risk tier and submitter | `declared-not-implemented` | [dni:review-queue-risk-tier](../conformance/declared_not_implemented.json) | UI shows MEDIUM / "system" |
| Audit-log requests bounded by the client's page size | `implemented` | [frontend/src/lib/\_\_tests\_\_/api.test.ts](../frontend/src/lib/__tests__/api.test.ts) | The client sends `page_size`, the parameter the endpoint reads |
| Governance UI shows only backend-confirmed decisions and audit rows | `implemented` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) | Fixed in `85fe619` |
| Provenance records written by real flows | `declared-not-implemented` | [dni:provenance-store-no-callers](../conformance/declared_not_implemented.json) | Writer has no callers |
| Runtime data contracts (freshness, schema) enforced by Guardian PR-009 | `declared-not-implemented` | [dni:data-contracts-not-enforced](../conformance/declared_not_implemented.json) | Models only; PR-009 reads violations from the caller's payload |
| Hallucination-risk scoring gating Guardian approval (PR-002) | `declared-not-implemented` | [dni:guardian-risk-score-caller-supplied](../conformance/declared_not_implemented.json) | Nothing computes it; `/check` takes it from the request body |
| Guardian PR-007 blocks edges without a sensitivity result | `declared-not-implemented` | [dni:sensitivity-gate-not-enforced](../conformance/declared_not_implemented.json) | In the rule table, never evaluated |
| Role-based access per platform role; domain isolation | `partial` | [dni:rbac-matrix-and-domain-isolation](../conformance/declared_not_implemented.json) | Two role sets on seven routes; other writes need only a token; RLS never engaged |
| Security scanning in CI (SAST, dependency audit, container scan) | `declared-not-implemented` | [dni:security-scanning-not-in-ci](../conformance/declared_not_implemented.json) | Tools declared as dev dependencies; no step runs them |
| Credentials in a secret manager, not environment variables | `declared-not-implemented` | [dni:secrets-in-env](../conformance/declared_not_implemented.json) | All secrets, including the Guardian signing key, come from env |
| PII pseudonymization, erasure and export | `declared-not-implemented` | [dni:gdpr-controls](../conformance/declared_not_implemented.json) | None exists |
| TLA+ model of the Guardian policy, checked by TLC | `declared-not-implemented` | [dni:tla-spec-not-checked](../conformance/declared_not_implemented.json) | Not runnable as written |
| Semantic objects versioned, never mutated in place | `declared-not-implemented` | [dni:semantic-objects-mutated-in-place](../conformance/declared_not_implemented.json) | Approve/reject UPDATE the row; `version` never changes |
| Lineage from source data to approver on every approved object | `partial` | [dni:lineage-partial](../conformance/declared_not_implemented.json) | One step per human decision; fragments had no lineage column until `006` |

### Agents, MCP and API

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Backend MCP server (`backend/mcp_server`): 7 causal tools over stdio, stdout reserved for protocol frames | `implemented` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) | Outputs are only as real as the causal rows above |
| `packages/causal-mcp`: causal-core over MCP (check graph, identify, analyze, verify narrative, and `causal_decide` and `causal_impact` with the decision extra) | `implemented` | [packages/causal-mcp/tests/test_stdio_flow.py](../packages/causal-mcp/tests/test_stdio_flow.py) | The canonical server; wired into `.gnomon/tools.toml` as `causal_core` and granted to every role except `smol` (summarisation only) |
| One MCP server: the legacy backend server retired | `planned` | [dni:legacy-mcp-server](../conformance/declared_not_implemented.json) | Deprecated: granted to no gnomon role, warns on startup; not yet deleted |
| LLM capability routing used by the agents | `declared-not-implemented` | [dni:llm-routing-no-callers](../conformance/declared_not_implemented.json) | `get_llm` has no callers |
| Agents render prompts from the versioned templates in `prompts/` | `declared-not-implemented` | [dni:prompt-templates-not-loaded](../conformance/declared_not_implemented.json) | Nothing loads them; they are specification artefacts |
| `EvidenceGroundingAgent` and `DomainExpertElicitationAgent` produce the outputs AGENTS.md specifies | `declared-not-implemented` | [dni:agent-skills-are-stubs](../conformance/declared_not_implemented.json) | Every skill under them returns a constant; the docstrings say so, AGENTS.md does not |
| Session orchestration API (`/api/v1/session/run`, `/incident`) | `implemented` | [backend/tests/integration/test_session_api.py](../backend/tests/integration/test_session_api.py) | Agents are rule-based code |
| Intervention feedback endpoint | `implemented` | [backend/tests/unit/test_interventions.py](../backend/tests/unit/test_interventions.py) | |
| Health endpoint | `implemented` | [backend/tests/unit/test_health_api.py](../backend/tests/unit/test_health_api.py) | |
| Agent evaluation endpoints (consistency, calibration, decision quality) measure the platform | `declared-not-implemented` | [dni:evaluation-endpoints-not-measured](../conformance/declared_not_implemented.json) | Each scores its own inputs; `calibration` here is confidence spread, not calibration |
| FLARE planning simulates each action with the approved causal model | `declared-not-implemented` | [dni:planning-simulation-no-data](../conformance/declared_not_implemented.json) | Kernel refuses without data; lookahead rescales the action's own confidence |

### Data and infrastructure

| Capability | Status | Evidence | Note |
|---|---|---|---|
| Prometheus connector (queries, response parsing) | `implemented` | [backend/tests/unit/test_connectors.py](../backend/tests/unit/test_connectors.py) | HTTP is mocked in the tests |
| Neo4j as the causal graph store | `declared-not-implemented` | [dni:neo4j-graph-store](../conformance/declared_not_implemented.json) | Graph lives in SQL; Neo4j paths tested only with mocks |
| Kafka event streaming | `declared-not-implemented` | [dni:kafka-streaming](../conformance/declared_not_implemented.json) | No client; broker unused |
| Session state and incidents persisted in PostgreSQL | `declared-not-implemented` | [dni:postgres-schema-missing-session-incidents](../conformance/declared_not_implemented.json) | Tables exist only in the SQLite DDL |
| Drift monitoring raises alerts from live data | `declared-not-implemented` | [dni:drift-monitor-no-data](../conformance/declared_not_implemented.json) | The agent hands its drift skills no data |

### Frontend

| Capability | Status | Evidence | Note |
|---|---|---|---|
| DAG Workbench (unified graph authoring) | `planned` | [dni:dag-workbench](../conformance/declared_not_implemented.json) | Not built |
| The frontend narrows on the canonical vocabulary | `implemented` | [frontend/src/lib/\_\_tests\_\_/types-parity.test.ts](../frontend/src/lib/__tests__/types-parity.test.ts) | `EvidenceType` imported from the contract package; the queue's status renamed `ReviewQueueStatus`, a view model |
| The TypeScript contract package matches the backend models field for field | `declared-not-implemented` | [dni:semantic-types-field-divergence](../conformance/declared_not_implemented.json) | Hand-written; seven interfaces declare fields the models lack |
| Shared enumerations mean the same thing in backend, contract package and frontend | `implemented` | [backend/tests/unit/test_contract_parity.py](../backend/tests/unit/test_contract_parity.py) | 18 of 18 shared names agree; `scripts/contract-parity.json` lists no divergence, and a new one fails the gate |

## Fixed items (moved off the register)

| Was | Fixed in | Pinned by |
|---|---|---|
| 2 of 7 MCP tools crashed at INFO; logs went to stdout, the stdio protocol channel | `6768cc6` | [backend/tests/integration/test_mcp_stdio.py](../backend/tests/integration/test_mcp_stdio.py) |
| Governance UI showed approvals and audit rows no backend call had confirmed | `85fe619` | [frontend/src/app/governance/\_\_tests\_\_/page.test.tsx](../frontend/src/app/governance/__tests__/page.test.tsx) |
| GitHub Actions pipeline: 0 of 10 jobs could pass | `32d0254` | [.github/workflows/ci.yml](../.github/workflows/ci.yml) (run 35504722068 on `main`: 13 passed, 1 named skip) |
| `packages/causal-core` was planned, not built | `06fd061` | [packages/causal-core/tests/t0/test_t0_gate.py](../packages/causal-core/tests/t0/test_t0_gate.py) |
| Backend effect estimates unpinned; constant (−0.5, 0.5) interval; heuristic identification; front-door label on a back-door estimate | backend rewire onto causal-core | [backend/tests/unit/test_effect_estimate.py](../backend/tests/unit/test_effect_estimate.py) |

## Gates

`.gnomon/ci.sh` is the command that decides whether a change passes. It prints PASS, FAIL or SKIPPED for each step, with a count of what the step inspected. The numbers it holds are ratchets. Each file records when and against which commit its number was measured:

| Gate | Committed baseline | Measured 2026-09-25 (at `4b908bb`) |
|---|---|---|
| Backend tests (unit, integration, guardian) | at least 500 tests; every test file reports ([`scripts/gate-scope-floors.json`](../scripts/gate-scope-floors.json)) | 637 passed, 45 of 45 files |
| Backend line coverage, full suite | floor 82.2%, may only rise ([`scripts/coverage-floor.json`](../scripts/coverage-floor.json)) | 82.21% |
| ruff (backend/) | at most 417 findings, may only fall ([`scripts/lint-baseline.json`](../scripts/lint-baseline.json)) | 417 |
| ruff (scripts/) | zero | 0 |
| mypy --strict (backend/src) | at most 39 errors, may only fall | 39 |
| mypy --strict (each package's src) | zero, per package | 0 for causal-core, causal-decision and causal-mcp |
| Frontend tsc / vitest | must pass | 50 files typecheck; 62 tests in 8 files |
| Frontend eslint | none: `frontend/` has no eslint config | SKIPPED |
| Every Python package under `packages/` | its suite must pass in the package's environment, and a skipped test fails it | causal-core 137 passed (T0, T4 and T5 gates included); causal-decision 117 (D0 and A0); causal-mcp 23 |
| Skill modules imported by a test | 2 known gaps, may only shrink | 9 of 11 |
| State-changing routes called by a test | 2 known gaps, may only shrink | 15 of 17 |
| MCP tools referenced by a test (legacy server) | no gaps allowed | 7 of 7 |

`scripts/doc_reconciliation.py` reports which documents in `docs/` are owed a re-read because the code they describe has changed. Only this page and the root README had been read against the code when tracking began.

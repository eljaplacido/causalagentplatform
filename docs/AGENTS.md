# Agents, Skills & Prompt Architecture

> **Scope:** Canonical reference for all agents deployed across the Unified Enterprise Causal Platform. Every agent, skill, and orchestration pattern must conform to this document. This file is loaded as shared context by every agent in the system.
>
> **This document is a specification, not a description of the code.** All eight agents below exist and run, but several of the skills beneath them return a constant. What is actually implemented is in [`CURRENT_STAGE.md`](CURRENT_STAGE.md), where every row links to the test that pins it; what this document claims and the code does not do is in [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json), checked on every run by `scripts/status_claims.py`. As of 2026-09-25 that covers the evidence-grounding and expert-elicitation skills (`agent-skills-are-stubs`), causal discovery (`causal-discovery`), the counterfactual skill (`scm-counterfactuals`), the capability-routed LLM layer, which nothing calls (`llm-routing-no-callers`), and the Cynefin classifier, which is a keyword stub (`cynefin-classifier-keyword-stub`). Earlier versions also named Rust crates (`cynepic-*`), EconML, PyMC, Kafka and a Neo4j causal-graph store as backing these agents; none of them is in this repository or used by it, and those claims were withdrawn (`rust-crates`, `econml-pymc`, `kafka-streaming`, `neo4j-graph-store` in the register's `left_the_list`).

---

## 1. Platform Agent Philosophy

Every agent in this platform is **epistemically aware**: it explicitly tracks what it knows, how confident it is, and what domain of complexity it is operating in before acting. This is not optional. It is the foundational contract of the platform.

Agents do not generate outputs directly. They generate **EpistemicState transitions** that are validated by the Guardian before any effect reaches storage, UI, or downstream systems.

### Core Design Principles

| Principle | Implication |
|---|---|
| Causal-first reasoning | Agents propose causal edges, not correlations. Every claim must cite structural evidence. |
| Human-in-the-loop by default | Agents propose. Humans approve. Approvals write to the semantic layer. |
| Guardian-gated outputs | Every agent output passes the Guardian policy engine before persistence. |
| Cynefin-aware routing | Agents self-classify the domain complexity of a query before selecting a reasoning strategy. |
| Immutable audit trails | All agent actions, tool calls, and state transitions are appended to the audit log. |
| Provider-agnostic LLM | Agents specify capability requirements, not model names. The runtime selects the provider. |

---

## 2. Agent Registry

### 2.1 Core Platform Agents

#### `CausalDiscoveryAgent`
**Role:** Proposes candidate edges, confounders, mediators, and missing variables in the causal graph from data and document evidence.

**Inputs:**
- Dataset schema + sample statistics
- Existing graph fragment (partial DAG)
- Domain context from the semantic layer
- Prior assumption list

**Outputs:**
- Ranked candidate edge list with confidence scores and evidence citations
- Flagged contradiction set (edges inconsistent with known domain constraints)
- Suggested missing variables

**Constraints:**
- Never writes edges directly to the semantic layer
- Must cite supporting evidence (statistical test, document reference, or prior graph asset) for every proposed edge
- Must surface all detected d-separation violations

**Tools available:** `causal_propose_edge`, `confounder_screen`, `conditional_independence_test`, `evidence_link`, `semantic_layer_read`

**Backed by:** `packages/causal-core` for graph checks and identification (networkx); no statistical discovery is implemented (`causal-discovery` in the register)

---

#### `DomainExpertElicitationAgent`
**Role:** Facilitates structured knowledge elicitation from domain experts. Translates natural-language domain knowledge into candidate graph structure.

**Inputs:**
- Expert description of a domain process (free text or structured interview)
- Prior graph fragments
- Ontology terms from the semantic layer

**Outputs:**
- Structured assumption statements
- Candidate edges annotated with expert justification
- Disagreement flags when expert statements conflict with existing graph assets

**Constraints:**
- Must always present proposed changes as suggestions, never as facts
- Must surface uncertainty when expert statements are ambiguous
- Must record the expert's identity and timestamp for audit

**Tools available:** `assumption_elicit`, `ontology_match`, `conflict_detect`, `semantic_layer_read`, `assumption_write_draft`

---

#### `GovernanceAgent`
**Role:** Cross-cutting control plane agent. Runs assumption review workflows, sensitivity tests, placebo checks, and policy enforcement across all platform operations.

**Inputs:**
- Approved graph assets
- Proposed interventions or simulation requests
- Configured policy rules (YAML / OPA)

**Outputs:**
- Policy verdict: `APPROVED` / `REJECTED` / `REQUIRES_ESCALATION`
- Sensitivity analysis results
- Placebo and negative-control check results
- Audit trail entries

**Constraints:**
- Cannot be bypassed or overridden by other agents
- Escalation must route to a human reviewer, never to another agent
- All verdicts are immutable once written
- A run whose PlanAhead lookahead found no safe action is never approved: a check whose payload carries `plan_outcome: "no_safe_action"` is `REQUIRES_ESCALATION` (rule PR-010, `src/agents/governance.py`)

**Tools available:** `guardian_check`, `sensitivity_test`, `placebo_check`, `assumption_audit`, `drift_detect`, `audit_write`

**Backed by:** `GovernanceAgent` in `src/agents/governance.py` (policy rules PR-001 to PR-005 evaluated in Python); OPA and CSL-Core are not used

---

#### `SimulationAgent`
**Role:** Executes counterfactual and what-if simulations against approved causal models. Provides intervention comparisons, scenario ranges, and narrative explanations.

**Inputs:**
- Approved causal graph asset (from semantic layer)
- Intervention specification (action, population, constraints)
- Outcome variables of interest
- Budget or feasibility constraints

**Outputs:**
- Estimated intervention effects with confidence intervals
- Ranked action alternatives
- Narrative explanation tied to explicit causal assumptions
- Sensitivity-labeled output (flagged if near assumption violation bounds)

**Constraints:**
- Must only operate on Guardian-approved graph assets
- Must attach the assumption set used to every simulation output
- Counterfactual claims must be labeled with their identification assumptions
- If observational data is unavailable, may return a clearly labeled graph-heuristic effect estimate rather than a silent placeholder or null result

**Tools available:** `counterfactual_reason`, `intervention_estimate`, `scenario_compare`, `explanation_generate`, `semantic_layer_read`

**Current implementation note:** `SimulationAgent.process(state, data=...)` estimates the approved fragment's Treatment -> Outcome effect with the kernel when it is given data (one column per node id), and records the kernel's sensitivity analysis on the fragment's draft edge from that treatment to that outcome, through a Guardian verdict for `RECORD_SENSITIVITY`: a new IGNORABILITY assumption the edge rests on, which is what Guardian PR-007 reads before the edge can be approved (septa-62). No platform flow passes it data yet; `POST /api/semantic/edges/{edge_id}/sensitivity` runs the same analysis for one draft edge on the caller's data.

**Backed by:** `packages/causal-core` (identification and estimation, called through `src/skills/_kernel.py`); the counterfactual skill is a labelled heuristic (`scm-counterfactuals` in the register)

---

#### `DriftMonitorAgent`
**Role:** Continuously monitors causal and semantic drift across the platform. Alerts when learned causal structure diverges from live data patterns or when entity definitions shift.

**Current implementation note:** Production-ready with multi-type drift detection (distribution, causal, semantic, concept), configurable thresholds, comprehensive severity scoring (0-100), and optional Neo4j alert storage with a 90-day TTL when `NEO4J_URI` is set (without it, alerts are not stored; the Neo4j path is tested against mocks only). Alerts are emitted in-process through `AlertEmitter` (`src/sensors/alert_emit.py`: structured log, registered callbacks, deduplication and throttling); there is no message broker.

**Inputs:**
- Live data streams or batch snapshots
- Approved graph assets
- Semantic layer entity definitions
- Configured drift thresholds (kl_threshold, severe_kl_threshold)

**Outputs:**
- Drift alerts with severity level (LOW/MEDIUM/HIGH/CRITICAL)
- Severity score (0-100) with multi-factor analysis
- Identified drift type: distribution, causal, semantic, or concept drift
- Per-feature divergence scores
- Suggested re-validation trigger
- Alerts stored in Neo4j with TTL and acknowledgment tracking, when `NEO4J_URI` is set

**Constraints:**
- Alerts must include the statistical evidence (KL divergence value, chi-squared result, etc.)
- Cannot autonomously modify graph assets in response to drift
- Must log all detections to audit trail
- Alert-only agent (never writes to semantic layer)

**Tools available:** `drift_detect`, `detect_semantic_drift`, `detect_causal_drift`, `detect_concept_drift`, `calculate_drift_severity`, `store_drift_alert`, `get_recent_drift_alerts`, `alert_emit`

**Backed by:** KL-divergence monitoring (histogram and JS methods), optional Neo4j alert storage, in-process `AlertEmitter`; no Bayesian belief tracking is implemented

**Configuration:**
```python
DriftMonitorAgent(
    kl_threshold=0.1,              # Moderate drift threshold
    severe_kl_threshold=0.5,       # Severe drift threshold
    semantic_similarity_threshold=0.7,
    enable_neo4j_storage=True,     # Store alerts in Neo4j (needs NEO4J_URI)
    enable_alert_emission=True,    # Emit through the in-process AlertEmitter
)
```

---

#### `EvidenceGroundingAgent`
**Role:** Links causal claims, graph edges, and assumptions to supporting evidence from documents, metrics, and external knowledge sources.

**Inputs:**
- Candidate edge or assumption statement
- Document corpus (runbooks, post-mortems, research papers, SLO docs)
- Metric time series

**Outputs:**
- Evidence citations with relevance scores
- Contradiction flags when evidence conflicts with proposals
- Evidence gap list (claims without citations)

**Constraints:**
- Must distinguish between statistical evidence and expert knowledge evidence
- Must flag low-evidence edges for human review
- Must not fabricate citations

**Tools available:** `evidence_link`, `document_search`, `metric_correlate`, `citation_validate`

---

### 2.2 Vertical Agents (DevOps Entry Point)

#### `IncidentTriageAgent`
**Role:** First-response agent for operational incidents. Classifies problem complexity via Cynefin, proposes likely causal drivers, retrieves relevant past incidents from the experience buffer, and surfaces actionable interventions.

**Current implementation note:** The harness uses an optional Neo4j experience buffer with TTL (90-day expiry) and relevance decay (30-day half-life) when `NEO4J_URI` is configured (tested against mocks only). Without it, it reads durable session-state snapshots from SQL. Intervention and counterfactual reasoning use graph-grounded heuristics. Observationally calibrated incident estimators remain future work. Cynefin classification is a keyword stub, measured below always guessing the most common domain (register `cynefin-classifier-keyword-stub`): a description with no chaotic, complex or complicated keyword is classified `DISORDER` with confidence 0.0, and triage then runs no analysis and flags the incident for human review.

**Inputs:**
- Incident alert payload (title, affected services, metrics snapshot, alert timestamps)
- Recent deployment and change event log
- Service dependency graph from semantic layer
- Experience buffer (past incidents and their resolutions)

**Outputs:**
- Cynefin domain classification with confidence
- Ranked candidate root-cause list with causal evidence
- Proposed interventions ranked by estimated effect
- Guardian policy verdict on proposed interventions
- Similar past incidents from experience buffer

**Constraints:**
- Chaotic-domain incidents route immediately to human incident commander with minimal AI framing
- Complex-domain incidents surface the causal uncertainty explicitly
- All proposed interventions require human approval before execution

**Tools available:** `cynefin_classify`, `causal_propose_edge`, `experience_buffer_query`, `intervention_estimate`, `guardian_check`, `counterfactual_reason`

**Reference implementation:** `src/agents/incident_triage.py` + `src/orchestration/graph.py` (Cynefin routing node)

---

#### `ChangeImpactAgent`
**Role:** Pre-deployment analysis agent. Given a proposed change, estimates likely causal impact on downstream services, SLOs, and risk metrics.

**Current implementation note:** The harness now resolves approved fragment edges and performs bounded downstream path tracing plus heuristic SLO screening, over the SQL-stored graph. Outcome-calibrated estimators remain future work.

**Inputs:**
- Change request specification
- Service dependency graph
- Historical change → outcome data

**Outputs:**
- Impact estimate with confidence bounds
- Risk tier (LOW / MEDIUM / HIGH / CRITICAL)
- Recommended rollout strategy (canary, blue-green, staged)
- Guardian policy verdict

**Tools available:** `intervention_estimate`, `dependency_graph_read`, `risk_score`, `guardian_check`, `counterfactual_reason`

---

## 3. Shared Skill Library

All agents share a common skill vocabulary. Skills are atomic, testable, composable units of agent capability. No agent implements business logic that duplicates a skill.

### 3.1 Skill Definitions

#### `cynefin_classify`
Classifies a problem into one of five Cynefin domains: `CLEAR`, `COMPLICATED`, `COMPLEX`, `CHAOTIC`, `DISORDER`.

**Inputs:** Problem description, available data features, time sensitivity flag
**Output:** Domain label, confidence score, recommended reasoning strategy
**Reference implementation:** `IncidentTriageAgent._skill_cynefin_classify` in `src/agents/incident_triage.py`, a keyword stub that never returns `CLEAR` and returns `DISORDER` (confidence 0.0) when no keyword matches; the Rust router named here earlier does not exist in this repository

---

#### `causal_propose_edge`
Proposes a directed causal edge between two variables, with evidence and a confidence score.

**Inputs:** Source variable, target variable, dataset statistics, domain context
**Output:** Edge proposal with statistical test results, confidence, and evidence citations
**Reference implementation:** `CausalDiscoveryAgent` in `src/agents/causal_discovery.py`; edges are proposed for Guardian review, not discovered from data (`causal-discovery` in the register)

---

#### `counterfactual_reason`
Applies Pearl's three-level hierarchy: Abduction → Action → Prediction.

**Inputs:** Structural causal model (SCM), observation, query (intervention or counterfactual)
**Output:** Estimated counterfactual distribution, identification status, sensitivity label
**Reference implementation:** `src/skills/counterfactual_reason.py`, a labelled heuristic (`heuristic: true`, `NOT_ASSESSED`); no structural causal model is fitted (`scm-counterfactuals` in the register)

---

#### `guardian_check`
Submits an action, graph change, or recommendation to the Guardian policy engine.

**Inputs:** Action payload, current EpistemicState, applicable policy rules
**Output:** `APPROVED` / `REJECTED` / `REQUIRES_ESCALATION` verdict with reason and rule citations
**Reference implementation:** `GovernanceAgent.check` in `src/agents/governance.py`

---

#### `experience_buffer_query`
Retrieves similar past incidents from the experience buffer with TTL filtering and relevance decay.

**Inputs:** Incident description, domain filter, service filters, result limit
**Output:** Ranked list of similar incidents with similarity scores, decay factors, resolutions
**Storage backends:** 
- Neo4j (optional, when `NEO4J_URI` is set): 90-day TTL, 30-day half-life relevance decay; tested against mocks only
- SQL (default): reads durable session-state snapshots
**Configuration:** Set `NEO4J_URI` to use the Neo4j backend

---

#### `evidence_link`
Links a causal claim to supporting evidence in documents or metrics.

**Inputs:** Claim text or edge specification, document corpus references
**Output:** Ranked evidence list with source, snippet, and relevance score

---

#### `assumption_elicit`
Elicits an explicit causal assumption from a domain expert or from agent-analyzed context.

**Inputs:** Graph fragment, domain context, elicitation mode (`INTERVIEW` or `AUTO`)
**Output:** Structured assumption statement with strength and scope

---

#### `drift_detect`
Computes drift between two distributions or graph states.

**Inputs:** Reference state (approved), current state (live), metric type
**Output:** Drift score, drift type (`CAUSAL` / `DISTRIBUTION` / `SEMANTIC`), severity

---

#### `explanation_generate`
Produces a human-readable causal explanation for a recommendation or estimate.

**Inputs:** Simulation result, assumption set, target persona (EXECUTIVE / ANALYST / OPERATOR / COMPLIANCE)
**Output:** Narrative explanation with explicit assumption disclosures, confidence ranges

---

#### `confounder_screen`
Screens a variable set for likely unmeasured confounders using domain knowledge and statistical patterns.

**Inputs:** Treatment, outcome, covariate list, domain context
**Output:** Flagged potential confounders with evidence basis

---

#### `semantic_layer_read`
Retrieves causal assets (entities, edges, assumptions, graph fragments) from the semantic layer registry.

**Inputs:** Query (entity name, graph ID, assumption scope)
**Output:** Typed semantic layer objects

---

#### `audit_write`
Appends an immutable entry to the platform audit trail.

**Inputs:** Agent identity, action type, payload hash, Guardian verdict, timestamp
**Output:** Audit entry ID (append-only, no modification)

---

#### `effect_estimate`
Computes Average Treatment Effect (ATE) with `packages/causal-core` identification and estimation over an approved graph fragment.

**Inputs:** Approved graph fragment, identification strategy, assumption set, optional observational data
**Output:** ATE with confidence interval, estimator name, treatment and outcome IDs
**Reference implementation:** `src/skills/effect_estimate.py`, delegating to `packages/causal-core`

---

#### `identification_check`
Validates whether a causal effect is identifiable given a graph fragment and assumptions.

**Inputs:** Graph fragment, treatment/outcome specification, available covariates
**Output:** Identification status, valid backdoor/frontdoor/IV strategies, blocking constraints
**Reference implementation:** `src/skills/identification_check.py`, delegating to `packages/causal-core`

---

#### `sensitivity_analysis`
Computes Rosenbaum sensitivity bounds and E-values for unmeasured confounders on an approved edge.

**Inputs:** Approved CausalEdge, effect estimate, confounder assumptions
**Output:** E-value, sensitivity bounds, robustness verdict
**Reference implementation:** `src/skills/sensitivity_analysis.py`

---

#### `causal_structure_compare`
Compares graph structure against live data patterns using conditional independence tests to detect structural drift.

**Inputs:** Graph fragment, live data observations, edge confidence threshold
**Output:** Structural drift flag, affected edge list, violated d-separation statements
**Reference implementation:** `src/skills/causal_structure_compare.py` (scipy, numpy)

---

#### `intervention_estimate`
Wrapper around effect_estimate that validates intervention preconditions (approved fragment, SUTVA, positivity) before delegation.

**Inputs:** Intervention specification, approved graph fragment, assumption set
**Output:** Estimated intervention effect with sensitivity labels
**Reference implementation:** `src/skills/intervention_estimate.py`

---

### 3.2 Phase 2B Extensions (ADR-002)

#### `provenance_trace`
Traces the full provenance chain from an agent output back to source data, model invocation, and reasoning steps. Produces W3C PROV-compliant provenance graphs.

**Inputs:** Session ID, EpistemicState
**Output:** PROV-JSON provenance graph with entities, activities, agents; gap flags for incomplete chains
**Reference implementation:** `src/semantic_layer/provenance.py`, `src/api/provenance.py`

---

#### `verify_data_contract`
Validates a data contract at runtime before agent data consumption, enforcing freshness SLAs, schema compliance, and semantic alignment.

**Inputs:** Data contract ID, current data product state, agent access request
**Output:** Contract verdict (COMPLIANT / VIOLATED / DEGRADED), violation details
**Reference implementation:** `src/semantic_layer/contracts.py`, Guardian PR-009

---

### 3.3 Phase 2C Extensions (ADR-003)

#### `plan_action_sequence`
Applies FLARE-style receding-horizon planning: simulate forward, detect failure points, back-propagate value, commit one action.

**Inputs:** Approved graph fragment, ranked action set, current platform state
**Output:** Committed action with confidence, lookahead simulation trace, failure points
**Reference implementation:** `src/orchestration/planning.py`

---

#### `ontology_negotiate`
Negotiates semantic alignment between two domain ontologies at runtime. Determines whether concepts from different vocabularies are equivalent, mappable, or incompatible.

**Inputs:** Source ontology, target ontology, concept pairs
**Output:** Alignment report (DIRECT_MATCH / MAPPED_MATCH / NO_MATCH)
**Reference implementation:** `src/semantic_layer/federation.py` (future — Phase 5)

---

### 3.4 MCP Skill Exposure

External agents (Claude Desktop, custom MCP clients, IDE plugins, gnomon) reach the platform's causal reasoning through its one Model Context Protocol server. Internal LangGraph workflows still invoke skills directly.

**The server: `packages/causal-mcp`** (console script `causal-mcp`; MCP SDK v2; stdio, or stateless HTTP with `--http`). It serves the `packages/causal-core` kernel: typed structured output whose `outputSchema` is the claim contract, every tool annotated read-only, and the contract's JSON Schemas as resources. Setup and tool reference: [`packages/causal-mcp/README.md`](../packages/causal-mcp/README.md). Tests: `packages/causal-mcp/tests/`.

| MCP tool name | Purpose |
|---|---|
| `causal_check_graph` | Validate a drafted graph; list unreviewed edges and its testable implications |
| `causal_identify` | Identifiable from the graph alone, before data? Returns the certificate or the reason not |
| `causal_analyze` | Estimate, or return a typed refusal with the missing information (a result, not an error) |
| `causal_decide` | Only with the `[decision]` extra: analyse and rule in one call — `act`, `act_with_approval`, `experiment` or `refuse` |
| `causal_impact` | Only with the `[decision]` extra: does a revised graph break an earlier estimate? `holds`, `invalidated` or `unverified` (or `gained` / `still_unidentified`), with the edge-level changes |
| `causal_verify_narrative` | Fail-closed check of prose: every number bound to a claim, causal wording only for `supported` |

**Recommended call order:** `causal_check_graph` → `causal_identify` → `causal_analyze` (or `causal_decide` when the question is whether to act) → `causal_verify_narrative`, and `causal_impact` when the graph behind an earlier answer is revised. A refusal is relayed with its `missing_information`; the verdict is the tool's, not the calling agent's. Governance actions (approving an edge, acting on a result) are deliberately not tools: an agent must never be able to grant itself an approval.

#### History: the legacy backend server

Until September 2026 a second server, `backend/mcp_server` (console script `causal-platform-mcp`, MCP SDK v1), served seven backend skills beside the kernel's server, two of them heuristic triage scores rather than effects. It was deprecated on 2026-09-25 and deleted on 2026-09-26 (register entry `legacy-mcp-server`, now in `left_the_list`); `backend/tests/unit/test_one_mcp_server.py` keeps it deleted. A client configuration that still launches `causal-platform-mcp` should launch `causal-mcp`.

#### Adding a causal capability

Not here, and not in `src/skills/`. A new method goes into the kernel with a typed contract, influence-function standard errors, a pre-registered benchmark and a seeded-defect negative control, and reaches agents through `packages/causal-mcp`. The working recipe is [`.gnomon/skills/adding-an-estimator.md`](../.gnomon/skills/adding-an-estimator.md).

#### Data, wiring and evidence

**Data.** Inline as `{"column": [values]}`, or as `data_path` (CSV, Parquet, JSON). A `data_path` must lie under a directory in `CAUSAL_MCP_DATA_ROOTS` (`os.pathsep`-separated; default: the server's working directory); a path outside is refused as `INVALID_INPUT`.

**Wiring.** `.gnomon/tools.toml` declares `causal_core` (the kernel's server, its only MCP server; a role reaches it by listing `mcp__causal_core`). `.gnomon/system.md` requires every causal number an agent states to come from an `mcp__causal_core__*` call, a test run or a script.

**Evidence.** `packages/causal-mcp/tests/test_stdio_flow.py` runs the recommended flow over the real stdio transport and validates each outcome against the published schema; `test_http.py` runs the tools over stateless HTTP; `test_decision_tool.py` covers `causal_decide` and `causal_impact`; `test_query_parity.py` holds every tool's analysis parameters to the kernel's `CausalQuery`, from which they are derived.

---

## 4. EpistemicState: Shared Agent State Contract

Every agent operates on and returns an `EpistemicState` object. This is the single source of in-flight session truth. No agent stores mutable state outside of `EpistemicState` during a reasoning cycle.

```typescript
interface EpistemicState {
  // Identity
  sessionId: string;
  agentId: string;
  timestamp: ISO8601;

  // Problem classification
  cynefinDomain: "CLEAR" | "COMPLICATED" | "COMPLEX" | "CHAOTIC" | "DISORDER";
  domainConfidence: number;          // 0.0 – 1.0

  // Causal evidence
  activeGraphFragmentId: string;
  proposedEdges: EdgeProposal[];
  activeAssumptions: Assumption[];
  evidenceCitations: Evidence[];

  // Uncertainty
  overallConfidence: number;
  uncertaintySources: UncertaintySource[];
  hallucinationRiskScore: number;    // grounding risk (Guardian PR-002), computed by the platform

  // Governance
  guardianVerdict: "APPROVED" | "REJECTED" | "REQUIRES_ESCALATION" | "PENDING";
  policyRulesCited: string[];

  // Reasoning chain
  reasoningSteps: ReasoningStep[];
  toolCallLog: ToolCall[];

  // Output
  recommendations: Recommendation[];
  simulationResults: SimulationResult[];
  draftExplanation: string;
}
```

---

## 5. Agent Orchestration: LangGraph State Machine

The platform uses **LangGraph StateGraph** for workflow coordination, implemented as a 7-node cognitive mesh.

### 5.1 Core Workflow Graph

```
Intake → CynefinClassify → [Router]
    ├── CLEAR        → DirectAnswer ────┐
    ├── COMPLICATED  → CausalAnalysis ──┤
    ├── COMPLEX      → CausalDiscovery ─┘
    │                                       ↓
    │                                  PlanAhead (Phase 2C)
    │                                  ├── SimulateAhead (SCM lookahead)
    │                                  ├── Backpropagate (failure detection)
    │                                  └── CommitNext (one action only)
    │                                    ╱           ╲
    │                                   │       EscalateHuman
    │                                   ↓            ↓
    │                               GuardianCheck ←──┘
    │                                   ↓
    │                               Output  [requires_human_review on every escalation]
    ├── CHAOTIC      → EscalateHuman → GuardianCheck → Output [Human approval required]
    └── DISORDER     → DisorderProtocol → CynefinClassify [retry once, then EscalateHuman]
```

Every path ends GuardianCheck → Output: nothing a session returns lacks a Guardian verdict, and
every verdict is written to `audit_log` before the response. An escalation is ruled on and
recorded like any other output (`action_type` `GRAPH_HUMAN_ESCALATION`, with the escalation's
reason in the payload the row's hash binds): the Guardian returns REQUIRES_ESCALATION for
CHAOTIC (PR-003) and REJECTED for DISORDER (PR-006), and the response still routes the run to a
person. Until 2026-09-26 EscalateHuman went straight to the end of the graph, so no escalation
left an audit record, and every classified run escalated at PlanAhead: the planner simulates
nothing (register `planning-simulation-no-data`), and its "no safe action found" was a
threshold on the recommendations' own confidences. A plan nothing could simulate now goes to
the Guardian marked `plan_unsimulated`, with that said on each recommendation; only a lookahead
that estimated consequences and found every candidate unsafe escalates. Pinned by
`backend/tests/integration/test_session_reaches_guardian.py`, on SQLite and PostgreSQL.

### 5.2 Graph Node Responsibilities

| Node | Agent | Condition for transition |
|---|---|---|
| `Intake` | — | Always transitions to `CynefinClassify` |
| `CynefinClassify` | `cynefin_classify` skill | Domain confidence > 0.7 |
| `CausalAnalysis` | `CausalDiscoveryAgent` | Graph fragment available |
| `CausalDiscovery` | `CausalDiscoveryAgent` | Complex domain with low evidence |
| `PlanAhead` | `plan_action_sequence` skill | Candidate recommendations exist |
| `GuardianCheck` | `GovernanceAgent` | Every run, before `Output`: after PlanAhead, and after EscalateHuman |
| `EscalateHuman` | — | CHAOTIC domain, DISORDER still unresolved after one retry, or PlanAhead's lookahead estimated every candidate unsafe; then `GuardianCheck` |

`CynefinClassify` classifies the problem as submitted, not the latest narrative an agent wrote:
`/incident` used to re-classify the triage agent's own summary, which carries no keyword, so
every non-chaotic incident became DISORDER.

### 5.3 Circuit Breaker Rules (enforced by `GovernanceAgent`)

- Maximum 5 cycles before mandatory human escalation
- If `hallucinationRiskScore > 0.7`, pause and surface for review
- If Guardian returns `REJECTED` twice for the same payload, escalate
- All circuit breaker triggers are written to audit trail

---

## 6. Context and Memory Architecture

### 6.1 Memory Tiers

| Tier | Store | Contents | Lifetime |
|---|---|---|---|
| **Working memory** | In-process `EpistemicState` | Current reasoning cycle state | Session |
| **Session memory** | PostgreSQL / Cloud SQL + in-process hot cache | Latest `EpistemicState` snapshot per session, resume context, API continuity | Session persisted across process restarts; richer per-user preference memory still pending |
| **Graph memory** | PostgreSQL / SQLite (`graph_fragments`, `causal_edges`) | Approved causal graph assets, edges, entities, assumptions | Platform lifetime |
| **Experience buffer** | Durable session snapshots in SQL; Neo4j optionally, when `NEO4J_URI` is set | Past incident patterns, resolutions, outcome feedback | SQL snapshots have no soft-expiry; the Neo4j backend applies a 90-day TTL |
| **Audit memory** | Append-only PostgreSQL | All agent actions and Guardian verdicts | Immutable, regulatory lifetime |
| **Provenance memory** | Append-only PostgreSQL (`provenance_entries`, `provenance_erasures`) | W3C PROV records of session runs, Guardian checks and decisions (ADR-002); written by the session and decision routes and by the Guardian beside its audit row, not by the other agents | Kept; an erasure is a tombstone that hides a session's records from the API, never a deletion |
| **Semantic registry** | PostgreSQL / SQLite | Causal semantic layer objects (entities, treatments, outcomes) | Governed lifecycle |

### 6.2 Memory Access Patterns

```
Read:   SemanticLayer → GraphMemory → ExperienceBuffer → SessionMemory
Write:  WorkingMemory → [GuardianCheck] → SemanticLayer / GraphMemory / AuditLog
```

Agents **never write directly** to long-term stores. All writes are mediated by `audit_write` and, for semantic layer and graph memory, require a Guardian `APPROVED` verdict first.

**Current boundary note:** Session-state durability is now implemented as governed `EpistemicState` snapshot persistence for orchestration continuity. The incident harness also reads those durable snapshots as an interim experience buffer. This is not a full preference/profile memory layer, and it does not bypass Guardian-gated semantic writes.

### 6.3 Prompt Architecture (PR)

Prompt recipes follow a strict four-part structure:

```
[CONTEXT]     Relevant semantic layer objects, graph fragment, persona
[SKILL]       The specific skill being invoked and its parameters
[CONSTRAINTS] Policy rules, Guardian guardrails, assumption scope
[OUTPUT_FORMAT] Typed schema the agent must return
```

Prompt templates are versioned in `prompts/` alongside their schema contracts. No agent uses ad-hoc prompts at runtime.

---

## 7. Multi-Provider LLM Routing

Agents specify **capability requirements**, not model names — the LLM factory resolves capability → provider at runtime.

| Capability | Ollama | Anthropic | OpenAI | DeepSeek |
|---|---|---|---|---|
| `causal_reasoning` | `qwen3.5:122b` | `claude-sonnet-4-20250514` | `gpt-4o` | `deepseek-chat` |
| `fast_classification` | `phi4:14b` | `claude-haiku-4-5-20250413` | `gpt-4o-mini` | `deepseek-chat` |
| `document_extraction` | `nemotron-super:120b` | `claude-sonnet-4-20250514` | `gpt-4o` | `deepseek-chat` |
| `code_generation` | `deepseek-r1:70b` | `claude-sonnet-4-20250514` | `gpt-4o` | `deepseek-reasoner` |
| `planning` | declared, no model mapped (resolves to `unknown`) | same | same | same |

Routing is handled by `src/agents/llm_factory.py`. Provider selection is never hardcoded in skill implementations. The provider is the first one configured, in the order Ollama (`OLLAMA_BASE_URL`), Anthropic (`ANTHROPIC_API_KEY`), OpenAI (`OPENAI_API_KEY`), DeepSeek (`DEEPSEEK_API_KEY`; each key may instead be a file named by `<NAME>_FILE`, `src/secret_source.py`); if building its client fails, the others are tried in the same order. The capability then picks the model within that provider, as in the table; each default can be overridden by environment variable (`OLLAMA_CAUSAL_MODEL`, `ANTHROPIC_MODEL`, `OPENAI_FAST_MODEL`, ...). Nothing calls `get_llm` today (register `llm-routing-no-callers`).

---

## 8. Agent Development Checklist

Before any new agent or skill is merged into the platform, it must satisfy:

- [ ] EpistemicState in/out contract defined and typed
- [ ] Guardian integration tested (APPROVED / REJECTED / ESCALATION paths all exercised)
- [ ] Cynefin domain handling documented (which domains the agent supports)
- [ ] Audit trail write verified in tests
- [ ] Narratives it emits are submitted to Guardian with the kernel outcome they report (PR-002 grounding)
- [ ] Prompt template versioned in `prompts/`
- [ ] Skill used is in the shared skill library (no duplicates)
- [ ] Circuit breaker behavior tested
- [ ] Memory access follows read/write tiers (no direct long-term store writes)
- [ ] Provider-agnostic (no hardcoded model names)

---

## 9. References

### 9.1 Internal modules

| Component | Source |
|---|---|
| Cynefin routing | `src/orchestration/graph.py` (Router node) |
| Causal DAG operations | `src/semantic_layer/models.py`, `src/skills/causal_structure_compare.py` |
| Guardian policy engine | `src/agents/governance.py` |
| LangGraph orchestration | `src/orchestration/graph.py` |
| EpistemicState schema | `src/semantic_layer/models.py` |
| Grounding-risk gating (PR-002) | `src/agents/grounding_risk.py` |
| LLM capability routing | `src/agents/llm_factory.py` |
| MCP skill exposure | `packages/causal-mcp/`, the one MCP server (see §3.4) |

### 9.2 External libraries

| Capability | Library |
|---|---|
| Causal effect estimation | `packages/causal-core` is the engine (numpy/scipy/pandas/networkx). DoWhy and y0 appear only in its optional `crosscheck` extra, as independent identification engines its tests compare against. EconML and PyMC are not dependencies; a new estimator (EconML's among them) would enter through causal-core's `causal_core.estimators` entry-point registry |
| Causal discovery | Roadmap: causal-learn (no discovery is implemented; `causal-discovery` in the register) |
| Bayesian belief tracking | Not implemented; no library is used |
| Policy as code | OPA (planned), TLA+ (`specs/guardian_policy.tla`) |
| Workflow orchestration | LangGraph |

### 9.3 Historical inspiration

The platform's overall shape — Guardian-mediated semantic writes, Cynefin-routed agent workflows, EpistemicState as the single in-flight state object — draws on prior research prototypes (`carfdevops`, `causalrust`/`cynepic-rs`, `projectcarfcynepic`). Those repos are **not consolidated into this codebase** and are not tracked here; the canonical implementation of every pattern above lives in `backend/src/`.

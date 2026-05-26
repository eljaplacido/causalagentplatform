# Platform Current Stage

> **Last updated:** 2026-05-24
> **Active phase:** Phase 1 effectively complete; opening Phase 2 (DAG Workbench)
> **Tests:** 551/551 backend passing • Frontend Playwright e2e suite (22 specs)

---

## 1. Strategic Position

The **Causal Platform** is a standalone enterprise system for governed causal reasoning, decision simulation, and intervention recommendation. It combines:

- A canonical **semantic layer** (causal entities, edges, assumptions, fragments) with Guardian-mediated approval.
- A **shared skill library** implementing the platform's causal-inference primitives (effect estimation, identification check, sensitivity analysis, drift detection, counterfactual reasoning, structural comparison, intervention estimation).
- An **agent fleet** (Governance, Drift Monitor, Incident Triage, Change Impact, Domain Expert Elicitation, Evidence Grounding, Simulation, Causal Discovery) operating on a single `EpistemicState` contract.
- An **MCP skill surface** (`backend/mcp_server/`) exposing the seven causal skills to external agents over stdio.
- A **React frontend** rendering governance review, incidents, interventions, and provenance views against a typed semantic-layer API.

> **Historical note:** Earlier drafts of this document framed the platform as a consolidation of three prior prototypes (`carfdevops`, `causalrust`/`cynepic-rs`, `projectcarfcynepic`). Those repos were **inspiration only** — they are not being merged into this codebase and are not tracked here. Every architectural pattern referenced in the docs lives in `backend/src/` of this repo.

---

## 2. Phase Completion Status

### Phase 1: Foundation — Semantic Layer + Agentic Skills + Guardian

**Status: Complete** (551/551 backend tests green; one frontend type-coverage line item outstanding)

| Deliverable | Status | Where it lives |
|---|---|---|
| Canonical causal semantic object model | ✅ | `src/semantic_layer/models.py`, ADR-001 |
| Guardian approval workflow (DRAFT → UNDER_REVIEW → APPROVED → DEPRECATED) | ✅ | `src/agents/governance.py` |
| Append-only audit trail | ✅ | `src/api/provenance.py`, `src/semantic_layer/provenance.py` |
| Evidence grounding + lineage capture | ✅ | `src/agents/evidence_grounding.py` |
| Drift monitor with KL/semantic/causal/concept detection | ✅ | `src/agents/drift_monitor.py`, `src/skills/drift_detect.py` |
| Continuous drift sensor + alert emitter | ✅ | `src/sensors/periodic_drift.py`, `src/sensors/alert_emit.py` |
| Causal skill library (7 skills, DoWhy-backed) | ✅ | `src/skills/*.py` |
| **MCP skill surface (external agentic-skills API)** | ✅ | `backend/mcp_server/` — console script `causal-mcp` |
| Frontend fail-closed UX (no silent mock fallback) | ✅ | `frontend/src/lib/api.ts` + page handlers |
| Health endpoint with full monitoring-stack visibility | ✅ | `src/api/health.py` |
| Incident persistence + intervention approve/reject (idempotent) | ✅ | `src/api/incidents.py`, `src/api/interventions.py` |
| Type unification: canonical → frontend | 🔄 In progress | governance + incidents pages done; remaining pages pending |
| Prompt template library | ✅ | `prompts/` |

**Exit criteria — Phase 1:**
- [x] Teams can create reusable causal assets
- [x] Every approved graph object has lineage, ownership, review metadata
- [x] Drift alerts carry statistical evidence and severity scoring
- [x] Frontend shows explicit degraded state on backend unavailability
- [x] Governance policy PR-008 blocks drift-triggered semantic layer writes
- [x] Causal skills callable by external agents over MCP
- [ ] Full type coverage across **all** frontend pages

---

### Phase 2: DAG Workbench — **NEXT PRIORITY**

**Status: ~30%** (skill primitives + agent suggestions exist; unified authoring UI not built)

| Deliverable | Status | Notes |
|---|---|---|
| Graph authoring UI (unified) | 🔄 Components exist, no unified surface | Highest-leverage open item |
| Agent edge + confounder suggestions | ✅ | `src/agents/causal_discovery.py` |
| Document-grounded evidence linking | 🔄 Partial | `src/agents/evidence_grounding.py` |
| Human approval + versioning workflow | ✅ | Guardian + audit trail |
| Reusable graph fragments by domain | 🔴 Not started | Phase 2 sprint deliverable |
| Single asset-promotion workflow (DRAFT → APPROVED → reusable) | 🔄 In progress | UI surface missing |

**Exit criteria — Phase 2:**
- [ ] Unified graph-authoring UI surface
- [ ] Non-technical SME can propose + approve a fragment in <30 min (first session)
- [ ] ≥3 approved fragments available per domain at launch
- [ ] Agent edge suggestions hit ≥70% accepted rate in persona tests

---

### Phase 3: Decision Simulation Cockpit

**Status: ~20%** (simulation engine exists, cockpit UI not built)

| Deliverable | Status | Notes |
|---|---|---|
| Intervention comparison UI | 🔄 In design | Personas defined in `docs/UIX.md`, component spec TBD |
| Outcome simulation with constraints | ✅ | `src/agents/simulation.py`, DoWhy/EconML |
| Recommendation summaries with assumptions | 🔄 Partial | `prompts/explanation_generate.txt` |
| Audit-ready output + review logs | ✅ | Guardian + append-only audit trail |

---

### Phase 4: First Vertical Agent (DevOps)

**Status: ~25%** (one connector implemented; ontology + outcome-feedback loop not started)

| Deliverable | Status | Notes |
|---|---|---|
| Domain connectors | 🔄 Prometheus only | `src/connectors/prometheus.py` |
| Domain ontology templates (incident / change / alert) | 🔴 Not started | |
| Workflow playbooks (Cynefin-routed incident triage) | ✅ | `src/agents/incident_triage.py`, `src/orchestration/graph.py` |
| Approval gates and feedback loops | ✅ | Guardian layer |
| Outcome feedback → causal graph update | 🔴 Not started | Requires fragment-reuse infrastructure from Phase 2 |

---

### Phase 5: Multi-vertical Expansion

Not started. Gated on Phase 4 GA.

---

## 3. Active Work Streams (Q2 2026)

| Stream | Priority | Notes |
|---|---|---|
| **DAG Workbench unified UI** | **P0** | Phase 2 exit blocker; gates Phase 3 + Phase 4 |
| Frontend type coverage (remaining pages) | P0 | Closes last Phase 1 line item |
| Decision Simulation cockpit spec | P1 | Phase 3 entry condition |
| Domain ontology templates (DevOps) | P1 | Phase 4 entry condition |
| MCP skill surface expansion (provenance, planning) | P2 | Surface more of `src/skills/` as MCP tools |
| Enterprise IdP (SAML 2.0 / OIDC) | P2 | Beyond Firebase Auth |
| Outcome-feedback → graph update loop | P3 | Phase 4 mid-stream |

---

## 4. Known Technical Debt and Risks

### Critical (must resolve before enterprise GA)

| Issue | Impact | Mitigation path |
|---|---|---|
| No unified frontend shell — pages are page-scoped components | Users see disconnected views | Phase 2 DAG Workbench UI + Phase 3 cockpit |
| Frontend type coverage incomplete across all pages | Type drift between backend canonical schema and frontend | Finish `packages/semantic-types` adoption |
| Domain ontology templates missing | Phase 4 vertical agent blocked on schema | Author DevOps incident/change ontology fragments |

### Moderate

| Issue | Impact | Mitigation path |
|---|---|---|
| Firebase auth only | Enterprise IdP requirements not met | Add SAML/OIDC provider support |
| Session memory richer than latest durable snapshot not implemented | Resume works across restarts, but preferences/history shallow | Extend session store into governed user-memory tier without bypassing Guardian |
| MCP surface advertises 7 tools; the broader skill library has more (planning, provenance, contracts) | External agents can't reach planning / contract-verification | Add wrappers in `mcp_server/tools.py` + register in `server.py` |
| Upstream warnings: `pyparsing`, `pandas4`, `datetime.utcnow()` deprecation in `test_simulation.py` | Test-run noise; no functional impact | Filter in `pyproject.toml` or pin compatible versions; replace `utcnow()` with timezone-aware `now(UTC)` |

---

## 5. Architectural Decisions Log

| Decision | Rationale | Date | Status |
|---|---|---|---|
| DevOps as first vertical | Bounded ontology, intervention-rich workflow, frequent outcome signals | 2025-Q4 | Committed |
| LangGraph for workflow orchestration | Conditional edges, cycle detection, human-in-the-loop nodes | 2025-Q4 | Committed |
| Neo4j for causal graph storage | Native graph traversal for d-separation, backdoor path queries | 2025-Q4 | Committed |
| Guardian before all semantic writes | Enterprise trust requirement; aligns with EU AI Act governance | 2026-Q1 | Committed |
| `EpistemicState` as session state contract | Single state object prevents context fragmentation across multi-step cycles | 2026-Q1 | Committed |
| Minimalist-first UI with progressive disclosure | Reduces cognitive load; complexity added only on validated persona need | 2026-Q1 | Committed |
| **MCP server as the agentic-skills surface** | Standard protocol; external agents (Claude Desktop, IDE plugins, custom clients) reach skills without re-implementing them | 2026-05-23 | Committed |

---

## 6. Dependency Sequence (Phase Gates)

```
[P1] Foundation (semantic layer + skills + Guardian + MCP surface) ✅ DONE
  → [P2] DAG Workbench UI               ← next priority
      → [P3] Decision Simulation Cockpit
          → [P4] DevOps Vertical Agent (production)
              → [P5] Second Vertical (Marketing / Supply Chain)
```

No phase gate can be bypassed. Platform coherence depends on each layer being stable before the next builds on it.

---

## 6b. "Blocked By" Matrix

| Work stream | Blocked by |
|---|---|
| DAG Workbench UI | Nothing — ready to start (Phase 1 complete) |
| Phase 3 cockpit | DAG Workbench must produce reusable fragments first |
| Phase 4 DevOps vertical | DAG Workbench (for domain ontology templates); cockpit (for intervention surface) |
| Outcome-feedback → graph update loop | Phase 2 reusable-fragments infrastructure must land |
| Enterprise IdP swap | Frontend auth integration refactor |

---

## 7. Success Metrics by Phase

### Phase 1 (Done)
- Semantic layer objects: ≥5 entity types, ≥3 treatment types, ≥4 confounder types ✅
- Every approved edge has owner, lineage, timestamp, assumption set ✅
- 100% of semantic writes pass through Guardian verdict ✅
- All 7 causal skills callable via MCP with JSON-native I/O and structured error envelope ✅

### Phase 2 (Next)
- DAG Workbench: non-technical SME proposes + approves a graph fragment in <30 min (first session)
- Agent edge suggestions: ≥70% accepted rate in usability tests
- ≥3 approved reusable fragments per domain at launch

### Phase 3
- Decision Simulation: analyst compares ≥2 interventions in <5 min
- ≥80% of explanations rated "causally intelligible" in user testing
- 100% of simulation outputs linkable to an approved graph asset + assumption set

### Phase 4 (DevOps GA)
- Time to root-cause identification: ≥30% reduction vs. baseline
- Incident resolution quality: ≥20% improvement (operator-rated)
- Measurable reduction in unnecessary rollbacks over 90-day period

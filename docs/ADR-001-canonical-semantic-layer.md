# ADR: Canonical Semantic Layer Specification

> **Status:** Accepted
> **Date:** 2026-04-07 (revised 2026-05-24)
> **Decision Owner:** Platform Architecture
> **Read against the code:** 2026-09-25. The decision stands; the sections below now say where
> the code does not yet hold it, with the register id from
> [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json).

## Summary

Define a single canonical causal semantic object model for the platform that serves as the contract for all agents, tests, MCP-exposed skills, and UI components.

## Context

Early design drafts of this platform drew on prior research prototypes that each had their own divergent semantic schemas (Neo4j-backed `EpistemicState`, Rust crate models, full-stack frontend prototypes). Those prototypes are **historical inspiration only** — not codebases this repo consolidates or tracks.

Without a single canonical model in *this* repo, we would risk re-introducing the same divergence between:
- The backend skill implementations (`src/skills/`)
- The MCP tool wrappers (`backend/mcp_server/tools.py`)
- The frontend type bindings (`packages/semantic-types`)
- The Guardian policy contracts

## Decision

### 1. Canonical Object Model (from DATA_LAYER.md §2.1)

Every surface in this repository (§5) MUST implement these core types with exact field alignment.
The authoritative definition is `backend/src/semantic_layer/models.py`:

```
CausalEntity     → id, name, domain, attributes, ontologyVersion,
                    createdBy, approvedAt, approvedBy, lineage

Treatment        → id, name, entityRef, treatmentType, units,
                    feasibilityConstraints, costModel, ownerTeam

Outcome          → id, name, entityRef, measurementType, timeHorizon,
                    businessValue, sloRef

Confounder       → id, name, treatmentRef, outcomeRef, measuredStatus,
                    proxyVariables, evidenceStrength

CausalEdge       → id, source, target, edgeType, direction, confidence,
                    evidenceRefs, assumptionRefs, approvalStatus,
                    approvedBy, approvedAt, guardianVerdictRef, version, lineage

GraphFragment    → id, name, domain, description, edges, entryNodes,
                    exitNodes, approvalStatus, version, usageCount, tags

Assumption       → id, statement, assumptionType, scope, justification,
                    elicitedFrom, elicitedBy, reviewStatus, sensitivityResult

Intervention     → id, name, treatmentRef, targetEntityRef, initiatedBy,
                    initiatedAt, approvedBy, approvalTimestamp,
                    guardianVerdictRef, expectedEffect, actualEffect,
                    outcomeObservedAt, feedbackLoopClosed

Evidence         → id, evidenceType, sourceRef, snippet, statisticalResult,
                    relevanceScore, createdBy, createdAt
```

### 2. Lifecycle Semantics

`ApprovalStatus` has four members. This is what moves an object between them today
(`backend/src/semantic_layer/registry.py`):

| Status | Meaning | Transitions |
|---|---|---|
| `DRAFT` | Stored by `propose_draft`, which forces this status whatever the submitter sent | → `APPROVED` (`promote_to_approved`), → `DEPRECATED` (`reject_draft`) |
| `UNDER_REVIEW` | Declared, and accepted by the review queue and by `reject_draft`, but no code path ever sets it | → `APPROVED`, → `DEPRECATED` |
| `APPROVED` | Human-approved against a signed Guardian verdict; usable in simulations | → `DEPRECATED` (intended; no code path deprecates an approved object yet) |
| `DEPRECATED` | Superseded or rejected, retained for audit | (terminal) |

There is no `REJECTED` status. A rejection is stored as `DEPRECATED` with a `REJECTED` lineage
step, and the reason is kept in `audit_log.notes`.

**Rules:**
- Only `APPROVED` objects participate in simulations (AGENTS.md §2.1 SimulationAgent)
- All writes require Guardian `APPROVED` verdict (DATA_LAYER.md §2.2 Registry Rule #1)
- All objects are versioned, with no in-place mutation (Rule #2). **Not held:** approval and
  rejection update the row in place and never change `version` (register
  `semantic-objects-mutated-in-place`)
- All `APPROVED` objects carry lineage (Rule #3). **Partial:** one lineage step per human
  decision, nothing from source data (register `lineage-partial`)

### 3. Guardian Requirements

From AGENTS.md §1 and §2:

- **Every agent output passes through Guardian** before persistence
- **Guardian verdict types:** `APPROVED` | `REJECTED` | `REQUIRES_ESCALATION`, plus `PENDING`
  (nobody has ruled yet; what `audit_write` records)
- **Escalation routes to human reviewer, never to another agent**
- **All verdicts are immutable once written**

Guardian integration points, as built:
- The session graph (`backend/src/orchestration/graph.py`) runs a `guardian_check` node on every
  CLEAR / COMPLICATED / COMPLEX path, after `plan_ahead`. CHAOTIC, and a plan with no safe
  action, go to human escalation without it.
- Only `GovernanceAgent` and `IncidentTriageAgent` call the `guardian_check` skill themselves.
  `BaseAgent._assert_guardian_approved`, the write guard meant for every agent, has no callers.
  The persistence guard that does hold is in the registry: every semantic-layer write needs a
  signed, subject-bound, single-use APPROVED verdict (`_assert_verdict_approved`).
- An audit trail entry is written on every verdict.
- The circuit breaker escalates at `cycle_count >= 5` (PR-005; `MAX_CYCLES` in `agents/base.py`).

### 4. Versioning Rules

> **Status:** not implemented. See register `semantic-objects-mutated-in-place`.

- Semantic objects use `version: string` (semver-style: `1.0.0`, `1.1.0`, `2.0.0`). Today only
  `CausalEdge` and `GraphFragment` have the field (`CausalEntity` has `ontology_version`), and
  it stays at its `1.0.0` default.
- Version increments on any field change. Target: no code path increments it.
- Deprecated versions retained in full, never deleted. Holds, in the sense that nothing deletes
  a row; but there is only one row per object, so earlier versions are not retained.
- Lineage tracks: creator, approver, evidence chain, Guardian verdict ref. Partial (register
  `lineage-partial`).

### 5. Internal Alignment Targets

The canonical model must be the single source of truth across these surfaces in *this* repository:

| Surface | Alignment requirement |
|---|---|
| `backend/src/semantic_layer/models.py` | Pydantic models match the canonical schema field-for-field |
| `backend/src/skills/*` | Every skill accepts/returns the canonical shape. Today skills take and return plain `dict`s, not the models |
| `backend/mcp_server/tools.py` | MCP wrappers accept JSON-native projections of the canonical shape |
| `packages/semantic-types` (frontend) | TypeScript interfaces aligned to the canonical schema. They are hand-written, not generated, and the fields differ from `models.py` (register `semantic-types-field-divergence`). The shared enumerations are checked by `scripts/contract_parity.py` |
| Guardian policies | Operate on canonical field names; do not encode legacy aliases |

### 6. Verification

Success criteria:
- [ ] All backend skills use identical `EpistemicState` contract. Skills take and return `dict`;
  the agents hold the `EpistemicState`
- [ ] Guardian integration consistent across agents. Two agents call `guardian_check`, and the
  base-class write guard has no callers (§3)
- [ ] Lifecycle status transitions match canonical model. `UNDER_REVIEW` is never set, and
  rejection goes straight to `DEPRECATED` (§2)
- [ ] Version field present on all semantic objects. Present on `CausalEdge` and `GraphFragment`
  only, and never incremented (§4)
- [ ] TypeScript bindings aligned with `models.py`. Enumerations: `scripts/contract_parity.py`
  (two known divergences, register `frontend-vocabulary-divergence`). Fields: register
  `semantic-types-field-divergence`

## Consequences

### Positive
- Single source of truth for causal knowledge
- Agent development can proceed in parallel across repos
- UI can consume canonical objects without translation layers
- Policy enforcement consistent via Guardian

### Negative
- Migration effort required for each repo
- Potential performance implications from schema changes
- Need for coordinated release across repos

## References

- docs/DATA_LAYER.md §2.1 (canonical object model)
- docs/AGENTS.md §4 (EpistemicState contract)
- docs/CURRENT_STAGE.md (what is implemented, with tests; everything else by register id)
- scripts/contract_parity.py (the shared enumerations across backend, package and frontend)
# ADR: Canonical Semantic Layer Specification

> **Status:** Accepted
> **Date:** 2026-04-07 (revised 2026-05-24)
> **Decision Owner:** Platform Architecture

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

All repos MUST implement these core types with exact field alignment:

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

| Status | Meaning | Transitions |
|---|---|---|
| `DRAFT` | Created by agent, not yet reviewed | → `UNDER_REVIEW` |
| `UNDER_REVIEW` | Submitted to Guardian | → `APPROVED`, `REJECTED` |
| `APPROVED` | Guardian-verified, usable in simulations | → `DEPRECATED` |
| `DEPRECATED` | Superseded, retained for audit | (terminal) |

**Rules:**
- Only `APPROVED` objects participate in simulations (AGENTS.md §2.1 SimulationAgent)
- All writes require Guardian `APPROVED` verdict (DATA_LAYER.md §2.2 Registry Rule #1)
- All objects are versioned — no in-place mutation (Rule #2)
- All `APPROVED` objects carry lineage (Rule #3)

### 3. Guardian Requirements

From AGENTS.md §1 and §2:

- **Every agent output passes through Guardian** before persistence
- **Guardian verdict types:** `APPROVED` | `REJECTED` | `REQUIRES_ESCALATION`
- **Escalation routes to human reviewer, never to another agent**
- **All verdicts are immutable once written**

Guardian integration points:
- `guardian_check` skill in every agent
- Audit trail entry written on every verdict
- Circuit breaker triggers escalation after 5 cycles (AGENTS.md §5.3)

### 4. Versioning Rules

- Semantic objects use `version: string` (semver-style: `1.0.0`, `1.1.0`, `2.0.0`)
- Version increments on any field change
- Deprecated versions retained in full — never deleted
- Lineage tracks: creator, approver, evidence chain, Guardian verdict ref

### 5. Internal Alignment Targets

The canonical model must be the single source of truth across these surfaces in *this* repository:

| Surface | Alignment requirement |
|---|---|
| `backend/src/semantic_layer/models.py` | Pydantic models match the canonical schema field-for-field |
| `backend/src/skills/*` | Every skill accepts/returns the canonical shape |
| `backend/mcp_server/tools.py` | MCP wrappers accept JSON-native projections of the canonical shape |
| `packages/semantic-types` (frontend) | TypeScript interfaces generated from / aligned to the canonical schema |
| Guardian policies | Operate on canonical field names; do not encode legacy aliases |

### 6. Verification

Success criteria:
- [x] All backend skills use identical `EpistemicState` contract
- [x] Guardian integration consistent across agents
- [x] Lifecycle status transitions match canonical model (`DRAFT → UNDER_REVIEW → APPROVED → DEPRECATED`)
- [x] Version field present on all semantic objects
- [ ] Frontend type bindings fully aligned across all pages (in progress — governance + incidents done)

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
- docs/CURRENT_STAGE.md §2 (phase status)
- docs/CURRENT_STAGE.md §4 (technical debt item: "Three codebases have divergent semantic schemas")
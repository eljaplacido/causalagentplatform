# ADR: Canonical Semantic Layer Specification

> **Status:** Accepted
> **Date:** 2026-04-07 (revised 2026-05-24)
> **Decision Owner:** Platform Architecture
> **Read against the code:** 2026-09-26 (versioning and lineage, septa-14/15). The decision
> stands; the sections below say where the code does not yet hold it, with the register id from
> [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json).

## Summary

Define a single canonical causal semantic object model for the platform that serves as the contract for all agents, tests, MCP-exposed skills, and UI components.

## Context

Early design drafts of this platform drew on prior research prototypes that each had their own divergent semantic schemas (Neo4j-backed `EpistemicState`, Rust crate models, full-stack frontend prototypes). Those prototypes are **historical inspiration only** — not codebases this repo consolidates or tracks.

Without a single canonical model in *this* repo, we would risk re-introducing the same divergence between:
- The backend skill implementations (`src/skills/`)
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
                    approvedBy, approvedAt, guardianVerdictRef, version, lineage,
                    createdBy

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
step, and the reason is kept in `audit_log.notes`. A review only moves forward: approving or
rejecting an object that is already `APPROVED` or `DEPRECATED` raises `InvalidTransitionError`,
which the governance API returns as 409.

**Rules:**
- Only `APPROVED` objects participate in simulations (AGENTS.md §2.1 SimulationAgent)
- All writes require Guardian `APPROVED` verdict (DATA_LAYER.md §2.2 Registry Rule #1)
- All objects are versioned — no in-place mutation (Rule #2). Held for `CausalEntity`,
  `CausalEdge`, `GraphFragment` and `Assumption`: every state change -- approval, rejection, and
  recording a sensitivity result on a draft edge (its `assumptionRefs` and lineage) -- is a new,
  immutable version and the prior one is kept exactly (§4). Not held for interventions, whose
  outcome feedback still updates the row (register `interventions-mutated-in-place`)
- All `APPROVED` objects carry lineage (Rule #3). Held from source data to approver: approval
  needs the object's source data and drafter on record, and the chain is served by
  `GET /api/semantic/objects/{type}/{id}/lineage` (DATA_LAYER.md §6). The link onward, to the
  analyses and decisions that used an object, is not recorded (register `lineage-to-analyses`)

### 3. Guardian Requirements

From AGENTS.md §1 and §2:

- **Every agent output passes through Guardian** before persistence
- **Guardian verdict types:** `APPROVED` | `REJECTED` | `REQUIRES_ESCALATION`, plus `PENDING`
  (nobody has ruled yet; what `audit_write` records)
- **Escalation routes to human reviewer, never to another agent**
- **All verdicts are immutable once written**

Guardian integration points, as built:
- The session graph (`backend/src/orchestration/graph.py`) ends every path in a `guardian_check`
  node: after `plan_ahead` on CLEAR / COMPLICATED / COMPLEX, and after human escalation (CHAOTIC,
  unresolved DISORDER, a lookahead that found no safe action), which is ruled on and written to
  `audit_log` like any other output. Until 2026-09-26 escalations skipped it, and every
  classified run escalated at `plan_ahead`, so no session run reached it
  (`backend/tests/integration/test_session_reaches_guardian.py`).
- Only `GovernanceAgent` and `IncidentTriageAgent` call the `guardian_check` skill themselves;
  SimulationAgent calls `GovernanceAgent.check` directly, for a `RECORD_SENSITIVITY` verdict
  before recording the kernel's sensitivity analysis on the draft edge it estimated.
  `BaseAgent._assert_guardian_approved`, the write guard meant for every agent, has no callers.
  The persistence guard that does hold is in the registry: every semantic-layer write needs a
  signed, subject-bound, single-use APPROVED verdict, and a review decision one issued for
  exactly that action, `GOVERNANCE_APPROVE` or `GOVERNANCE_REJECT` (`_assert_verdict_approved`).
- An audit trail entry is written on every verdict.
- The circuit breaker escalates at `cycle_count >= 5` (PR-005; `MAX_CYCLES` in `agents/base.py`).

### 4. Versioning Rules

> **Status:** implemented for `CausalEntity`, `CausalEdge`, `GraphFragment` and `Assumption`
> (`014_semantic_object_versions.sql`, `backend/src/semantic_layer/registry.py`; pinned by
> `backend/tests/unit/test_registry_versioning.py` on SQLite and PostgreSQL). Not for
> interventions (register `interventions-mutated-in-place`). Causal *models* are versioned by a
> separate mechanism, the assumption registry (`causal_decision.assumptions`,
> `backend/src/semantic_layer/model_store.py`, `/api/v1/models`): immutable, content-addressed
> versions, each built on its parent, append-only in the PostgreSQL schema
> (`007_causal_models.sql`).

- **Every state is a version.** A row of `semantic_object_versions`, keyed
  `(object_type, object_id, revision)`, holds the object's complete stored row at that revision
  (`state`), what produced it (`PROPOSED` for the draft, `APPROVED`, `REJECTED`, or `RECORDED` for
  a state found from before versioning), who produced it (the person, the agent it came through
  and the platform version) and the verdict that authorised it.
- **Reads return the current version.** The row in the object's own table is the head: the
  latest version, whose `revision` it carries. The object endpoints read it.
- **History is queryable.** `GET /api/semantic/objects/{type}/{id}/versions` (registry
  `get_versions`; `get_object_version` returns the object as it was at a revision), bounded by
  domain scope like every read, and for a viewer only when the object is approved.
- **Nothing overwrites a version.** A state change records the next revision and advances the
  head by exactly one (`_record_change`, the registry's only head write), guarded on the
  revision it read. Version rows are append-only: triggers refuse UPDATE and DELETE on both
  engines, and `causal_app` may only SELECT and INSERT them. The database refuses a head UPDATE
  that does not advance `revision` by one over a revision already recorded, and on PostgreSQL
  one whose new revision is not recorded by commit. Existing rows were migrated forward as
  `RECORDED` revision 1.
- **Deprecate and replace.** A rejection's version is `DEPRECATED`; nothing deletes an object or a
  version. No path edits an object's content after its draft: a changed object is a new draft.
- **`revision` counts states; `version` is the author's.** The semver `version` on edges and
  fragments is the content version an author declares, and part of `graph_fragments`' unique key;
  a state change does not touch it. "Version increments on any field change" holds as: every
  recorded change increments `revision`.
- **The head is a projection.** It is updated, under the rules above, rather than replaced by a
  new row, so an object keeps one id and the queries, joins and row-level-security policies over
  the head tables are unchanged. What the principle protects -- no state is lost or rewritten --
  lives in the version rows.
- **Lineage** is assembled from the versions and `audit_log`: see §2 rule 3 and DATA_LAYER.md §6.

### 5. Internal Alignment Targets

The canonical model must be the single source of truth across these surfaces in *this* repository:

| Surface | Alignment requirement |
|---|---|
| `backend/src/semantic_layer/models.py` | Pydantic models match the canonical schema field-for-field |
| `backend/src/skills/*` | Every skill accepts/returns the canonical shape. Today skills take and return plain `dict`s, not the models |
| `packages/semantic-types` (frontend) | TypeScript interfaces aligned to the canonical schema. They are hand-written, not generated, and the fields differ from `models.py` (register `semantic-types-field-divergence`). The shared enumerations are checked by `scripts/contract_parity.py` |
| Guardian policies | Operate on canonical field names; do not encode legacy aliases |

This table listed a fifth surface, the MCP wrappers in `backend/mcp_server/tools.py`, until that server was deleted (2026-09-26). The MCP server that remains, `packages/causal-mcp`, speaks the causal-core claim contract (`conformance/causal-claims/`), not this semantic layer.

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
# ADR-002: Agentic Provenance Layer & Runtime Data Contracts

> **Status:** Proposed
> **Date:** 2026-05-05
> **Decision Owner:** Platform Architecture
> **Trigger:** [Missing Standards for Agentic Data Engineering report](research/missing-standards-agentic-data-engineering.md) (§2 Provenance, §4 Data Contracts)
> **Replaces:** N/A — new capability
> **Read against the code:** 2026-09-26. Provenance is durable and append-only, and session
> runs, Guardian checks and decisions write it. No LLM call is made, so no invocation is captured,
> and no running flow feeds the data contracts or PR-009. See *Implementation Status*, and
> registers `llm-routing-no-callers` and `data-contracts-not-enforced` in
> [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json).

## Summary

Extend the platform's audit trail and semantic layer with (a) W3C PROV-AGENT-aligned
provenance records capturing the full chain from source data → model invocation → tool
use → reasoning steps → final artefact, and (b) runtime data contracts that enforce
freshness SLAs, schema compliance, and semantic alignment before agent data consumption.

## Context

Two critical gaps identified from cross-referencing the 2026 standards landscape:

1. **Agentic Provenance Gap**: The platform's audit_log captures agent_id, action_type,
   payload_hash, and verdict — but does not capture LLM invocation metadata (model version,
   prompt hash, temperature), tool call provenance, or W3C PROV-compliant entity-activity-agent
   graphs. The EU AI Act's coming enforcement (August 2026) requires reproducibility from
   model output back to source data. The PROV-AGENT academic proposal (arXiv 2508.02866)
   provides a concrete extension pattern.

2. **Data Contract Gap**: The semantic layer defines typed schemas for CausalEntity,
   CausalEdge, etc., but has no runtime enforcement mechanism for data freshness or
   semantic alignment. An agent consuming a stale data product produces "confident outputs
   based on wrong state" — the #1 production failure mode in 2025-2026 agentic deployments.

## Decision

### Part A: Provenance Layer

1. **Module `src/semantic_layer/provenance.py`** implements:
   - `ProvenanceRecord` Pydantic model (PROV-AGENT-aligned)
   - `LLMInvocation` model for capturing every LLM call
   - `ProvenanceGraph` aggregated view for PROV-JSON export

2. **Module `src/api/provenance.py`** implements:
   - `GET /api/v1/provenance/trace/{session_id}` — full provenance trace
   - `GET /api/v1/provenance/export/{session_id}` — W3C PROV-JSON export
   - `GET /api/v1/provenance/invocations/{session_id}` — LLM invocation listing
   - `POST /api/v1/provenance/session/{session_id}/erasure` — hide a session's records behind a
     tombstone (a reason is required)
   - All four provenance routes need a governance role (`governance_officer` or
     `platform_engineer`; the role matrix, CYBERSECURITY.md §4.0)
   - Provenance store: `src/semantic_layer/provenance_store.py` over tables
     `provenance_entries` and `provenance_erasures` (migration `011_provenance.sql`, and the
     SQLite schema). Both are append-only: no role is granted UPDATE or DELETE, and a trigger
     refuses both. An erasure is a row saying which session, who, when and why; the reads stop
     returning that session's earlier records and return the tombstone instead. The records
     stay in the database, so an erasure is not GDPR Art. 17 erasure (register `gdpr-controls`)
   - Who writes (`src/semantic_layer/provenance_capture.py`): session runs and incident sessions
     (the agent, the run, each reasoning step it added, input and output as digests); every
     Guardian check (`GovernanceAgent.check`, an activity citing its `audit_log` row, in the same
     transaction); and `POST /api/v1/decisions` (data, graph, analysis and ledger entry, under
     session `decision:<entry_digest>`)

3. **Module `src/agents/llm_factory.py`** extended with:
   - `_ProvenanceCapturingLLM` wrapper that auto-captures LLMInvocation on every call
   - Optional `session_id` and `agent_id` params on `get_llm()` enable provenance tracking
   - Best-effort capture (never blocks on provenance failure)
   - **Status:** the wrapper writes to the durable store, but `get_llm` has no callers, so
     nothing is captured (register `llm-routing-no-callers`)

4. **Prompt `prompts/provenance_trace.yaml`** for the `provenance_trace` skill. The file exists,
   and no code loads it

### Part B: Data Contracts

1. **Module `src/semantic_layer/contracts.py`** implements:
   - `DataContract`, `FreshnessSLA`, `SemanticBinding`, `SchemaConstraint`,
     `QualityThreshold`, `ContractViolation` Pydantic models
   - `ContractRegistry` in-memory store with product-indexed lookup. It registers, looks up
     and removes contracts. It does not evaluate one, and only the tests instantiate it

2. **Guardian policy PR-009** ("Data contract enforcement") added to `src/agents/governance.py`:
   - CRITICAL/HIGH violations block agent action (REJECTED)
   - MEDIUM/LOW violations allow action with warning (APPROVED with PR-009 cited)
   - **Status:** the rule fires only for `action_type == "DATA_ACCESS_WITH_CONTRACT"`, which no
     code path sends, and it reads the violations from the caller's own payload. Nothing
     measures freshness or schema compliance, and nothing produces a `ContractViolation`, so no
     contract is enforced at runtime (register `data-contracts-not-enforced`)

## Alternatives Considered

1. **Build provenance as standalone service**: Rejected — provenance records need the
   same Guardian governance, audit trail immutability, and EpistemicState context that
   the existing platform provides. A standalone service would duplicate governance logic.

2. **Adopt OpenLineage for provenance**: Rejected — OpenLineage has no model for LLM
   invocations or agent reasoning chains. PROV-AGENT extends W3C PROV specifically for
   agentic workflows.

3. **Defer data contracts to Phase 4**: Rejected — contract enforcement is a Phase 2B
   concern because agents already consume semantic layer data in the DAG Workbench.

## Guardian Policy Alignment

| Policy | Status | Notes |
|--------|--------|-------|
The Status column was PASS on every row when the ADR was written. As read against the code:

| Policy | Status | Notes |
|--------|--------|-------|
| EpistemicState contract preserved | PASS | Provenance records are separate from state; they do not mutate it |
| Guardian-gated writes enforced | NOT MET | Nothing writes contract violations, and PR-009 never sees a real one |
| Audit trail integrity maintained | PASS | Provenance is append-only (`011_provenance.sql`); an erasure is a kept tombstone. It is a separate table from `audit_log`; a Guardian check's provenance record cites its `audit_log` row and is written in the same transaction |
| Provider-agnostic implementation | PASS, unexercised | The capture wrapper is provider-agnostic; no call goes through it |
| PII handling compliant | NOT ASSESSED | Nothing pseudonymises identifiers (see CYBERSECURITY.md §5) |

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Provenance graph size explosion | MEDIUM | Not built. The mitigation first proposed, TTL-based expiry and pruning, would delete rows the store keeps append-only; retention has to be designed against that rule |
| Contract enforcement latency | LOW | Contract checks are O(1) in-memory lookups; Redis cache for production |
| PROV-AGENT spec divergence | MEDIUM | Track upstream changes; abstract via internal model layer |
| Agent blocking on stale data | MEDIUM | DEGRADED mode allows read with warning; BLOCK mode only for PR-009 critical paths |

## Dependencies

- `src/agents/llm_factory.py`: the LLMInvocation capture wrapper exists; `get_llm` has no callers
- `src/api/main.py` registers the provenance router (implemented)
- `src/agents/governance.py`: the PR-009 rule exists; no caller sends the action type it checks
- Neo4j: future work. Provenance is stored in PostgreSQL (SQLite locally), not in a graph store

## Implementation Status (read against the code 2026-09-26)

Test counts and coverage are not written down here, because they would go stale. The suite and
its coverage are reported by `.gnomon/ci.sh` on every run.

| Component | Status | Tests |
|---|---|---|
| `src/semantic_layer/provenance.py` | Implemented: the PROV models | `tests/unit/test_provenance.py` |
| `src/semantic_layer/contracts.py` | Models and a lookup registry. No contract evaluation; register `data-contracts-not-enforced` | `tests/unit/test_contracts.py` |
| `src/api/provenance.py`, `src/semantic_layer/provenance_store.py` | Implemented: durable, append-only, erasure by tombstone | `tests/integration/test_provenance_append_only.py` |
| Provenance written by running flows | Implemented for session runs, Guardian checks and decisions | `tests/integration/test_provenance_flow.py` |
| `src/agents/llm_factory.py` capture hook | Writes to the durable store; unreachable at runtime, since `get_llm` has no callers (register `llm-routing-no-callers`) | `tests/integration/test_provenance_flow.py` (with a stub model) |
| `src/agents/governance.py` PR-009 | Rule written; no caller sends `DATA_ACCESS_WITH_CONTRACT` | -- |
| `prompts/provenance_trace.yaml` | Created; not loaded by any code | -- |

## Consequences

### Positive (intended; see Implementation Status for what holds)
- An LLM call made through `get_llm(session_id=..., agent_id=...)` would capture invocation
  metadata. No call is made
- Provenance traces are queryable via API endpoints, for session runs, Guardian checks and
  decisions
- Data contracts would enforce freshness SLAs at runtime. Nothing evaluates a contract today
- Guardian PR-009 would gate agent actions on contract compliance, given a violation list from
  something other than the caller

### Negative
- Provenance grows without bound: append-only rows, and an erasure adds a row rather than
  freeing one. No retention or pruning exists (see *Risk Assessment*)
- LLM invocation capture adds a small overhead per call (best-effort, non-blocking)
- Data contract enforcement requires manual contract registration (no UI yet)

## Review

- **Author:** DeepSeek-V4 via OpenCode
- **Reviewer:** Platform Architecture
- **Date:** 2026-05-05
- **Lane:** plan → implement

# ADR-002: Agentic Provenance Layer & Runtime Data Contracts

> **Status:** Proposed
> **Date:** 2026-05-05
> **Decision Owner:** Platform Architecture
> **Trigger:** Missing Standards for Agentic Data Engineering report (§2 Provenance, §4 Data Contracts)
> **Replaces:** N/A — new capability

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
   - In-memory provenance store (SQLite-backed for local dev)

3. **Module `src/agents/llm_factory.py`** extended with:
   - `_ProvenanceCapturingLLM` wrapper that auto-captures LLMInvocation on every call
   - Optional `session_id` and `agent_id` params on `get_llm()` enable provenance tracking
   - Best-effort capture (never blocks on provenance failure)

4. **Prompt `prompts/provenance_trace.yaml`** for the `provenance_trace` skill

### Part B: Data Contracts

1. **Module `src/semantic_layer/contracts.py`** implements:
   - `DataContract`, `FreshnessSLA`, `SemanticBinding`, `SchemaConstraint`,
     `QualityThreshold`, `ContractViolation` Pydantic models
   - `ContractRegistry` in-memory store with product-indexed lookup

2. **Guardian policy PR-009** ("Data contract enforcement") added to `src/agents/governance.py`:
   - CRITICAL/HIGH violations block agent action (REJECTED)
   - MEDIUM/LOW violations allow action with warning (APPROVED with PR-009 cited)

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
| EpistemicState contract preserved | PASS | Provenance records are append-only, not state mutations |
| Guardian-gated writes enforced | PASS | Contract violations write via audit_write; PR-009 enforces at runtime |
| Audit trail integrity maintained | PASS | Both provenance and contract records are append-only |
| Provider-agnostic implementation | PASS | LLM invocation capture works for any provider via llm_factory.py |
| PII handling compliant | PASS | Provenance records reference pseudonymised IDs, never raw PII |

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Provenance graph size explosion | MEDIUM | TTL-based expiry matching audit_log retention; periodic pruning |
| Contract enforcement latency | LOW | Contract checks are O(1) in-memory lookups; Redis cache for production |
| PROV-AGENT spec divergence | MEDIUM | Track upstream changes; abstract via internal model layer |
| Agent blocking on stale data | MEDIUM | DEGRADED mode allows read with warning; BLOCK mode only for PR-009 critical paths |

## Dependencies

- `src/agents/llm_factory.py` emits LLMInvocation metadata (implemented)
- `src/api/main.py` registers provenance router (implemented)
- `src/agents/governance.py` enforces PR-009 (implemented)
- Neo4j: future work — production provenance graph storage (currently in-memory)

## Implementation Status (2026-05-05)

| Component | Status | Test Coverage |
|---|---|---|
| `src/semantic_layer/provenance.py` | Implemented | 100% (78 lines) |
| `src/semantic_layer/contracts.py` | Implemented | 99% (100 lines) |
| `src/api/provenance.py` | Implemented | 83% (89 lines) |
| `src/agents/llm_factory.py` capture hook | Implemented | Existing tests pass |
| `src/agents/governance.py` PR-009 | Implemented | Existing tests pass |
| `prompts/provenance_trace.yaml` | Created | N/A |
| `tests/unit/test_provenance.py` | 25 tests | All passing |
| `tests/unit/test_contracts.py` | 6 tests | All passing |

## Consequences

### Positive
- Every LLM call via `get_llm(session_id=..., agent_id=...)` auto-captures invocation metadata
- Provenance traces are queryable via API endpoints for regulatory audit
- Data contracts enforce freshness SLAs at runtime, not just schema validation
- Guardian PR-009 gates agent actions on contract compliance

### Negative
- In-memory provenance store is session-scoped; production needs Neo4j/pg persistence
- LLM invocation capture adds a small overhead per call (best-effort, non-blocking)
- Data contract enforcement requires manual contract registration (no UI yet)

## Review

- **Author:** DeepSeek-V4 via OpenCode (gx10 satellite)
- **Reviewer:** Platform Architecture
- **Date:** 2026-05-05
- **Lane:** plan → implement

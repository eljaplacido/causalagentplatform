# Copilot Instructions for Causal Platform

This repository is the Unified Enterprise Causal Platform — an enterprise causal operating system combining a semantic layer, human-in-the-loop graph construction, causal validation, decision simulation, and workflow-native vertical agents.

## Architecture

- **Backend:** Python 3.12, FastAPI, LangGraph, SQLAlchemy async, Neo4j, Pydantic v2
- **Frontend:** Next.js 15, React 19, Tailwind CSS, Radix UI, TanStack Query, Zustand
- **Shared types:** `packages/semantic-types/` (TypeScript)
- **Infrastructure:** PostgreSQL, Neo4j, Kafka, Redis (Docker Compose for local dev)

## Critical Contracts

### EpistemicState

Every agent receives and returns an `EpistemicState` object (defined in `backend/src/semantic_layer/models.py`). This is the single in-flight session truth. Key rules:
- `reasoning_steps` and `tool_call_log` are append-only
- `flags` are append-only — never remove flags set by earlier nodes
- `cycle_count` only incremented via `increment_cycle()`

### Guardian-Gated Writes

No code writes directly to the semantic layer. Every write requires:
1. A Guardian `APPROVED` verdict
2. A valid `AuditEntry` reference
3. An `audit_write` call creating an immutable log entry

### Cynefin Routing

The LangGraph graph in `backend/src/orchestration/graph.py` routes by Cynefin domain:
- CLEAR → direct answer
- COMPLICATED → causal analysis
- COMPLEX → causal discovery
- CHAOTIC → human escalation (no automated action)
- DISORDER → re-classify

### Agent Development

All agents extend `BaseAgent` from `backend/src/agents/base.py`:
- Call `_check_circuit_breaker(state)` at entry (max 5 cycles)
- Call `_check_hallucination_risk(state)` (flag if score > 0.7)
- Use `_call_skill()` for all skill invocations (telemetry)
- Use `_add_reasoning_step()` for decision logging
- Never hardcode model names — agents are provider-agnostic

### Semantic Layer

Types in `packages/semantic-types/` must stay in sync with `backend/src/semantic_layer/models.py`. The canonical spec is `docs/DATA_LAYER.md`.

## Key Documentation

- `docs/AGENTS.md` — agent registry, skills, orchestration, EpistemicState
- `docs/DATA_LAYER.md` — semantic objects, storage, integrations
- `docs/CYBERSECURITY.md` — security, governance, compliance
- `docs/UIX.md` — persona views, UX contracts
- `docs/CURRENT_STAGE.md` — delivery phase and priorities
- `docs/ORCHESTRATION.md` — work lanes, tool roles, handoff conventions

## Testing

- Backend: `cd backend && python -m pytest tests/ -q`
- Frontend types: `cd frontend && npm run typecheck`
- Frontend tests: `cd frontend && npm run test`
- Coverage target: 80%

## Do Not

- Bypass Guardian checks on semantic layer writes
- Catch `CircuitBreakerError` or `GuardianBlockedError` inside agents
- Hardcode model names (use capability routing)
- Add UPDATE/DELETE operations on `audit_log`
- Skip `_call_skill()` telemetry wrapper for skill invocations

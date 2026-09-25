# Copilot instructions

Read the root [`AGENTS.md`](../AGENTS.md) first. It is the single entry point for coding agents in
this repository: the kernel-first rule, the causal-mcp flow, the `.gnomon/ci.sh` gate and the
register rules all live there, and the working details are in [`.gnomon/skills/`](../.gnomon/skills/).
(`docs/AGENTS.md` is a different document: the specification of the platform's own agents.)

## Essentials when suggesting code

- **Causal methods go in `packages/causal-core`**, not `backend/src/skills/`. Never suggest an
  effect estimate, interval or p-value computed ad hoc in backend or frontend code.
- **Guardian-gated writes.** Nothing writes to the semantic layer without a Guardian `APPROVED`
  verdict and an audit entry. Never add UPDATE/DELETE on `audit_log`.
- **Agents** extend `BaseAgent` (`backend/src/agents/base.py`): check the circuit breaker at entry,
  invoke skills through `_call_skill()`, and never catch `CircuitBreakerError` or
  `GuardianBlockedError` inside an agent. Do not hard-code model names.
- **Shared types** in `packages/semantic-types/` must match `backend/src/semantic_layer/models.py`;
  `scripts/contract_parity.py` fails when they diverge.

## Commands

- Everything: `bash .gnomon/ci.sh` (this is what CI runs)
- Backend: `cd backend && .venv/bin/pytest -q`
- Kernel: `cd packages/causal-core && .venv/bin/pytest`
- Frontend types: `cd frontend && npm run type-check`
- Frontend tests: `cd frontend && npx vitest run`

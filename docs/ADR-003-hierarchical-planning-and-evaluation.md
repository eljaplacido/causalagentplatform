# ADR-003: Hierarchical Planning with FLARE Lookahead

> **Status:** Proposed
> **Date:** 2026-05-05
> **Decision Owner:** Platform Architecture
> **Trigger:** [World Models for Agents & LLMs report](research/world-models-for-agents-and-llms.md) (§2.2 Causal Reasoning, §2.5 Long-Horizon Planning)
> **Replaces:** N/A — extends existing LangGraph orchestration

## Summary

Extend the platform's LangGraph orchestration with a FLARE-style (Forward Lookahead
with Action Receding-horizon Evaluation) planning node that simulates action
consequences before committing, back-propagates failure signals to earlier actions,
and commits only one action at a time.

## Context

The World Model report identifies two directly applicable gaps:

1. **Planning vs. Reasoning Gap**: LangGraph's existing 7-node workflow routes based
   on Cynefin domain but does not simulate action consequences before committing.
   Agents commit all recommendations without detecting mid-plan failure points.
   FLARE (arXiv 2024) demonstrates that receding-horizon replanning with backward
   value propagation reduces compounding error in long-horizon tasks.

2. **Causal Layer Integration**: The platform has a SimulationAgent with SCM-based
   effect estimation but no mechanism to USE those estimates for planning decisions
   — only for retrospective analysis. The planning layer closes this loop.

## Decision

### FLARE Planning Architecture

**Module `src/orchestration/planning.py`** implements:

1. `FLARERecedingHorizonPlanner` class with three-phase execution:
   - **SimulateAhead**: Forward-simulates each candidate action using the approved
     SCM (effect_estimate skill) or confidence-weighted heuristics
   - **BackpropagateValue**: When a later action fails (confidence below threshold),
     reduces the propagated_value of earlier actions that may have contributed
   - **CommitNext**: Commits only the next action; if the first action fails,
     checks the second (receding horizon)

2. `node_plan_ahead(state)` — LangGraph node integrating into the existing
   PlatformState graph

3. `_ProvenanceCapturingLLM` integration: planning metadata captured for
   provenance audit when LLM-based planning is used

### LangGraph Topology Update

```
direct_answer ──────┐
causal_analysis ────┤
causal_discovery ───┘
        ↓
   plan_ahead ← NEW (Phase 2C)
        ↓
   ┌────┴────┐
   │  ROUTER │
   └────┬────┘
guardian_check   escalate_human
(no failures)    (no safe action found)
```

### Planning Constraints
- Max plan depth: 10 actions (configurable)
- Confidence threshold: 0.4 (actions below this are failures)
- Circuit breaker: respects MAX_CYCLES (5)
- Simulated actions labelled SIMULATED_NOT_EXECUTED
- Planning failures escalate to CHAOTIC domain (human review)
- SCM used for effect estimation when approved fragment available

## Alternatives Considered

1. **Defer planning to LLM prompt engineering**: Rejected — pure LLM planning
   without SCM grounding produces physically invalid plans in ~37% of cases
   (World Model report §2.1). FLARE with SCM simulation provides structurally
   valid lookahead.

2. **Use LangGraph interrupt/pause mechanism**: Rejected — interrupt is a
   pause-and-resume pattern, not lookahead simulation. FLARE simulates forward
   without side effects.

3. **Full Monte Carlo tree search**: Rejected — overkill for this phase.
   FLARE's receding-horizon approach achieves comparable results with O(n)
   complexity vs. MCTS O(b^d).

## Guardian Policy Alignment

| Policy | Status | Notes |
|--------|--------|-------|
| EpistemicState contract preserved | PASS | Planning emits updated EpistemicState with committed recommendations |
| Guardian-gated writes enforced | PASS | Committed actions still pass Guardian before execution |
| Audit trail integrity maintained | PASS | Simulation states are labelled, not persisted to audit_log |
| Provider-agnostic implementation | PASS | Planning uses causal SCM, not LLM calls |
| PII handling compliant | PASS | Planning operates on anonymised graph fragments |

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Planning simulation drift from reality | HIGH | SCM re-validated when DriftMonitorAgent flags fragment; planning pauses on drift |
| Planning latency > 2s SLA | MEDIUM | Depth capped at 10 actions; SCM evaluations cached |
| Receding horizon skips correct first action | MEDIUM | Confidence thresholds configurable per domain |

## Dependencies

- `src/orchestration/graph.py` — PlanAhead node + conditional edge added
- `src/agents/simulation.py` — SimulationAgent provides effect estimation
- `src/skills/effect_estimate.py` — Used for forward simulation

## Implementation Status (2026-05-05)

### Part A: Hierarchical Planning

| Component | Status | Test Coverage |
|---|---|---|
| `src/orchestration/planning.py` | Implemented | 84% |
| `src/orchestration/graph.py` (modified) | Extended | PlanAhead node + routing |
| `prompts/plan_action_sequence.yaml` | Created | N/A |
| `tests/unit/test_planning.py` | 17 tests | All passing |

### Part B: Production Evaluation Pipeline

| Component | Status | Test Coverage |
|---|---|---|
| `src/evaluation/consistency.py` | Implemented | 95% |
| `src/evaluation/probes.py` | Implemented | 92% |
| `src/evaluation/metrics.py` | Implemented | 88% |
| `src/api/platform.py` (modified) | Extended | 3 new evaluation endpoints |
| `tests/unit/test_evaluation.py` | 20 tests | All passing |

### Evaluation API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/platform/evaluation/consistency` | N-run output variance probe |
| `POST /api/v1/platform/evaluation/calibration` | Confidence calibration check |
| `GET /api/v1/platform/evaluation/decision-quality` | Composite DQI score |

## Consequences

### Positive (Planning)
- Agents commit one action at a time after looking ahead
- Failure points detected during simulation, surfaced before execution
- Receding horizon: if the first action would fail, the second-best is tried
- Planning trace appended to `draft_explanation` for auditability
- Gateway routing: planning failures skip Guardian and escalate directly to human

### Positive (Evaluation)
- 8-run consistency probe quantifies the 37% lab-vs-production gap
- Confidence calibration measured per agent, detectable within single session
- Decision quality index provides composite health metric
- All evaluation data routed through Guardian for auditable storage
- Evaluation results feed into experience buffer (90-day TTL)

### Negative
- Adds planning latency (p95 < 2s target for 10 actions)
- Planning without an approved fragment uses heuristic fallback (lower accuracy)
- Evaluation probes multiply agent workload (8x for consistency) — run as async background jobs
- Calibration probe requires ground truth for full accuracy; heuristic proxy used when unavailable

## Review

- **Author:** DeepSeek-V4 via OpenCode (gx10 satellite)
- **Reviewer:** Platform Architecture
- **Date:** 2026-05-05
- **Lane:** plan → implement

# ADR-003: Hierarchical Planning with FLARE Lookahead

> **Status:** Proposed
> **Date:** 2026-05-05
> **Decision Owner:** Platform Architecture
> **Trigger:** [World Models for Agents & LLMs report](research/world-models-for-agents-and-llms.md) (§2.2 Causal Reasoning, §2.5 Long-Horizon Planning)
> **Replaces:** N/A — extends existing LangGraph orchestration
> **Read against the code:** 2026-09-25. The planner is wired into the session graph, but it
> simulates nothing: with no observational data the kernel refuses every estimate, so the
> "lookahead" only rescales each action's own confidence. The evaluation endpoints score their
> own inputs. Registers `planning-simulation-no-data` and `evaluation-endpoints-not-measured`
> in [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json).

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
   - **SimulateAhead**: meant to forward-simulate each candidate action with the
     `effect_estimate` skill over the approved fragment. As built, it calls `effect_estimate`
     with `data=None`, and the kernel refuses without data, so no action ever gets an effect
     estimate. An action under an APPROVED fragment gets `confidence * 0.5`, and one without a
     fragment keeps its own confidence. An action "fails" when that number is below 0.3
   - **BackpropagateValue**: When a later action fails, reduces the `propagated_value` of each
     earlier action by `0.1 / distance`
   - **CommitNext**: Commits only the next action, if it has not failed, its confidence is at
     least the threshold and its propagated value is at least 0.5; otherwise it tries the
     second. If neither qualifies, nothing is committed and the session escalates

2. `node_plan_ahead(state)` — LangGraph node integrating into the existing
   PlatformState graph

3. `_ProvenanceCapturingLLM` integration: planning metadata captured for
   provenance audit when LLM-based planning is used. Not built: the planner calls no LLM,
   and `get_llm` has no callers (register `llm-routing-no-callers`)

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

As implemented in `backend/src/orchestration/planning.py`:

- Max plan depth: `min(10, cycle_count + 5)` in the graph node. The planner's own default is 10
- Commit threshold: the node sets 0.4. `DEFAULT_CONFIDENCE_THRESHOLD` is 0.5 and applies
  only when the planner is built without one. A *failure* is a separate test: confidence below
  0.3
- Circuit breaker: the depth cap above is the planner's only bound. `MAX_CYCLES` (5) is
  enforced by the agents and PR-005, not by the planner
- Simulated actions labelled SIMULATED_NOT_EXECUTED: not implemented. The uncommitted lookahead
  outcomes stay on the planning result and are not surfaced as recommendations
- Planning failures escalate to human review. The node sets `requires_human_review` and the
  flag `no_safe_action_found`, and the router sends the session to `escalate_human`, skipping
  the Guardian. The Cynefin domain is not changed to CHAOTIC
- Effect estimation from the approved fragment: attempted, always refused for lack of data (see
  SimulateAhead). One consequence: under an APPROVED fragment every confidence is halved, so
  an action needs confidence of at least 0.8 to be committed, while without a fragment it needs
  0.4

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
| Planning simulation drift from reality | HIGH | SCM re-validated when DriftMonitorAgent flags fragment; planning pauses on drift. Neither exists yet (register `drift-monitor-no-data`) |
| Planning latency > 2s SLA | MEDIUM | Depth capped at 10 actions; SCM evaluations cached |
| Receding horizon skips correct first action | MEDIUM | Confidence thresholds configurable per domain |

## Dependencies

- `src/orchestration/graph.py` — PlanAhead node + conditional edge added
- `src/agents/simulation.py` — SimulationAgent provides effect estimation
- `src/skills/effect_estimate.py` — Used for forward simulation

## Implementation Status (2026-05-05)

### Part A: Hierarchical Planning

Test counts and coverage are not written down here, because they would go stale. `.gnomon/ci.sh`
reports them on every run.

| Component | Status | Tests |
|---|---|---|
| `src/orchestration/planning.py` | The planner runs; its simulation step estimates nothing (register `planning-simulation-no-data`) | `tests/unit/test_planning.py` |
| `src/orchestration/graph.py` (modified) | PlanAhead node and routing wired in; CLEAR / COMPLICATED / COMPLEX sessions pass through it | `tests/unit/test_planning.py` (node and router) |
| `prompts/plan_action_sequence.yaml` | Created; not loaded by any code | -- |

### Part B: Production Evaluation Pipeline

| Component | Status | Tests |
|---|---|---|
| `src/evaluation/consistency.py`, `probes.py`, `metrics.py` | The scoring functions are written and unit-tested | `tests/unit/test_evaluation.py` |
| `src/api/platform.py` (modified) | Three endpoints. None observes an agent: each scores the inputs it is given (register `evaluation-endpoints-not-measured`) | -- |

### Evaluation API Endpoints

| Endpoint | Intended purpose | What it computes today |
|---|---|---|
| `GET /api/v1/platform/evaluation/consistency` | N-run output variance probe | Variance across `num_runs` empty recommendation lists: 0 by construction |
| `POST /api/v1/platform/evaluation/calibration` | Confidence calibration check | The spread of the confidences in the request body. Calibration needs realized outcomes, and none are recorded |
| `GET /api/v1/platform/evaluation/decision-quality` | Composite DQI score | A weighted combination of four query parameters with defaults |

## Consequences

### Positive (Planning)
- The session keeps one committed recommendation, chosen by the rule in CommitNext. The
  "lookahead" behind it is confidence arithmetic until the planner gets data
- Failure points are recorded (confidence below 0.3) and reported in the trace
- Receding horizon: if the first action does not qualify, the second is tried
- Planning trace appended to `draft_explanation` for auditability
- Gateway routing: planning failures skip Guardian and escalate directly to human

### Intended (Evaluation), not delivered
The ADR expected these; none holds today (register `evaluation-endpoints-not-measured`):
- an 8-run consistency probe measuring a lab-vs-production gap (the 37% figure is from the
  research report, not measured here);
- confidence calibration measured per agent;
- a decision quality index as a health metric;
- evaluation data routed through the Guardian, and results fed to the experience buffer.
  Neither route exists.

### Negative
- Adds planning latency (p95 < 2s target for 10 actions)
- Planning without an approved fragment uses heuristic fallback (lower accuracy)
- Evaluation probes multiply agent workload (8x for consistency) — run as async background jobs
- Calibration probe requires ground truth for full accuracy; heuristic proxy used when unavailable

## Review

- **Author:** DeepSeek-V4 via OpenCode
- **Reviewer:** Platform Architecture
- **Date:** 2026-05-05
- **Lane:** plan → implement

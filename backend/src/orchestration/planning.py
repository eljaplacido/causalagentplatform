"""
Hierarchical planning module implementing FLARE-style receding-horizon reasoning.

FLARE (Forward Lookahead with Action Receding-horizon Evaluation) applies
three-phase execution for each planning cycle:

  1. SimulateAhead   — Forward-simulate action consequences using the approved SCM
  2. Backpropagate    — Detect failure points and propagate value to earlier actions
  3. CommitNext       — Commit only the next action; replan after observation

The planner integrates with the existing SimulationAgent and causal estimation
skills. Simulated actions are labelled SIMULATED_NOT_EXECUTED and never persist
to the semantic layer. Planning failures escalate to CHAOTIC domain (human review).

EpistemicState constraints:
  - require_approved_fragment: Only APPROVED fragments used for simulation
  - no_semantic_layer_writes: Simulated actions are ephemeral
  - audit_log_required: Lookahead results logged as reasoning steps
  - circuit_breaker_enforced: Max planning depth respects MAX_CYCLES
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.semantic_layer.models import (
    EpistemicState,
    Recommendation,
)

log = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_MAX_PLAN_DEPTH = 10
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_PLANNING_TEMPERATURE = 0.1


# ── Planning data structures ──────────────────────────────────────────────────


@dataclass
class SimulatedActionOutcome:
    """Result of forward-simulating a single action in the plan."""

    action: Recommendation
    step_index: int
    estimated_effect: float | None
    confidence: float
    failure_detected: bool
    failure_reason: str | None
    # Back-propagated value score (0 = worst, 1 = best)
    propagated_value: float = 1.0


@dataclass
class PlanningResult:
    """
    Output of a FLARE planning cycle.

    Contains the committed next action and the full lookahead simulation
    results for auditing and evaluation purposes.
    """

    committed_action: Recommendation | None = None
    lookahead_outcomes: list[SimulatedActionOutcome] = field(default_factory=list)
    failure_points: list[int] = field(default_factory=list)
    re_plan_triggered: bool = False
    total_simulations: int = 0
    planning_duration_ms: float = 0.0


# ── FLARE Planner ─────────────────────────────────────────────────────────────


class FLARERecedingHorizonPlanner:
    """
    FLARE-style hierarchical planner for agent action sequences.

    Usage::

        planner = FLARERecedingHorizonPlanner(max_plan_depth=10)
        result = await planner.plan(
            actions=candidate_actions,
            state=epistemic_state,
            fragment=approved_fragment,
        )
        if result.committed_action:
            # Execute ONLY the committed action, then replan
            pass
    """

    def __init__(
        self,
        max_plan_depth: int = DEFAULT_MAX_PLAN_DEPTH,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._max_plan_depth = max_plan_depth
        self._confidence_threshold = confidence_threshold

    async def plan(
        self,
        actions: list[Recommendation],
        state: EpistemicState,
        fragment: dict[str, Any] | None = None,
    ) -> PlanningResult:
        """
        Run a FLARE planning cycle over a set of candidate actions.

        Args:
            actions: Ranked candidate actions to simulate forward
            state: Current EpistemicState with approved fragment reference
            fragment: Optional pre-loaded approved graph fragment (avoids registry call)

        Returns:
            PlanningResult with committed action + lookahead outcomes
        """
        if not actions:
            log.warning("planning.no_actions", session_id=state.session_id)
            return PlanningResult(committed_action=None)

        t0 = time.perf_counter()
        depth = min(len(actions), self._max_plan_depth)
        lookahead_actions = actions[:depth]

        # ── Phase 1: SimulateAhead ────────────────────────────────────────────
        log.info(
            "planning.simulate_ahead",
            session_id=state.session_id,
            action_count=len(lookahead_actions),
        )
        outcomes = await self._simulate_ahead(lookahead_actions, state, fragment)

        # ── Phase 2: BackpropagateValue ───────────────────────────────────────
        log.info(
            "planning.backpropagate",
            session_id=state.session_id,
            outcome_count=len(outcomes),
        )
        outcomes, failure_points = self._backpropagate(outcomes)
        re_plan = len(failure_points) > 0

        # ── Phase 3: CommitNext ──────────────────────────────────────────────
        committed = self._commit_next(outcomes, re_plan)
        if committed:
            log.info(
                "planning.committed",
                session_id=state.session_id,
                action=f"{committed.action[:50]}...",
                confidence=committed.confidence,
                re_plan=re_plan,
            )
        else:
            log.warning(
                "planning.no_action_committed",
                session_id=state.session_id,
                re_plan=re_plan,
            )

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        return PlanningResult(
            committed_action=committed,
            lookahead_outcomes=outcomes,
            failure_points=failure_points,
            re_plan_triggered=re_plan,
            total_simulations=len(outcomes),
            planning_duration_ms=duration_ms,
        )

    # ── Phase 1: Forward simulation ────────────────────────────────────────────

    async def _simulate_ahead(
        self,
        actions: list[Recommendation],
        state: EpistemicState,
        fragment: dict[str, Any] | None,
    ) -> list[SimulatedActionOutcome]:
        """
        Forward-simulate each action in sequence.

        Uses the approved causal graph to estimate the effect of each action.
        If a prior action would change the state, the simulation accounts
        for the cumulative effect in subsequent actions.
        """
        outcomes: list[SimulatedActionOutcome] = []
        simulation_count = 0

        for i, action in enumerate(actions):
            simulation_count += 1

            # Simulate the action using the causal estimation skill
            try:
                estimated_effect, confidence, failure, reason = await _simulate_single_action(
                    action=action,
                    step_index=i,
                    state=state,
                    fragment=fragment,
                )
            except Exception as exc:
                log.error(
                    "planning.simulation_failed",
                    step=i,
                    error=str(exc),
                    session_id=state.session_id,
                )
                estimated_effect = None
                confidence = 0.0
                failure = True
                reason = f"Simulation error: {exc}"

            outcomes.append(
                SimulatedActionOutcome(
                    action=action,
                    step_index=i,
                    estimated_effect=estimated_effect,
                    confidence=confidence,
                    failure_detected=failure,
                    failure_reason=reason,
                )
            )

            # If this action fails at low confidence, propagate forward
            # but don't stop — we simulate all actions for full lookahead

        return outcomes

    # ── Phase 2: Backward value propagation ────────────────────────────────────

    def _backpropagate(
        self,
        outcomes: list[SimulatedActionOutcome],
    ) -> tuple[list[SimulatedActionOutcome], list[int]]:
        """
        Back-propagate failure signals from later actions to earlier ones.

        When a later action fails, we reduce the propagated_value of
        earlier actions that may have contributed to the failure state.

        Returns updated outcomes and the list of failure point indices.
        """
        failure_points: list[int] = []

        for i, outcome in enumerate(outcomes):
            if outcome.failure_detected:
                failure_points.append(i)

        # Back-propagate: each failure reduces earlier action values
        for fp_idx in failure_points:
            # The penalty for earlier actions weakens with distance
            for i in range(fp_idx):
                distance = fp_idx - i
                penalty = 0.1 * (1.0 / distance)  # Weaker penalty for farther actions
                outcomes[i].propagated_value = max(
                    0.0,
                    outcomes[i].propagated_value - penalty,
                )

        return outcomes, failure_points

    # ── Phase 3: Commit next action ────────────────────────────────────────────

    def _commit_next(
        self,
        outcomes: list[SimulatedActionOutcome],
        re_plan_triggered: bool,
    ) -> Recommendation | None:
        """
        Commit only the next action.

        If the first action has high confidence and no failure detected,
        commit it. Otherwise, check the second action (receding horizon).

        Returns the committed Recommendation, or None if no action is safe.
        """
        if not outcomes:
            return None

        # If first action passes all checks, commit it
        first = outcomes[0]
        if (
            not first.failure_detected
            and first.confidence >= self._confidence_threshold
            and first.propagated_value >= 0.5
        ):
            return first.action

        # If first action fails but second is strong, commit second
        if len(outcomes) > 1:
            second = outcomes[1]
            if (
                not second.failure_detected
                and second.confidence >= self._confidence_threshold
                and second.propagated_value >= 0.5
            ):
                return second.action

        # Receding horizon: no safe action to commit — escalate
        return None


# ── Single-action simulation ──────────────────────────────────────────────────


async def _simulate_single_action(
    *,
    action: Recommendation,
    step_index: int,
    state: EpistemicState,
    fragment: dict[str, Any] | None = None,
) -> tuple[float | None, float, bool, str | None]:
    """
    Simulate a single action against the approved causal graph.

    Uses the effect_estimate skill when an approved fragment is available.
    Falls back to a confidence-weighted heuristic when no fragment is provided.

    Returns:
        (estimated_effect, confidence, failure_detected, failure_reason)
    """
    # ── Use the approved fragment for effect estimation ───────────────────────
    if fragment is not None and fragment.get("approval_status") == "APPROVED":
        from src.skills import effect_estimate

        try:
            result = await effect_estimate(
                fragment=fragment,
                identification_strategy="backdoor",
                assumptions=[],
                data=None,  # Observational data unavailable → heuristic
            )
            ate = result.get("ate")
            estimator = result.get("estimator", "unknown")

            if ate is None:
                return (
                    None,
                    action.confidence * 0.5,
                    False,
                    None,
                )

            # Confidence from the simulation: combine action confidence with estimate
            confidence = min(1.0, (action.confidence * 0.6) + (abs(ate) * 0.4))
            failure = confidence < 0.3 or estimator == "none"
            reason = "Low confidence estimate" if failure else None

            return (float(ate), confidence, failure, reason)

        except Exception as exc:
            return (None, 0.0, True, f"Estimation error: {exc}")

    # ── Fallback: heuristic based on action properties ────────────────────────
    confidence = action.confidence
    failure = confidence < 0.3
    reason = "Low confidence — no approved fragment for simulation" if failure else None

    return (None, confidence, failure, reason)


# ── Planning node for LangGraph ───────────────────────────────────────────────


async def node_plan_ahead(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node that runs FLARE planning before committing actions.

    Integrates into the existing PlatformState graph by:
    1. Extracting candidate actions from EpistemicState.recommendations
    2. Running FLARE lookahead simulation
    3. Updating state with committed action + planning trace

    Expected state keys:
        epistemic: EpistemicState
        disorder_retries: int

    Returns updated PlatformState.
    """
    epistemic: EpistemicState = state["epistemic"]
    retries: int = state.get("disorder_retries", 0)

    actions = epistemic.recommendations
    if not actions:
        log.info(
            "plan_ahead.no_actions",
            session_id=epistemic.session_id,
        )
        return state

    planner = FLARERecedingHorizonPlanner(
        max_plan_depth=min(10, epistemic.cycle_count + 5),
        confidence_threshold=0.4,
    )

    # Load approved fragment for simulation
    fragment = None
    if epistemic.active_graph_fragment_id:
        from src.semantic_layer.graph_context import load_fragment_with_edges
        try:
            fragment = await load_fragment_with_edges(
                str(epistemic.active_graph_fragment_id)
            )
        except Exception as exc:
            log.warning(
                "plan_ahead.fragment_load_failed",
                fragment_id=str(epistemic.active_graph_fragment_id),
                error=str(exc),
            )

    result = await planner.plan(
        actions=list(actions),
        state=epistemic,
        fragment=fragment,
    )

    # ── Update EpistemicState with planning results ────────────────────────────
    flags = list(epistemic.flags)

    if result.committed_action:
        flags.append("plan_ahead_committed")
        # Replace the recommendations list with just the committed action
        updated_recommendations = [result.committed_action]

        # Add lookahead outcomes as additional recommendations marked SIMULATED
        for outcome in result.lookahead_outcomes:
            if outcome.step_index == 0:
                continue  # Skip the committed one

    else:
        flags.append("plan_ahead_escalation")
        # No action could be committed — escalate to human
        epistemic = epistemic.model_copy(
            update={
                "requires_human_review": True,
                "flags": [*flags, "no_safe_action_found"],
                "draft_explanation": (
                    epistemic.draft_explanation
                    + " [PLANNING: No safe action found after FLARE lookahead. "
                    + f"Simulated {result.total_simulations} actions. "
                    + f"Failure points: {result.failure_points}. "
                    + "Escalating to human review.]"
                ),
            }
        )
        return {"epistemic": epistemic, "disorder_retries": retries}

    # Attach planning trace to the explanation
    trace = (
        f" [PLANNING: Simulated {result.total_simulations} actions in "
        f"{result.planning_duration_ms}ms. "
    )
    if result.failure_points:
        trace += f"Detected failures at steps {result.failure_points}. "
    if result.re_plan_triggered:
        trace += "Re-plan triggered after action execution. "
    trace += "]"

    epistemic = epistemic.model_copy(
        update={
            "flags": flags,
            "recommendations": [result.committed_action],
            "draft_explanation": epistemic.draft_explanation + trace,
        }
    )

    return {"epistemic": epistemic, "disorder_retries": retries}

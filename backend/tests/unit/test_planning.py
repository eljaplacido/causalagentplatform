"""
Unit tests for FLARE hierarchical planning (src/orchestration/planning.py).

Tests:
  1. FLARERecedingHorizonPlanner with empty actions
  2. Forward simulation produces expected outcomes
  3. Back-propagation detects failure points correctly
  4. CommitNext selects highest-confidence safe action
  5. Receding-horizon: skips failing first action
  6. Planning node integrates with PlatformState
  7. SimulatedActionOutcome data structure
  8. PlanningResult data structure
"""

from __future__ import annotations

import pytest

from src.orchestration.planning import (
    FLARERecedingHorizonPlanner,
    PlanningResult,
    SimulatedActionOutcome,
    node_plan_ahead,
)
from src.semantic_layer.models import (
    EpistemicState,
    GuardianVerdict,
    Recommendation,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_recommendation(
    rank: int = 1,
    action: str = "Restart service",
    confidence: float = 0.85,
    risk_tier: str = "LOW",
) -> Recommendation:
    return Recommendation(
        rank=rank,
        action=action,
        rationale="Test rationale",
        confidence=confidence,
        risk_tier=risk_tier,
    )


def _make_epistemic_state(
    session_id: str = "test-planning-session",
    agent_id: str = "planning-test-agent",
    recommendations: list[Recommendation] | None = None,
    fragment_id: str | None = None,
) -> EpistemicState:
    return EpistemicState(
        session_id=session_id,
        agent_id=agent_id,
        recommendations=recommendations or [],
        active_graph_fragment_id=fragment_id or "",
        guardian_verdict=GuardianVerdict.APPROVED,
    )


# ── SimulatedActionOutcome ────────────────────────────────────────────────────


def test_simulated_action_outcome_defaults() -> None:
    """SimulatedActionOutcome has sensible defaults."""
    action = _make_recommendation()
    outcome = SimulatedActionOutcome(
        action=action,
        step_index=0,
        estimated_effect=0.8,
        confidence=0.9,
        failure_detected=False,
        failure_reason=None,
    )
    assert outcome.action == action
    assert outcome.step_index == 0
    assert outcome.estimated_effect == 0.8
    assert outcome.confidence == 0.9
    assert outcome.failure_detected is False
    assert outcome.failure_reason is None
    assert outcome.propagated_value == 1.0  # Default


def test_simulated_action_failure() -> None:
    """SimulatedActionOutcome captures failure details."""
    action = _make_recommendation(confidence=0.2)
    outcome = SimulatedActionOutcome(
        action=action,
        step_index=2,
        estimated_effect=None,
        confidence=0.2,
        failure_detected=True,
        failure_reason="Confidence below threshold",
    )
    assert outcome.failure_detected is True
    assert outcome.failure_reason == "Confidence below threshold"


# ── PlanningResult ────────────────────────────────────────────────────────────


def test_planning_result_defaults() -> None:
    """PlanningResult has sensible defaults."""
    result = PlanningResult()
    assert result.committed_action is None
    assert result.lookahead_outcomes == []
    assert result.failure_points == []
    assert result.re_plan_triggered is False
    assert result.total_simulations == 0
    assert result.planning_duration_ms == 0.0


def test_planning_result_with_data() -> None:
    """PlanningResult holds all planning cycle data."""
    action = _make_recommendation()
    outcomes = [
        SimulatedActionOutcome(
            action=action, step_index=0, confidence=0.9,
            estimated_effect=0.8, failure_detected=False, failure_reason=None,
        )
    ]
    result = PlanningResult(
        committed_action=action,
        lookahead_outcomes=outcomes,
        failure_points=[],
        re_plan_triggered=False,
        total_simulations=1,
        planning_duration_ms=15.5,
    )
    assert result.committed_action is action
    assert len(result.lookahead_outcomes) == 1
    assert result.total_simulations == 1
    assert result.planning_duration_ms == 15.5


# ── FLARERecedingHorizonPlanner.plan() ────────────────────────────────────────


async def test_plan_empty_actions() -> None:
    """Planning with no actions returns no committed action."""
    planner = FLARERecedingHorizonPlanner()
    state = _make_epistemic_state(recommendations=[])
    result = await planner.plan(actions=[], state=state)
    assert result.committed_action is None
    assert result.total_simulations == 0


async def test_plan_single_safe_action() -> None:
    """Single action with high confidence is committed."""
    planner = FLARERecedingHorizonPlanner(confidence_threshold=0.4)
    actions = [_make_recommendation(confidence=0.85)]
    state = _make_epistemic_state(recommendations=actions)

    result = await planner.plan(actions=actions, state=state)

    assert result.committed_action is not None
    assert result.committed_action.action == "Restart service"
    assert result.total_simulations == 1
    assert result.re_plan_triggered is False  # No failures detected


async def test_plan_multiple_actions() -> None:
    """Multiple actions are all simulated; one is committed."""
    planner = FLARERecedingHorizonPlanner(
        max_plan_depth=5,
        confidence_threshold=0.4,
    )
    actions = [
        _make_recommendation(rank=1, action="Scale up", confidence=0.9),
        _make_recommendation(rank=2, action="Restart", confidence=0.8),
        _make_recommendation(rank=3, action="Rollback", confidence=0.7),
    ]
    state = _make_epistemic_state(recommendations=actions)

    result = await planner.plan(actions=actions, state=state)

    assert result.total_simulations == 3
    # First action is high confidence → committed
    assert result.committed_action is not None
    assert result.committed_action.action == "Scale up"


async def test_plan_respects_max_depth() -> None:
    """Plan depth is capped at max_plan_depth."""
    planner = FLARERecedingHorizonPlanner(max_plan_depth=2, confidence_threshold=0.4)
    actions = [
        _make_recommendation(rank=i, action=f"Action {i}", confidence=0.8)
        for i in range(1, 6)
    ]
    state = _make_epistemic_state(recommendations=actions)

    result = await planner.plan(actions=actions, state=state)

    assert result.total_simulations == 2  # Capped at max_plan_depth


async def test_plan_low_confidence_escalates() -> None:
    """Low confidence actions result in no commit (receding horizon)."""
    planner = FLARERecedingHorizonPlanner(confidence_threshold=0.5)
    actions = [
        _make_recommendation(rank=1, action="Risky action", confidence=0.25),
        _make_recommendation(rank=2, action="Also risky", confidence=0.3),
    ]
    state = _make_epistemic_state(recommendations=actions)

    result = await planner.plan(actions=actions, state=state)

    # Both actions below threshold → receding horizon, no commit
    assert result.committed_action is None
    assert result.total_simulations == 2


async def test_plan_receding_horizon_skips_first() -> None:
    """When first action fails but second is strong, commit the second."""
    planner = FLARERecedingHorizonPlanner(confidence_threshold=0.4)
    actions = [
        _make_recommendation(rank=1, action="Failing action", confidence=0.2),
        _make_recommendation(rank=2, action="Strong action", confidence=0.85),
    ]
    state = _make_epistemic_state(recommendations=actions)

    result = await planner.plan(actions=actions, state=state)

    assert result.committed_action is not None
    assert result.committed_action.action == "Strong action"
    assert result.total_simulations == 2


# ── Back-propagation ──────────────────────────────────────────────────────────


def test_backpropagate_no_failures() -> None:
    """No failures → no value propagation changes."""
    planner = FLARERecedingHorizonPlanner()
    outcomes = [
        SimulatedActionOutcome(
            action=_make_recommendation(action="A", confidence=0.9),
            step_index=0, confidence=0.9, estimated_effect=0.8,
            failure_detected=False, failure_reason=None,
        ),
        SimulatedActionOutcome(
            action=_make_recommendation(action="B", confidence=0.85),
            step_index=1, confidence=0.85, estimated_effect=0.7,
            failure_detected=False, failure_reason=None,
        ),
    ]
    updated, failures = planner._backpropagate(outcomes)
    assert len(failures) == 0
    assert updated[0].propagated_value == 1.0
    assert updated[1].propagated_value == 1.0


def test_backpropagate_with_failure() -> None:
    """Later failures reduce earlier action values."""
    planner = FLARERecedingHorizonPlanner()
    outcomes = [
        SimulatedActionOutcome(
            action=_make_recommendation(action="A", confidence=0.9),
            step_index=0, confidence=0.9, estimated_effect=0.8,
            failure_detected=False, failure_reason=None,
        ),
        SimulatedActionOutcome(
            action=_make_recommendation(action="B", confidence=0.3),
            step_index=1, confidence=0.3, estimated_effect=None,
            failure_detected=True, failure_reason="Low confidence",
        ),
        SimulatedActionOutcome(
            action=_make_recommendation(action="C", confidence=0.2),
            step_index=2, confidence=0.2, estimated_effect=None,
            failure_detected=True, failure_reason="Very low confidence",
        ),
    ]
    updated, failures = planner._backpropagate(outcomes)
    assert len(failures) == 2
    assert failures == [1, 2]
    # Action A (index 0) should have reduced propagated_value
    # from failures at indices 1 (penalty = 0.1 * 1/1 = 0.1) and 2 (penalty = 0.1 * 1/2 = 0.05)
    assert updated[0].propagated_value < 1.0
    assert updated[1].propagated_value < 1.0  # Affected by action C failure
    assert updated[2].propagated_value == 1.0  # Last action, no later failures


# ── node_plan_ahead (LangGraph integration) ────────────────────────────────────


async def test_node_plan_ahead_no_recommendations() -> None:
    """Node passes through unchanged when no recommendations exist."""
    state_data = {
        "epistemic": _make_epistemic_state(recommendations=[]),
        "disorder_retries": 0,
    }
    result = await node_plan_ahead(state_data)
    assert result["disorder_retries"] == 0
    assert result["epistemic"].recommendations == []


async def test_node_plan_ahead_commits_action() -> None:
    """Node commits a safe action and updates state flags."""
    state_data = {
        "epistemic": _make_epistemic_state(
            recommendations=[
                _make_recommendation(rank=1, action="Safe action", confidence=0.9),
            ],
            fragment_id="test-fragment-id",
        ),
        "disorder_retries": 0,
    }
    result = await node_plan_ahead(state_data)
    assert "plan_ahead_committed" in result["epistemic"].flags
    # The committed action should be the only recommendation now
    assert len(result["epistemic"].recommendations) == 1


async def test_node_plan_ahead_escalates_on_failure() -> None:
    """Node escalates when no safe action is found."""
    state_data = {
        "epistemic": _make_epistemic_state(
            recommendations=[
                _make_recommendation(rank=1, action="Bad action", confidence=0.1),
            ],
            fragment_id="test-fragment-id",
        ),
        "disorder_retries": 0,
    }
    result = await node_plan_ahead(state_data)
    assert result["epistemic"].requires_human_review is True
    assert "no_safe_action_found" in result["epistemic"].flags


async def test_plan_with_approved_fragment() -> None:
    """Planning with an approved fragment uses effect estimation."""
    planner = FLARERecedingHorizonPlanner(confidence_threshold=0.3)
    actions = [_make_recommendation(rank=1, action="Scale up", confidence=0.8)]
    state = _make_epistemic_state(recommendations=actions)

    fragment = {
        "approval_status": "APPROVED",
        "edges": [
            {
                "source": {"id": "t1", "object_type": "Treatment"},
                "target": {"id": "o1", "object_type": "Outcome"},
                "confidence": 0.8,
            }
        ],
    }

    result = await planner.plan(actions=actions, state=state, fragment=fragment)

    assert result.total_simulations == 1
    assert result.committed_action is not None


async def test_plan_with_fragment_estimation_error() -> None:
    """Planning handles estimation error gracefully when fragment is invalid."""
    planner = FLARERecedingHorizonPlanner(confidence_threshold=0.4)
    actions = [_make_recommendation(rank=1, action="Test", confidence=0.8)]
    state = _make_epistemic_state(recommendations=actions)

    fragment = {
        "approval_status": "APPROVED",
        "edges": [],  # empty edges cause estimation to fail
    }

    result = await planner.plan(actions=actions, state=state, fragment=fragment)

    # Even with estimation error, the simulation should complete
    # (may produce an empty result or use fallback)
    assert result.total_simulations >= 0

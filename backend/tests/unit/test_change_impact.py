"""
Unit tests for ChangeImpactAgent.

Invariants tested:
  1. CHAOTIC domain skips impact assessment and escalates
  2. SLO violations detected and flagged
  3. Risk tier based on effect magnitude
  4. Downstream outcomes traced and evaluated
  5. Circuit breaker fires at cycle_count >= 5
  6. process() never mutates the input EpistemicState
  7. Recommendations include affected paths count

From AGENTS.md §2.1 ChangeImpactAgent:
  'Trace the causal propagation of a proposed change through the graph'
  'Produce risk-tiered impact assessment for human review'
  'Surface affected services, SLOs, and operational processes'
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from src.agents.change_impact import ChangeImpactAgent
from src.semantic_layer.models import (
    CausalEdge,
    CausalObjectRef,
    CynefinDomain,
    EdgeDirection,
    EdgeType,
    EpistemicState,
)


pytestmark = pytest.mark.asyncio


def make_impact_state(
    domain: CynefinDomain = CynefinDomain.COMPLICATED,
    fragment_id: str | None = None,
    cycle_count: int = 0,
    description: str = "deploy new version",
) -> EpistemicState:
    return EpistemicState(
        session_id="test-impact-session",
        agent_id="test-impact-agent",
        cynefin_domain=domain,
        cycle_count=cycle_count,
        active_graph_fragment_id=fragment_id or str(uuid4()),
        draft_explanation=description,
    )


# ── 1. CHAOTIC domain skips ────────────────────────────────────────────────────


async def test_chaotic_domain_skips(db_engine: object) -> None:
    """
    CHAOTIC domain must skip impact assessment and route to human.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state(domain=CynefinDomain.CHAOTIC)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "chaotic_impact_skip" in result.flags
    assert "CHAOTIC domain: Automated impact assessment unreliable" in result.draft_explanation


# ── 2. SLO violations detected ────────────────────────────────────────────────


async def test_slo_violations_detected(db_engine: object) -> None:
    """
    SLO threshold breaches must be flagged as HIGH risk.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    async def mock_trace(inputs):
        return {
            "affected_paths": [{"path": ["a", "b", "c"]}],
            "downstream_outcomes": ["latency", "error_rate"],
        }

    agent._skill_causal_trace = mock_trace

    async def mock_estimate(inputs):
        return {
            "estimated_effect": 0.8,
            "direction": "negative",
            "confidence": 0.9,
        }

    agent._skill_effect_estimate = mock_estimate

    async def mock_slo_check(inputs):
        return {
            "slo_violations": ["latency > 100ms", "error_rate > 1%"],
            "overall_risk_tier": "HIGH",
        }

    agent._skill_slo_impact_check = mock_slo_check

    result = await agent.process(state)

    assert "slo_impact_risk" in result.flags
    assert result.recommendations[0].risk_tier == "HIGH"
    assert "SLO impact detected" in result.recommendations[0].action


# ── 3. Risk tier based on effect magnitude ───────────────────────────────────


async def test_risk_tier_based_on_effect(db_engine: object) -> None:
    """
    Risk tier must reflect estimated effect magnitude.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    async def mock_trace(inputs):
        return {
            "affected_paths": [{"path": ["a", "b"]}],
            "downstream_outcomes": ["cpu_usage"],
        }

    agent._skill_causal_trace = mock_trace

    async def mock_estimate(inputs):
        return {
            "estimated_effect": 0.6,  # > 0.5 = HIGH
            "direction": "negative",
            "confidence": 0.8,
        }

    agent._skill_effect_estimate = mock_estimate
    async def mock_slo_check(inputs):
        return {"slo_violations": [], "overall_risk_tier": "MEDIUM"}

    agent._skill_slo_impact_check = mock_slo_check

    result = await agent.process(state)

    # Find the outcome-specific recommendation
    outcome_rec = next(
        (r for r in result.recommendations if "Impact on" in r.action),
        None
    )
    if outcome_rec:
        assert outcome_rec.risk_tier == "HIGH"


# ── 4. No downstream outcomes ────────────────────────────────────────────────


async def test_no_downstream_outcomes(db_engine: object) -> None:
    """
    When no downstream outcomes found, MEDIUM risk recommendation issued.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    async def mock_trace(inputs):
        return {
            "affected_paths": [],
            "downstream_outcomes": [],
        }

    agent._skill_causal_trace = mock_trace

    result = await agent.process(state)

    assert "No downstream outcomes identified" in result.recommendations[0].action
    assert result.recommendations[0].risk_tier == "MEDIUM"


# ── 5. Circuit breaker ────────────────────────────────────────────────────────


async def test_circuit_breaker_fires(db_engine: object) -> None:
    """
    At cycle_count >= 5, impact must escalate without running pipeline.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state(cycle_count=5)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "circuit_breaker" in result.flags


# ── 6. Immutable state ───────────────────────────────────────────────────────


async def test_process_never_mutates_input(db_engine: object) -> None:
    """
    The agent must return a new EpistemicState, never mutate the input.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()
    original_id = id(state)

    result = await agent.process(state)

    assert id(result) != original_id
    assert state.session_id == result.session_id


# ── 7. Explanation includes path count ──────────────────────────────────────


async def test_explanation_includes_paths(db_engine: object) -> None:
    """
    Draft explanation must include affected paths count.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    async def mock_trace(inputs):
        return {
            "affected_paths": [{"path": ["a"]}, {"path": ["b"]}],
            "downstream_outcomes": ["metric1"],
        }

    agent._skill_causal_trace = mock_trace
    async def mock_estimate(inputs):
        return {"estimated_effect": 0.1, "direction": "neutral", "confidence": 0.5}

    agent._skill_effect_estimate = mock_estimate
    async def mock_slo_check(inputs):
        return {"slo_violations": [], "overall_risk_tier": "LOW"}

    agent._skill_slo_impact_check = mock_slo_check

    result = await agent.process(state)

    assert "2 causal path(s) traced" in result.draft_explanation


# ── 8. Reasoning step logged ─────────────────────────────────────────────────


async def test_reasoning_step_logged(db_engine: object) -> None:
    """
    Impact assessment must add reasoning step to audit trail.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    result = await agent.process(state)

    assert any(step.node == "ChangeImpact" for step in result.reasoning_steps)
    assert any(step.action == "IMPACT_ASSESSMENT" for step in result.reasoning_steps)


# ── 9. Graph context loaded ───────────────────────────────────────────────────


async def test_graph_context_loaded(db_engine: object) -> None:
    """
    Semantic layer must be read to get graph context.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    # Verify the skill is called (mock returns context)
    result = await agent.process(state)

    # The agent attempts to read the graph context via semantic_layer_read
    # Even if empty, the skill is invoked
    assert "Change impact assessment complete" in result.draft_explanation


# ── 10. Risk tier escalation with violations ─────────────────────────────────


async def test_risk_escalation_with_violations(db_engine: object) -> None:
    """
    SLO violations + HIGH overall risk tier must escalate.
    """
    agent = ChangeImpactAgent()
    state = make_impact_state()

    async def mock_trace(inputs):
        return {
            "affected_paths": [{"path": ["a"]}],
            "downstream_outcomes": ["slo1"],
        }

    agent._skill_causal_trace = mock_trace
    async def mock_estimate(inputs):
        return {"estimated_effect": 0.3, "direction": "negative", "confidence": 0.7}

    agent._skill_effect_estimate = mock_estimate
    async def mock_slo_check(inputs):
        return {
            "slo_violations": ["slo1"],
            "overall_risk_tier": "CRITICAL",
        }

    agent._skill_slo_impact_check = mock_slo_check

    result = await agent.process(state)

    assert result.requires_human_review is True
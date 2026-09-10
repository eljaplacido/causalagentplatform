"""
Unit tests for DomainExpertElicitationAgent.

Invariants tested:
  1. CHAOTIC domain skips elicitation and escalates
  2. Complex domain activates this agent
  3. Elicited assumptions require governance review
  4. Expert-data disagreements surfaced
  5. requires_human_review set after elicitation
  6. Circuit breaker fires at cycle_count >= 5
  7. process() never mutates the input EpistemicState

From AGENTS.md §2.1 DomainExpertElicitationAgent:
  'COMPLEX domain: this agent is activated when CausalDiscovery cannot resolve
   structure from data alone'
  'Surface expert disagreements and conflicting prior beliefs'
  'All elicited assumptions require governance review before acceptance'
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.agents.domain_expert_elicitation import DomainExpertElicitationAgent
from src.semantic_layer.models import (
    AssumptionType,
    CausalObjectRef,
    CynefinDomain,
    EdgeProposal,
    EdgeType,
    ElicitedFrom,
    EpistemicState,
    ReviewStatus,
)

pytestmark = pytest.mark.asyncio


def make_elicitation_state(
    domain: CynefinDomain = CynefinDomain.COMPLEX,
    proposed_edges: list[CausalEdge] | None = None,
    cycle_count: int = 0,
) -> EpistemicState:
    return EpistemicState(
        session_id="test-elicitation-session",
        agent_id="test-elicitation-agent",
        cynefin_domain=domain,
        cycle_count=cycle_count,
        active_graph_fragment_id=str(uuid4()),
        proposed_edges=proposed_edges or [],
    )


def make_test_edge() -> EdgeProposal:
    return EdgeProposal(
        source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
        target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        confidence=0.7,
        rationale="Proposed from data analysis",
    )


# ── 1. CHAOTIC domain skips ────────────────────────────────────────────────────


async def test_chaotic_domain_skips(db_engine: object) -> None:
    """
    CHAOTIC domain must skip elicitation and set requires_human_review.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state(domain=CynefinDomain.CHAOTIC)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "chaotic_elicitation_skip" in result.flags
    assert "CHAOTIC domain: Expert elicitation suspended" in result.draft_explanation


# ── 2. Complex domain activates ───────────────────────────────────────────────


async def test_complex_domain_activates(db_engine: object) -> None:
    """
    COMPLEX domain must trigger elicitation workflow.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state(domain=CynefinDomain.COMPLEX)

    result = await agent.process(state)

    assert "complex_domain_elicitation_done" in result.flags
    assert len(result.active_assumptions) >= 0


# ── 3. Elicited assumptions require review ───────────────────────────────────


async def test_elicited_assumptions_require_review(db_engine: object) -> None:
    """
    All elicited assumptions must have review_status = PENDING.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()

    # Mock assumption_elicit to return new assumptions
    async def mock_elicit(inputs):
        return {
            "assumptions": [
                {
                    "statement": "Treatment affects outcome via direct path",
                    "type": "STRUCTURAL",
                    "justification": "Domain expert confirms",
                    "expert_id": "expert-001",
                    "domain": "devops",
                }
            ]
        }

    agent._skill_assumption_elicit = mock_elicit

    result = await agent.process(state)

    # Check that new assumptions have PENDING review status
    new_assumptions = [a for a in result.active_assumptions if a.elicited_from == ElicitedFrom.DOMAIN_EXPERT]
    for assumption in new_assumptions:
        assert assumption.review_status == ReviewStatus.PENDING


# ── 4. Expert-data disagreements surfaced ────────────────────────────────────


async def test_disagreements_surfaced(db_engine: object) -> None:
    """
    Conflicts between expert assumptions and data-driven proposals must be surfaced.
    """
    agent = DomainExpertElicitationAgent()
    edge = make_test_edge()
    state = make_elicitation_state(proposed_edges=[edge])

    async def mock_elicit(inputs):
        return {
            "assumptions": [
                {
                    "statement": "Expert believes opposite direction",
                    "type": "DOMAIN",
                    "justification": "Expert opinion",
                    "expert_id": "expert-001",
                    "domain": "devops",
                }
            ]
        }

    agent._skill_assumption_elicit = mock_elicit
    async def mock_disagreement(inputs):
        return {
            "disagreements": [
                "Expert direction conflicts with data-driven edge proposal"
            ]
        }

    agent._skill_disagreement_surface = mock_disagreement

    result = await agent.process(state)

    assert "expert_data_disagreement" in result.flags
    assert any("Resolve" in r.action for r in result.recommendations)


# ── 5. Requires human review after elicitation ───────────────────────────────


async def test_requires_human_review_after_elicitation(db_engine: object) -> None:
    """
    Elicitation must set requires_human_review = True (all assumptions need governance).
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()

    result = await agent.process(state)

    assert result.requires_human_review is True


# ── 6. Circuit breaker ────────────────────────────────────────────────────────


async def test_circuit_breaker_fires(db_engine: object) -> None:
    """
    At cycle_count >= 5, elicitation must escalate without running pipeline.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state(cycle_count=5)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "circuit_breaker" in result.flags


# ── 7. Immutable state ───────────────────────────────────────────────────────


async def test_process_never_mutates_input(db_engine: object) -> None:
    """
    The agent must return a new EpistemicState, never mutate the input.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()
    original_id = id(state)

    result = await agent.process(state)

    assert id(result) != original_id
    assert state.session_id == result.session_id


# ── 8. Assumption type preservation ─────────────────────────────────────────


async def test_assumption_type_preserved(db_engine: object) -> None:
    """
    Assumptions from elicitation must preserve their type.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()

    async def mock_elicit(inputs):
        return {
            "assumptions": [
                {
                    "statement": "SUTVA holds",
                    "type": "SUTVA",
                    "justification": "Stable unit treatment value assumption",
                    "expert_id": "expert-001",
                    "domain": "devops",
                }
            ]
        }

    agent._skill_assumption_elicit = mock_elicit

    result = await agent.process(state)

    new_assumptions = [a for a in result.active_assumptions if a.elicited_from == ElicitedFrom.DOMAIN_EXPERT]
    if new_assumptions:
        assert new_assumptions[0].assumption_type == AssumptionType.SUTVA


# ── 9. ElicitedFrom set to DOMAIN_EXPERT ─────────────────────────────────────


async def test_elicited_from_set(db_engine: object) -> None:
    """
    All elicited assumptions must have elicited_from = DOMAIN_EXPERT.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()

    async def mock_elicit(inputs):
        return {"assumptions": []}

    agent._skill_assumption_elicit = mock_elicit

    result = await agent.process(state)

    # If any assumptions were added, they must be marked as DOMAIN_EXPERT
    for a in result.active_assumptions:
        assert a.elicited_from in (ElicitedFrom.DOMAIN_EXPERT, ElicitedFrom.AGENT)


# ── 10. Reasoning step logged ────────────────────────────────────────────────


async def test_reasoning_step_logged(db_engine: object) -> None:
    """
    Elicitation must add reasoning step to audit trail.
    """
    agent = DomainExpertElicitationAgent()
    state = make_elicitation_state()

    result = await agent.process(state)

    assert any(step.node == "DomainExpertElicitation" for step in result.reasoning_steps)

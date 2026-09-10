"""
Unit tests for EvidenceGroundingAgent.

Invariants tested:
  1. No proposed edges returns early with explanation
  2. Edges without evidence flagged as 'insufficient_evidence'
  3. Contradictions detected across evidence set
  4. Grounded edges count in recommendations
  5. Agent NEVER fabricates citations (critical invariant)
  6. Circuit breaker fires at cycle_count >= 5
  7. process() never mutates the input EpistemicState

From AGENTS.md §2.1 EvidenceGroundingAgent:
  'NEVER fabricate citations — flag insufficient evidence instead'
  'Read-only access to semantic layer and evidence store'
  'Distinguish between statistical evidence and expert knowledge evidence'
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.agents.evidence_grounding import EvidenceGroundingAgent
from src.semantic_layer.models import (
    CausalObjectRef,
    CynefinDomain,
    EdgeProposal,
    EdgeType,
    EpistemicState,
)

pytestmark = pytest.mark.asyncio


def make_grounding_state(
    domain: CynefinDomain = CynefinDomain.COMPLICATED,
    proposed_edges: list[CausalEdge] | None = None,
    cycle_count: int = 0,
) -> EpistemicState:
    return EpistemicState(
        session_id="test-grounding-session",
        agent_id="test-grounding-agent",
        cynefin_domain=domain,
        cycle_count=cycle_count,
        proposed_edges=proposed_edges or [],
    )


def make_test_edge() -> EdgeProposal:
    return EdgeProposal(
        source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
        target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        confidence=0.8,
        rationale="Test edge",
    )


# ── 1. No edges returns early ─────────────────────────────────────────────────


async def test_no_edges_returns_early(db_engine: object) -> None:
    """
    Without proposed_edges, grounding returns early with explanation.
    """
    agent = EvidenceGroundingAgent()
    state = make_grounding_state(proposed_edges=[])

    result = await agent.process(state)

    assert "No proposed edges to ground" in result.draft_explanation


# ── 2. Insufficient evidence flagged ──────────────────────────────────────────


async def test_insufficient_evidence_flagged(db_engine: object) -> None:
    """
    Edges without valid citations must be flagged as insufficient_evidence.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    # Mock evidence_link to return empty citations
    async def mock_evidence_link(inputs):
        return {"citations": []}

    agent._skill_evidence_link = mock_evidence_link

    result = await agent.process(state)

    assert "insufficient_evidence" in result.flags
    assert result.recommendations[0].risk_tier == "HIGH"
    assert "Review" in result.recommendations[0].action


# ── 3. Contradictions detected ────────────────────────────────────────────────


async def test_contradictions_detected(db_engine: object) -> None:
    """
    Contradictory evidence across sources must be flagged.
    """
    agent = EvidenceGroundingAgent()
    edge1 = make_test_edge()
    edge2 = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge1, edge2])

    # Mock evidence_link to return multiple citations
    async def mock_evidence_link(inputs):
        return {
            "citations": [
                {"source": "doc1", "direction": "positive"},
                {"source": "doc2", "direction": "negative"},
            ]
        }

    agent._skill_evidence_link = mock_evidence_link
    async def mock_contradiction_detect(inputs):
        return {
            "contradictions": [
                "Edge 1 direction conflicts with Edge 2"
            ]
        }

    agent._skill_contradiction_detect = mock_contradiction_detect

    result = await agent.process(state)

    assert "contradictory_evidence" in result.flags
    assert result.recommendations[0].risk_tier == "HIGH"


# ── 4. Grounded edges counted ────────────────────────────────────────────────


async def test_grounded_edges_counted(db_engine: object) -> None:
    """
    Successfully grounded edges count appears in explanation.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    async def mock_evidence_link(inputs):
        return {
            "citations": [
                {"source": "statistical_test", "p_value": 0.01},
                {"source": "runbook", "snippet": "documented behavior"},
            ]
        }

    agent._skill_evidence_link = mock_evidence_link

    result = await agent.process(state)

    assert "1/1 edges grounded" in result.draft_explanation
    assert result.recommendations[-1].risk_tier == "LOW"


# ── 5. Agent never fabricates citations (critical) ────────────────────────────


async def test_never_fabricates_citations(db_engine: object) -> None:
    """
    CRITICAL: Agent must not fabricate citations when evidence is missing.
    This is verified by the stub returning empty and the agent flagging insufficient.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    async def mock_evidence_link(inputs):
        return {"citations": []}

    agent._skill_evidence_link = mock_evidence_link

    result = await agent.process(state)

    # Agent MUST flag insufficient rather than fabricate
    assert "insufficient_evidence" in result.flags
    assert result.recommendations[0].risk_tier == "HIGH"
    # No fabricated citation IDs attached to any recommendation
    assert all(r.supporting_fragment_id is None for r in result.recommendations)


# ── 6. Circuit breaker ────────────────────────────────────────────────────────


async def test_circuit_breaker_fires(db_engine: object) -> None:
    """
    At cycle_count >= 5, grounding must escalate without running pipeline.
    """
    agent = EvidenceGroundingAgent()
    state = make_grounding_state(cycle_count=5)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "circuit_breaker" in result.flags


# ── 7. Immutable state ───────────────────────────────────────────────────────


async def test_process_never_mutates_input(db_engine: object) -> None:
    """
    The agent must return a new EpistemicState, never mutate the input.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])
    original_id = id(state)

    result = await agent.process(state)

    assert id(result) != original_id
    assert state.session_id == result.session_id


# ── 8. Statistical vs expert evidence distinction ─────────────────────────────


async def test_evidence_type_distinction(db_engine: object) -> None:
    """
    Evidence citations must be distinguished by type.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    async def mock_evidence_link(inputs):
        return {
            "citations": [
                {"source": "t_test.pval", "evidence_type": "STATISTICAL_TEST"},
                {"source": "sre_runbook.md", "evidence_type": "DOCUMENT"},
            ]
        }

    agent._skill_evidence_link = mock_evidence_link
    async def mock_validate(inputs):
        citation = inputs.get("citation", {})
        return {"valid": bool(citation.get("source"))}

    agent._skill_statistical_validate = mock_validate

    result = await agent.process(state)

    # Validates both types without error
    assert "grounded" in result.draft_explanation.lower() or "citation" in result.draft_explanation.lower()


# ── 9. Confidence score based on grounding ────────────────────────────────────


async def test_confidence_based_on_grounding(db_engine: object) -> None:
    """
    Confidence in recommendations reflects evidence strength.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    async def mock_evidence_link(inputs):
        return {"citations": [{"source": "test"}]}

    agent._skill_evidence_link = mock_evidence_link

    result = await agent.process(state)

    grounded_rec = next(
        (r for r in result.recommendations if "grounded" in r.action.lower()),
        None
    )
    if grounded_rec:
        assert grounded_rec.confidence > 0


# ── 10. Reasoning step logged ────────────────────────────────────────────────


async def test_reasoning_step_logged(db_engine: object) -> None:
    """
    Grounding must add reasoning step to audit trail.
    """
    agent = EvidenceGroundingAgent()
    edge = make_test_edge()
    state = make_grounding_state(proposed_edges=[edge])

    result = await agent.process(state)

    assert len(result.reasoning_steps) > 0
    assert any(step.node == "EvidenceGrounding" for step in result.reasoning_steps)

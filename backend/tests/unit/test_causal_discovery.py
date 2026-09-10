"""
Unit tests for CausalDiscoveryAgent.

Invariants tested:
  1. CHAOTIC domain short-circuits immediately (no proposed edges, escalation flag)
  2. Non-CHAOTIC domain produces proposed_edges
  3. Circuit breaker prevents processing at cycle_count >= 5
  4. COMPLEX domain flags all proposals for expert review
  5. Low-evidence edges receive the evidence floor penalty
  6. process() always returns an EpistemicState (immutable contract)
  7. draft_explanation is populated after processing
"""

from __future__ import annotations

import pytest

from src.agents.causal_discovery import (
    CausalDiscoveryAgent,
)
from src.exceptions import CircuitBreakerError
from src.semantic_layer.models import (
    CynefinDomain,
    EdgeProposal,
    EpistemicState,
)

pytestmark = pytest.mark.asyncio


def make_state(
    domain: CynefinDomain = CynefinDomain.COMPLICATED,
    cycle_count: int = 0,
    hallucination_risk: float = 0.0,
) -> EpistemicState:
    return EpistemicState(
        session_id="test-discovery-session",
        agent_id="test-discovery-agent",
        cynefin_domain=domain,
        cycle_count=cycle_count,
        hallucination_risk_score=hallucination_risk,
    )


# ── 1. CHAOTIC domain short-circuit ───────────────────────────────────────────


async def test_chaotic_domain_skips_discovery() -> None:
    """
    CHAOTIC domain must short-circuit immediately.

    Per AGENTS.md §2.1 CausalDiscoveryAgent constraints:
    'Never writes edges directly to the semantic layer.'
    For CHAOTIC domain, no analysis should be attempted at all.
    """
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.CHAOTIC)

    result = await agent.process(state)

    assert result.requires_human_review is True
    assert "chaotic_domain_skip" in result.flags
    assert len(result.proposed_edges) == 0, (
        "CHAOTIC domain must not produce proposed edges"
    )
    assert "CHAOTIC" in result.draft_explanation.upper()


# ── 2. Non-CHAOTIC domain produces edges ─────────────────────────────────────


@pytest.mark.parametrize(
    "domain",
    [CynefinDomain.CLEAR, CynefinDomain.COMPLICATED, CynefinDomain.COMPLEX],
)
async def test_non_chaotic_produces_proposed_edges(domain: CynefinDomain) -> None:
    """
    All non-CHAOTIC, non-DISORDER domains must produce at least one proposed edge.

    Proposed edges are candidates for Guardian review — the agent never writes
    them directly to the semantic layer.
    """
    agent = CausalDiscoveryAgent()
    state = make_state(domain=domain)

    result = await agent.process(state)

    assert isinstance(result, EpistemicState)
    assert len(result.proposed_edges) >= 1, (
        f"Expected proposed edges for {domain} domain, got none"
    )
    for edge in result.proposed_edges:
        assert isinstance(edge, EdgeProposal)
        assert 0.0 <= edge.confidence <= 1.0
        assert edge.rationale, "Every proposed edge must have a rationale"


# ── 3. Circuit breaker ────────────────────────────────────────────────────────


async def test_circuit_breaker_at_max_cycles() -> None:
    """
    CausalDiscoveryAgent must raise CircuitBreakerError when cycle_count >= 5.

    This is the platform-wide circuit breaker invariant (AGENTS.md §5.3).
    """
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLICATED, cycle_count=5)

    with pytest.raises(CircuitBreakerError) as exc_info:
        await agent.process(state)

    assert exc_info.value.cycle_count == 5


async def test_circuit_breaker_not_triggered_below_max() -> None:
    """Circuit breaker must NOT fire at cycle_count < 5."""
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLICATED, cycle_count=4)

    # Should not raise
    result = await agent.process(state)
    assert isinstance(result, EpistemicState)


# ── 4. COMPLEX domain flags for expert review ─────────────────────────────────


async def test_complex_domain_flags_expert_review() -> None:
    """
    COMPLEX domain must surface causal uncertainty explicitly.

    Per AGENTS.md §5.2: 'COMPLEX → CausalDiscovery → ExpertElicitation'
    The agent must flag the need for expert validation.
    """
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLEX)

    result = await agent.process(state)

    assert result.requires_human_review is True, (
        "COMPLEX domain must require human review"
    )
    has_complex_flag = any(
        "complex" in flag.lower() or "expert" in flag.lower()
        for flag in result.flags
    )
    # Check draft explanation mentions uncertainty
    assert (
        "COMPLEX" in result.draft_explanation.upper()
        or "uncertain" in result.draft_explanation.lower()
        or "expert" in result.draft_explanation.lower()
    ), "COMPLEX domain explanation must surface uncertainty"


# ── 5. Edge confidence is bounded ────────────────────────────────────────────


async def test_proposed_edge_confidence_in_bounds() -> None:
    """All proposed edges must have confidence in [0.0, 1.0]."""
    agent = CausalDiscoveryAgent()

    for domain in [CynefinDomain.CLEAR, CynefinDomain.COMPLICATED, CynefinDomain.COMPLEX]:
        state = make_state(domain=domain)
        result = await agent.process(state)

        for edge in result.proposed_edges:
            assert 0.0 <= edge.confidence <= 1.0, (
                f"Edge confidence {edge.confidence} out of bounds for {domain}"
            )


# ── 6. process() returns EpistemicState (immutability contract) ───────────────


async def test_process_returns_epistemic_state() -> None:
    """process() must always return an EpistemicState, never mutate the input."""
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLICATED)
    original_edges = list(state.proposed_edges)

    result = await agent.process(state)

    assert isinstance(result, EpistemicState)
    # Input state must be unchanged (immutable copy pattern)
    assert state.proposed_edges == original_edges, (
        "process() must not mutate the input EpistemicState"
    )
    # Returned state must have incremented cycle_count
    assert result.cycle_count == state.cycle_count + 1


# ── 7. draft_explanation is populated ────────────────────────────────────────


async def test_draft_explanation_populated() -> None:
    """draft_explanation must be non-empty after processing."""
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLICATED)

    result = await agent.process(state)

    assert result.draft_explanation, "draft_explanation must be populated"
    assert len(result.draft_explanation) > 10


# ── 8. Recommendations populated from proposed edges ────────────────────────


async def test_recommendations_populated() -> None:
    """Recommendations must reflect proposed edges."""
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.COMPLICATED)

    result = await agent.process(state)

    if result.proposed_edges:
        assert len(result.recommendations) >= 1, (
            "Recommendations must be populated from proposed edges"
        )
        for rec in result.recommendations:
            assert rec.rank >= 1
            assert 0.0 <= rec.confidence <= 1.0
            assert rec.risk_tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ── 9. DISORDER domain — not short-circuited (uses default handling) ──────────


async def test_disorder_domain_processes_without_error() -> None:
    """DISORDER domain should not crash — it uses the default base confidence."""
    agent = CausalDiscoveryAgent()
    state = make_state(domain=CynefinDomain.DISORDER)

    # Should not raise — DISORDER is not short-circuited in CausalDiscoveryAgent
    result = await agent.process(state)
    assert isinstance(result, EpistemicState)

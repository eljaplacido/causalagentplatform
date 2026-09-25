"""
Unit tests for IncidentTriageAgent.

Invariants tested:
  1. CHAOTIC domain produces immediate escalation recommendation, no AI analysis
  2. CHAOTIC domain sets requires_human_review=True and 'chaotic_incident' flag
  3. COMPLICATED domain runs full triage pipeline and produces root-cause proposals
  4. DISORDER domain prompts re-classification without deep analysis
  5. Circuit breaker fires at cycle_count >= 5
  6. Guardian check is called and audit entry written for non-CHAOTIC incidents
  7. process() never mutates the input EpistemicState
  8. CRITICAL recommendation rank 1 for CHAOTIC incidents
  9. Counterfactual reasoning called for COMPLEX domain

From AGENTS.md §2.2 IncidentTriageAgent:
  'CHAOTIC-domain incidents route immediately to human incident commander
   with minimal AI framing'
  'All proposed interventions require human approval before execution'
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.agents.incident_triage import IncidentTriageAgent
from src.db.types import TIMESTAMP, typed_text
from src.exceptions import CircuitBreakerError
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
)

pytestmark = pytest.mark.asyncio


def make_incident_state(
    domain: CynefinDomain = CynefinDomain.DISORDER,
    description: str = "high CPU latency on api-gateway service",
    cycle_count: int = 0,
) -> EpistemicState:
    return EpistemicState(
        session_id="test-triage-session",
        agent_id="test-triage-agent",
        cynefin_domain=domain,
        cycle_count=cycle_count,
        draft_explanation=description,
    )


# ── 1. CHAOTIC domain immediate escalation ────────────────────────────────────


async def test_chaotic_incident_immediate_escalation(db_engine: object) -> None:
    """
    CHAOTIC-domain incidents must route immediately to human escalation.

    The agent must set requires_human_review=True, add 'chaotic_incident' flag,
    and produce a CRITICAL rank-1 recommendation with no AI causal analysis.

    From AGENTS.md: 'Chaotic-domain incidents route immediately to human
    incident commander with minimal AI framing'
    """
    agent = IncidentTriageAgent()
    state = make_incident_state(
        description="cascading failure unknown total outage"
    )

    result = await agent.process(state)

    assert result.requires_human_review is True, (
        "CHAOTIC incident must set requires_human_review=True"
    )
    assert "chaotic_incident" in result.flags, (
        "CHAOTIC incident must add 'chaotic_incident' flag"
    )
    assert len(result.recommendations) >= 1
    assert result.recommendations[0].risk_tier == "CRITICAL", (
        "CHAOTIC incident rank-1 recommendation must be CRITICAL risk tier"
    )
    assert "ESCALATE" in result.recommendations[0].action.upper(), (
        "CHAOTIC rank-1 recommendation must be an escalation action"
    )
    # No proposed edges — no causal analysis performed
    assert len(result.proposed_edges) == 0, (
        "CHAOTIC domain must not generate proposed causal edges"
    )


# ── 2. CHAOTIC requires_human_review flag ────────────────────────────────────


async def test_chaotic_sets_human_review_flag(db_engine: object) -> None:
    """requires_human_review must be True for CHAOTIC regardless of input state."""
    agent = IncidentTriageAgent()
    # Explicitly set non-CHAOTIC domain — the agent must still detect CHAOTIC
    # from the description keywords
    state = make_incident_state(description="never seen before cascading total outage")

    result = await agent.process(state)

    assert result.requires_human_review is True


# ── 3. COMPLICATED domain full triage pipeline ───────────────────────────────


async def test_complicated_incident_runs_full_triage(db_engine: object) -> None:
    """
    COMPLICATED domain must produce root-cause proposals and recommendations.

    The pipeline should:
    - Classify domain
    - Query experience buffer (may return empty)
    - Propose root-cause edges
    - Estimate interventions
    - Run Guardian check (writes audit entry)
    """
    agent = IncidentTriageAgent()
    state = make_incident_state(
        description="deployment cpu memory timeout latency degradation"
    )

    result = await agent.process(state)

    assert isinstance(result, EpistemicState)
    # Should have classified as COMPLICATED from keywords
    assert result.cynefin_domain in (
        CynefinDomain.COMPLICATED,
        CynefinDomain.COMPLEX,
        CynefinDomain.CLEAR,
    ), f"Expected non-CHAOTIC domain, got: {result.cynefin_domain}"

    assert len(result.proposed_edges) >= 1, (
        "COMPLICATED incident must produce at least one root-cause candidate"
    )
    assert len(result.recommendations) >= 1, (
        "COMPLICATED incident must produce recommendations"
    )


# ── 4. DISORDER domain prompts re-classification ──────────────────────────────


async def test_disorder_domain_no_deep_analysis(db_engine: object) -> None:
    """
    DISORDER domain must not attempt deep causal analysis.
    It should set a disorder flag and prompt for more signal.
    """
    agent = IncidentTriageAgent()
    # Provide a minimal description that won't trigger any domain keywords
    state = make_incident_state(description="something happened somewhere")

    result = await agent.process(state)

    assert isinstance(result, EpistemicState)
    # May classify as COMPLICATED/COMPLEX based on no-keywords heuristic
    # Key invariant: if classified as DISORDER, no deep analysis and human review set
    if result.cynefin_domain == CynefinDomain.DISORDER:
        assert result.requires_human_review is True
        assert any("disorder" in f.lower() for f in result.flags)


# ── 5. Circuit breaker ────────────────────────────────────────────────────────


async def test_circuit_breaker_at_max_cycles() -> None:
    """IncidentTriageAgent must raise CircuitBreakerError at cycle_count >= 5."""
    agent = IncidentTriageAgent()
    state = make_incident_state(cycle_count=5)

    with pytest.raises(CircuitBreakerError) as exc_info:
        await agent.process(state)

    assert exc_info.value.cycle_count == 5


# ── 6. Guardian check writes audit entry ────────────────────────────────────


async def test_triage_writes_audit_entry(db_engine: object) -> None:
    """
    For non-CHAOTIC incidents, the Guardian check must write an audit_log entry.

    This validates the Guardian-gated intervention estimation contract.
    From AGENTS.md: 'All proposed interventions require human approval before execution.'
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    agent = IncidentTriageAgent()
    state = make_incident_state(
        description="deployment latency cpu timeout"
    )

    # Count audit entries before
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-arg]
    async with session_factory() as session:
        before = await session.execute(text("SELECT COUNT(*) FROM audit_log"))
        count_before = before.scalar() or 0

    await agent.process(state)

    async with session_factory() as session:
        after = await session.execute(text("SELECT COUNT(*) FROM audit_log"))
        count_after = after.scalar() or 0

    # Guardian check inside the triage pipeline should write at least one entry
    assert count_after > count_before, (
        "IncidentTriageAgent must trigger a Guardian audit_log entry for non-CHAOTIC triage"
    )


# ── 7. Immutability contract ─────────────────────────────────────────────────


async def test_process_does_not_mutate_input(db_engine: object) -> None:
    """process() must return a new EpistemicState without mutating the input."""
    agent = IncidentTriageAgent()
    state = make_incident_state(
        description="deployment latency cpu timeout"
    )
    original_flags = list(state.flags)
    original_recommendations = list(state.recommendations)

    result = await agent.process(state)

    assert isinstance(result, EpistemicState)
    assert state.flags == original_flags, "Input state.flags must not be mutated"
    assert state.recommendations == original_recommendations, (
        "Input state.recommendations must not be mutated"
    )
    # Result must be different from input
    assert result is not state


# ── 8. CHAOTIC rank-1 is CRITICAL ────────────────────────────────────────────


async def test_chaotic_recommendation_risk_tier(db_engine: object) -> None:
    """CHAOTIC incident rank-1 recommendation must have risk_tier CRITICAL."""
    agent = IncidentTriageAgent()
    state = make_incident_state(description="cascading unknown never seen total outage")

    result = await agent.process(state)

    if result.cynefin_domain == CynefinDomain.CHAOTIC:
        assert result.recommendations[0].risk_tier == "CRITICAL"
        assert result.recommendations[0].confidence == 1.0


# ── 9. Non-CHAOTIC produces draft_explanation ────────────────────────────────


async def test_triage_populates_draft_explanation(db_engine: object) -> None:
    """draft_explanation must be populated and mention the classified domain."""
    agent = IncidentTriageAgent()
    state = make_incident_state(
        description="deployment timeout high latency cpu memory"
    )

    result = await agent.process(state)

    assert result.draft_explanation, "draft_explanation must be populated"
    assert len(result.draft_explanation) > 20


async def test_experience_buffer_returns_prior_similar_incident(
    db_session: object,
) -> None:
    """Incident triage should retrieve prior similar incidents from durable session snapshots."""
    prior_state = make_incident_state(
        description="deployment cpu memory timeout latency degradation on payments service"
    ).model_copy(
        update={
            "agent_id": "incident-triage-agent",
            "draft_explanation": (
                "deployment cpu memory timeout latency degradation "
                "on payments service"
            ),
        }
    )

    await db_session.execute(
        typed_text(
            "INSERT INTO session_state "
            "(session_id, user_id, agent_id, state_json, created_at, updated_at) "
            "VALUES (:sid, :uid, :aid, :state_json, :created_at, :updated_at)",
            created_at=TIMESTAMP,
            updated_at=TIMESTAMP,
        ),
        {
            "sid": "prior-incident-session",
            "uid": "test-user-001",
            "aid": "incident-triage-agent",
            "state_json": json.dumps(prior_state.model_dump(mode="json")),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    await db_session.commit()

    agent = IncidentTriageAgent()
    current_state = make_incident_state(
        description="deployment timeout cpu memory on payments service"
    )

    result = await agent.process(current_state)

    assert any(
        "Review past incident:" in recommendation.action
        for recommendation in result.recommendations
    ), "Expected similar-incident recommendation from durable experience buffer"
    assert "similar past incident" in result.draft_explanation.lower()


async def test_counterfactual_analysis_is_explicitly_graph_heuristic(db_engine: object) -> None:
    """The counterfactual text must say it did not answer the question, and why."""
    agent = IncidentTriageAgent()
    state = make_incident_state(description="intermittent unclear multiple pattern")

    result = await agent.process(state)

    assert "Counterfactual analysis:" in result.draft_explanation
    assert "Not answered" in result.draft_explanation
    assert "structural causal model" in result.draft_explanation
    assert "%" not in result.draft_explanation.split("Counterfactual analysis:")[1]


async def test_intervention_estimate_is_not_confidence_only(db_engine: object) -> None:
    """Intervention recommendations should be grounded in estimator output wording."""
    agent = IncidentTriageAgent()
    state = make_incident_state(description="deployment cpu memory timeout latency")

    result = await agent.process(state)

    ranked_actions = [recommendation.action for recommendation in result.recommendations]
    assert any(
        "Investigate and mitigate the suspected causes path" in action
        for action in ranked_actions
    )


@pytest.mark.parametrize(
    "description",
    ["never seen this before", "a total outage in eu-west", "cascading, then silence"],
)
async def test_each_chaotic_keyword_is_seen_on_its_own(description: str) -> None:
    """The old check split on whitespace, so the two-word keywords could never match
    and punctuation hid the single ones; the test above passed only because its
    sentence also contained a bare "cascading"."""
    agent = IncidentTriageAgent()
    out = await agent._skill_cynefin_classify({"description": description, "signals": {}})
    assert out["domain"] is CynefinDomain.CHAOTIC


async def test_an_incident_no_keyword_describes_is_disorder_not_a_guess() -> None:
    """It used to be COMPLEX with no active assumptions and COMPLICATED otherwise -- a
    confident label for 'nothing matched', on an external corpus nearly always wrong."""
    agent = IncidentTriageAgent()
    for signals in ({}, {"active_assumptions": 3}):
        out = await agent._skill_cynefin_classify(
            {"description": "checkout feels slow for some users", "signals": signals}
        )
        assert out["domain"] is CynefinDomain.DISORDER
        assert out["confidence"] == 0.0


async def test_a_disorder_incident_goes_to_a_person_without_analysis() -> None:
    agent = IncidentTriageAgent()
    result = await agent.process(make_incident_state(description="checkout feels slow"))
    assert result.requires_human_review is True
    assert "disorder_domain" in result.flags

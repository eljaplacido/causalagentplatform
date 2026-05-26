"""
Unit tests for EpistemicState invariants.

These are fast, isolated unit tests — no database, no external services.
All tests operate on pure Pydantic model logic.

From TESTING_CICD.md §5.1 (EpistemicState Contract Tests) and
AGENTS.md §4 (EpistemicState spec).

Tests:
  1. Initial state defaults to DISORDER domain
  2. CircuitBreakerError raised at cycleCount >= 5
  3. Hallucination risk > 0.7 sets requires_human_review flag
  4. APPROVED state allows semantic layer writes
  5. PENDING verdict blocks semantic layer writes
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from src.agents.base import BaseAgent, MAX_CYCLES, HALLUCINATION_THRESHOLD
from src.exceptions import CircuitBreakerError, GuardianBlockedError
from src.semantic_layer.models import (
    Assumption,
    AssumptionType,
    CynefinDomain,
    EdgeProposal,
    EdgeType,
    CausalObjectRef,
    ElicitedFrom,
    EpistemicState,
    GuardianVerdict,
    ReasoningStep,
    ToolCall,
    UserRef,
)
from tests.conftest import make_epistemic_state


# ── 1. Initial state is DISORDER domain ──────────────────────────────────────


def test_initial_state_is_disorder_domain() -> None:
    """
    A freshly constructed EpistemicState must default to DISORDER domain
    with confidence 0.0.

    From AGENTS.md §4: agents self-classify domain complexity. DISORDER is the
    safe default — it forces classification before any action is taken.
    """
    state = EpistemicState(session_id="s-001", agent_id="a-001")

    assert state.cynefin_domain == CynefinDomain.DISORDER, (
        f"Initial cynefin_domain should be DISORDER, got {state.cynefin_domain}"
    )
    assert state.domain_confidence == 0.0, (
        f"Initial domain_confidence should be 0.0, got {state.domain_confidence}"
    )
    assert state.guardian_verdict == GuardianVerdict.PENDING, (
        f"Initial guardian_verdict should be PENDING, got {state.guardian_verdict}"
    )
    assert state.cycle_count == 0, (
        f"Initial cycle_count should be 0, got {state.cycle_count}"
    )
    assert state.hallucination_risk_score == 0.0
    assert state.requires_human_review is False
    assert state.proposed_edges == []
    assert state.reasoning_steps == []
    assert state.tool_call_log == []
    assert state.recommendations == []


def test_initial_state_session_and_agent_id() -> None:
    """session_id and agent_id must be preserved exactly as provided."""
    state = EpistemicState(session_id="my-session-xyz", agent_id="my-agent-abc")
    assert state.session_id == "my-session-xyz"
    assert state.agent_id == "my-agent-abc"


def test_state_immutable_copy_preserves_all_fields() -> None:
    """
    model_copy() must preserve all fields that are not explicitly updated.
    """
    state = EpistemicState(
        session_id="s-001",
        agent_id="a-001",
        cynefin_domain=CynefinDomain.COMPLICATED,
        domain_confidence=0.9,
        overall_confidence=0.75,
        cycle_count=2,
    )
    updated = state.model_copy(update={"cycle_count": 3})

    assert updated.cynefin_domain == CynefinDomain.COMPLICATED
    assert updated.domain_confidence == 0.9
    assert updated.overall_confidence == 0.75
    assert updated.cycle_count == 3
    # Original unchanged
    assert state.cycle_count == 2


# ── 2. Circuit breaker at 5 cycles ──────────────────────────────────────────


def test_circuit_breaker_at_5_cycles() -> None:
    """
    BaseAgent._check_circuit_breaker() must raise CircuitBreakerError
    when cycle_count >= MAX_CYCLES (5).

    From AGENTS.md §5.3:
        'Maximum 5 cycles before mandatory human escalation'
    From TESTING_CICD.md §4.3.
    """

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(
        session_id="cb-test",
        agent_id="test-agent",
        cycle_count=MAX_CYCLES,  # = 5
    )

    with pytest.raises(CircuitBreakerError) as exc_info:
        agent._check_circuit_breaker(state)

    err = exc_info.value
    assert err.cycle_count == MAX_CYCLES, (
        f"CircuitBreakerError.cycle_count should be {MAX_CYCLES}, got {err.cycle_count}"
    )
    assert err.session_id == "cb-test"


def test_circuit_breaker_at_4_cycles_does_not_raise() -> None:
    """cycle_count=4 is below threshold — should not raise."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(
        session_id="cb-test",
        agent_id="test-agent",
        cycle_count=MAX_CYCLES - 1,  # = 4
    )
    updated = agent._check_circuit_breaker(state)
    assert updated.cycle_count == MAX_CYCLES, (
        "After successful check, cycle_count should increment by 1"
    )


def test_circuit_breaker_increments_cycle_count() -> None:
    """_check_circuit_breaker returns a new state with cycle_count+1."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(session_id="s", agent_id="a", cycle_count=0)

    updated = agent._check_circuit_breaker(state)
    assert updated.cycle_count == 1
    assert state.cycle_count == 0  # original unchanged (immutable)


def test_circuit_breaker_at_zero_cycles() -> None:
    """cycle_count=0: should pass and return count=1."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(session_id="s", agent_id="a", cycle_count=0)
    updated = agent._check_circuit_breaker(state)
    assert updated.cycle_count == 1


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_circuit_breaker_below_threshold_range(count: int) -> None:
    """All counts below MAX_CYCLES should pass the circuit breaker."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(session_id="s", agent_id="a", cycle_count=count)
    updated = agent._check_circuit_breaker(state)
    assert updated.cycle_count == count + 1


def test_epistemic_state_increment_cycle() -> None:
    """EpistemicState.increment_cycle() returns a new state with count+1."""
    state = EpistemicState(session_id="s", agent_id="a", cycle_count=2)
    incremented = state.increment_cycle()
    assert incremented.cycle_count == 3
    assert state.cycle_count == 2  # original unchanged


# ── 3. Hallucination risk threshold ──────────────────────────────────────────


def test_hallucination_risk_threshold() -> None:
    """
    When hallucination_risk_score > 0.7, the state must auto-flag
    requires_human_review=True and add 'hallucination_risk' to flags.

    From AGENTS.md §5.3 and TESTING_CICD.md §5.2.
    """
    state = EpistemicState(
        session_id="h-test",
        agent_id="test-agent",
        hallucination_risk_score=0.85,  # above 0.7 threshold
    )
    assert state.requires_human_review is True, (
        "requires_human_review must be True when hallucination_risk_score > 0.7"
    )
    assert "hallucination_risk" in state.flags, (
        "flags must contain 'hallucination_risk' when score > 0.7"
    )


def test_hallucination_risk_at_threshold_not_flagged() -> None:
    """
    hallucination_risk_score = 0.7 exactly is NOT above threshold — should not flag.
    (Threshold is strictly greater than 0.7.)
    """
    state = EpistemicState(
        session_id="h-test",
        agent_id="test-agent",
        hallucination_risk_score=0.7,
    )
    assert state.requires_human_review is False
    assert "hallucination_risk" not in state.flags


def test_hallucination_risk_below_threshold() -> None:
    """hallucination_risk_score = 0.5 should not flag for review."""
    state = EpistemicState(
        session_id="h-test",
        agent_id="test-agent",
        hallucination_risk_score=0.5,
    )
    assert state.requires_human_review is False
    assert "hallucination_risk" not in state.flags


def test_hallucination_risk_at_zero() -> None:
    """hallucination_risk_score = 0.0 (default) should not flag."""
    state = EpistemicState(session_id="s", agent_id="a")
    assert state.hallucination_risk_score == 0.0
    assert state.requires_human_review is False


def test_hallucination_check_method_sets_flag() -> None:
    """
    BaseAgent._check_hallucination_risk() sets requires_human_review when
    score exceeds threshold.
    """

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(
        session_id="s",
        agent_id="a",
        hallucination_risk_score=0.0,
        requires_human_review=False,
    )
    # Manually set score above threshold (bypassing validator)
    state_with_risk = state.model_copy(
        update={"hallucination_risk_score": 0.95}
    )
    updated = agent._check_hallucination_risk(state_with_risk)
    assert updated.requires_human_review is True
    assert "hallucination_risk" in updated.flags


# ── 4. APPROVED state allows semantic layer writes ────────────────────────────


def test_approved_state_allows_write() -> None:
    """
    EpistemicState.can_write_to_semantic_layer() must return True when
    guardian_verdict == APPROVED.

    From AGENTS.md §6.2:
        'Write: WorkingMemory → [GuardianCheck] → SemanticLayer'
    """
    state = EpistemicState(
        session_id="w-test",
        agent_id="test-agent",
        guardian_verdict=GuardianVerdict.APPROVED,
    )
    assert state.can_write_to_semantic_layer() is True, (
        "APPROVED state must allow semantic layer writes"
    )


def test_approved_state_passes_agent_guard() -> None:
    """BaseAgent._assert_guardian_approved() should not raise for APPROVED state."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(
        session_id="s",
        agent_id="a",
        guardian_verdict=GuardianVerdict.APPROVED,
    )
    # Should not raise
    agent._assert_guardian_approved(state)


# ── 5. PENDING verdict blocks semantic layer writes ───────────────────────────


def test_pending_verdict_blocks_write() -> None:
    """
    EpistemicState.can_write_to_semantic_layer() must return False when
    guardian_verdict == PENDING.

    PENDING is the initial default — no write is ever permitted without
    an explicit APPROVED verdict from the GovernanceAgent.
    """
    state = EpistemicState(
        session_id="w-test",
        agent_id="test-agent",
        guardian_verdict=GuardianVerdict.PENDING,
    )
    assert state.can_write_to_semantic_layer() is False, (
        "PENDING state must block semantic layer writes"
    )


def test_rejected_verdict_blocks_write() -> None:
    """REJECTED verdict must also block writes."""
    state = EpistemicState(
        session_id="s",
        agent_id="a",
        guardian_verdict=GuardianVerdict.REJECTED,
    )
    assert state.can_write_to_semantic_layer() is False


def test_escalation_verdict_blocks_write() -> None:
    """REQUIRES_ESCALATION verdict must also block writes."""
    state = EpistemicState(
        session_id="s",
        agent_id="a",
        guardian_verdict=GuardianVerdict.REQUIRES_ESCALATION,
    )
    assert state.can_write_to_semantic_layer() is False


def test_pending_verdict_raises_in_agent_guard() -> None:
    """
    BaseAgent._assert_guardian_approved() must raise GuardianBlockedError
    when verdict is PENDING (or any non-APPROVED verdict).
    """

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")

    for verdict in (
        GuardianVerdict.PENDING,
        GuardianVerdict.REJECTED,
        GuardianVerdict.REQUIRES_ESCALATION,
    ):
        state = EpistemicState(
            session_id="s",
            agent_id="a",
            guardian_verdict=verdict,
        )
        with pytest.raises(GuardianBlockedError) as exc_info:
            agent._assert_guardian_approved(state)

        assert exc_info.value.verdict_status == str(verdict) or str(verdict) in str(
            exc_info.value
        ), f"GuardianBlockedError should reference verdict {verdict}"


# ── Additional invariant tests ────────────────────────────────────────────────


def test_reasoning_steps_append() -> None:
    """_add_reasoning_step returns a new state with the step appended."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")
    state = EpistemicState(session_id="s", agent_id="a")

    updated = agent._add_reasoning_step(
        state,
        node="TestNode",
        action="TEST_ACTION",
        inputs={"key": "value"},
        outputs={"result": 42},
    )

    assert len(updated.reasoning_steps) == 1
    assert len(state.reasoning_steps) == 0  # original unchanged
    step = updated.reasoning_steps[0]
    assert step.node == "TestNode"
    assert step.action == "TEST_ACTION"
    assert step.inputs == {"key": "value"}
    assert step.outputs == {"result": 42}


def test_disorder_domain_detected() -> None:
    """BaseAgent._is_disorder_domain() correctly identifies DISORDER."""

    class _ConcreteAgent(BaseAgent):
        async def process(self, state: EpistemicState) -> EpistemicState:
            return state

    agent = _ConcreteAgent(agent_id="test-agent")

    for domain, expected in [
        (CynefinDomain.DISORDER, True),
        (CynefinDomain.CLEAR, False),
        (CynefinDomain.COMPLICATED, False),
        (CynefinDomain.COMPLEX, False),
        (CynefinDomain.CHAOTIC, False),
    ]:
        state = EpistemicState(session_id="s", agent_id="a", cynefin_domain=domain)
        assert agent._is_disorder_domain(state) is expected


def test_epistemic_state_confidence_bounds() -> None:
    """Confidence fields must be constrained to [0.0, 1.0]."""
    with pytest.raises(Exception):
        EpistemicState(
            session_id="s",
            agent_id="a",
            overall_confidence=1.5,  # out of bounds
        )
    with pytest.raises(Exception):
        EpistemicState(
            session_id="s",
            agent_id="a",
            hallucination_risk_score=-0.1,  # out of bounds
        )
    with pytest.raises(Exception):
        EpistemicState(
            session_id="s",
            agent_id="a",
            domain_confidence=2.0,  # out of bounds
        )


def test_causal_edge_no_self_loop() -> None:
    """CausalEdge must reject self-loop (source == target)."""
    from src.semantic_layer.models import CausalEdge, CausalObjectRef, EdgeType

    same_id = uuid4()
    with pytest.raises(Exception):
        CausalEdge(
            source=CausalObjectRef(id=same_id, object_type="CausalEntity"),
            target=CausalObjectRef(id=same_id, object_type="CausalEntity"),
            edge_type=EdgeType.CAUSES,
            confidence=0.9,
            guardian_verdict_ref=uuid4(),
            domain="devops",
        )


def test_audit_entry_is_frozen() -> None:
    """AuditEntry must be frozen (immutable after construction)."""
    from src.semantic_layer.models import AuditEntry

    entry = AuditEntry(
        agent_id="test",
        action_type="TEST",
        payload_hash="abc123",
        guardian_verdict=GuardianVerdict.APPROVED,
    )
    with pytest.raises(Exception):  # ValidationError or TypeError
        entry.agent_id = "tampered"  # type: ignore[misc]

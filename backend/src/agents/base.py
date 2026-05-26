"""
BaseAgent — abstract base class for all platform agents.

Design principles (from AGENTS.md §1):
  - Agents process EpistemicState in → EpistemicState out
  - Every tool/skill call is logged to tool_call_log
  - Every reasoning step is appended to reasoning_steps
  - Guardian check is called before any write to the semantic layer
  - CircuitBreakerError is raised when cycleCount >= 5
  - structlog logs every action with full context

Subclasses must implement:
  async def process(self, state: EpistemicState) -> EpistemicState
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import structlog

from src.exceptions import CircuitBreakerError, GuardianBlockedError
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
    ReasoningStep,
    ToolCall,
)

# Maximum reasoning cycles before mandatory human escalation (cynepic-guardian spec)
MAX_CYCLES: int = 5

# Hallucination risk threshold above which the state is flagged for review
HALLUCINATION_THRESHOLD: float = 0.7

log = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for all causal platform agents.

    Provides:
      - process() abstract method contract
      - _call_skill() with timing and logging
      - _add_reasoning_step() for state mutation
      - _check_circuit_breaker() for cycle enforcement
      - _assert_guardian_approved() for write-guard
      - Structured log context on every action
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._log = log.bind(agent_id=agent_id)

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Process an EpistemicState and return a new EpistemicState.

        Subclasses must:
          1. Call _check_circuit_breaker(state) at entry
          2. Perform their reasoning / skill calls
          3. Return a new (immutable copy) EpistemicState
        """

    # ── Skill call logging ────────────────────────────────────────────────────

    async def _call_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> tuple[dict[str, Any], EpistemicState]:
        """
        Invoke a named skill, measure duration, log the call, and append to
        the state's tool_call_log.

        Returns (outputs, updated_state).

        The actual skill execution must be provided by the subclass via
        _execute_skill().  This method handles the telemetry wrapper.
        """
        start = time.perf_counter()
        success = True
        error_msg: str | None = None
        outputs: dict[str, Any] = {}

        self._log.info(
            "agent.skill.starting",
            skill=skill_name,
            session_id=state.session_id,
            cynefin_domain=state.cynefin_domain,
            cycle_count=state.cycle_count,
        )

        try:
            outputs = await self._execute_skill(skill_name, inputs, state)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            self._log.error(
                "agent.skill.failed",
                skill=skill_name,
                session_id=state.session_id,
                error=error_msg,
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            tool_call = ToolCall(
                skill_name=skill_name,
                inputs=inputs,
                outputs=outputs,
                duration_ms=round(duration_ms, 2),
                timestamp=datetime.now(timezone.utc),
                success=success,
                error=error_msg,
            )
            state = state.model_copy(
                update={"tool_call_log": [*state.tool_call_log, tool_call]}
            )
            self._log.info(
                "agent.skill.completed",
                skill=skill_name,
                session_id=state.session_id,
                duration_ms=round(duration_ms, 2),
                success=success,
            )

        return outputs, state

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        """
        Default skill dispatcher.  Subclasses should override this method to
        route to their specific skill implementations.

        The base implementation raises NotImplementedError for unknown skills.
        """
        raise NotImplementedError(
            f"Agent '{self.agent_id}' has no implementation for skill '{skill_name}'. "
            "Override _execute_skill() in the subclass."
        )

    # ── Reasoning step tracking ───────────────────────────────────────────────

    def _add_reasoning_step(
        self,
        state: EpistemicState,
        node: str,
        action: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> EpistemicState:
        """
        Append a reasoning step to the state's reasoning_steps list.

        Returns a new EpistemicState (immutable update pattern).
        """
        step = ReasoningStep(
            node=node,
            action=action,
            inputs=inputs,
            outputs=outputs,
            timestamp=datetime.now(timezone.utc),
        )
        updated = state.model_copy(
            update={"reasoning_steps": [*state.reasoning_steps, step]}
        )
        self._log.debug(
            "agent.reasoning_step",
            node=node,
            action=action,
            session_id=state.session_id,
            step_index=len(updated.reasoning_steps),
        )
        return updated

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def _check_circuit_breaker(self, state: EpistemicState) -> EpistemicState:
        """
        Enforce the maximum 5-cycle circuit breaker rule.

        If cycleCount >= MAX_CYCLES, raises CircuitBreakerError (which must be
        caught by the orchestrator and routed to human escalation).

        Otherwise increments cycleCount and returns the updated state.
        """
        if state.cycle_count >= MAX_CYCLES:
            self._log.error(
                "agent.circuit_breaker.open",
                agent_id=self.agent_id,
                session_id=state.session_id,
                cycle_count=state.cycle_count,
                max_cycles=MAX_CYCLES,
            )
            raise CircuitBreakerError(
                cycle_count=state.cycle_count,
                session_id=state.session_id,
            )

        new_state = state.increment_cycle()
        self._log.debug(
            "agent.cycle.incremented",
            session_id=state.session_id,
            cycle_count=new_state.cycle_count,
        )
        return new_state

    # ── Guardian guard ────────────────────────────────────────────────────────

    def _assert_guardian_approved(self, state: EpistemicState) -> None:
        """
        Assert that the current EpistemicState carries an APPROVED Guardian verdict.

        Raises GuardianBlockedError if the verdict is not APPROVED.

        Call this before every semantic layer write.
        """
        if not state.can_write_to_semantic_layer():
            self._log.warning(
                "agent.guardian.blocked",
                agent_id=self.agent_id,
                session_id=state.session_id,
                verdict=state.guardian_verdict,
            )
            raise GuardianBlockedError(
                reason="Semantic layer write attempted without APPROVED Guardian verdict",
                verdict_status=state.guardian_verdict,
            )

    # ── Hallucination risk check ──────────────────────────────────────────────

    def _check_hallucination_risk(self, state: EpistemicState) -> EpistemicState:
        """
        If hallucination_risk_score > HALLUCINATION_THRESHOLD, set
        requires_human_review=True and add the "hallucination_risk" flag.

        The model_validator on EpistemicState handles this automatically,
        but this method provides an explicit call site for agent use.
        """
        if state.hallucination_risk_score > HALLUCINATION_THRESHOLD:
            updated_flags = (
                state.flags
                if "hallucination_risk" in state.flags
                else [*state.flags, "hallucination_risk"]
            )
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": updated_flags,
                }
            )
            self._log.warning(
                "agent.hallucination_risk.high",
                agent_id=self.agent_id,
                session_id=state.session_id,
                hallucination_risk_score=state.hallucination_risk_score,
                threshold=HALLUCINATION_THRESHOLD,
            )
        return state

    # ── Disorder protocol ─────────────────────────────────────────────────────

    def _is_disorder_domain(self, state: EpistemicState) -> bool:
        """Return True when the Cynefin domain is DISORDER."""
        return state.cynefin_domain == CynefinDomain.DISORDER

    # ── Log context helper ────────────────────────────────────────────────────

    def _bind_log(self, state: EpistemicState) -> structlog.BoundLogger:
        """Return a structlog logger bound with full EpistemicState context."""
        return self._log.bind(
            session_id=state.session_id,
            cynefin_domain=state.cynefin_domain,
            guardian_verdict=state.guardian_verdict,
            cycle_count=state.cycle_count,
            hallucination_risk=state.hallucination_risk_score,
        )

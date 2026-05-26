"""
DomainExpertElicitationAgent — structured elicitation of causal knowledge from domain experts.

Responsibilities (AGENTS.md §2.1 DomainExpertElicitationAgent):
  - Conduct structured assumption elicitation sessions
  - Guide experts through variable identification, direction, strength estimation
  - Convert elicited knowledge into typed Assumption and EdgeProposal objects
  - Surface expert disagreements and conflicting prior beliefs
  - COMPLEX domain: this agent is activated when CausalDiscovery cannot resolve
    structure from data alone

Implemented skills:
  assumption_elicit       — guide expert through structured elicitation
  prior_encode            — convert qualitative expert judgment to quantified prior
  disagreement_surface    — identify conflicts between expert and data-driven proposals
  semantic_layer_read     — load existing assumptions for comparison
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.exceptions import CircuitBreakerError
from src.semantic_layer.models import (
    Assumption,
    AssumptionType,
    CynefinDomain,
    ElicitedFrom,
    EpistemicState,
    Recommendation,
    ReviewStatus,
    UserRef,
)

log = structlog.get_logger(__name__)


class DomainExpertElicitationAgent(BaseAgent):
    """
    Conduct structured causal knowledge elicitation from domain experts.

    This agent is critical for COMPLEX domain problems where data alone
    cannot resolve the causal structure. It mediates between the platform's
    data-driven proposals and human expert knowledge.

    Usage::

        agent = DomainExpertElicitationAgent()
        state = await agent.process(state)
        # state.active_assumptions contains elicited assumptions
        # state.proposed_edges may be updated with expert-informed confidence
    """

    def __init__(self, agent_id: str = "domain-expert-elicitation-agent") -> None:
        super().__init__(agent_id=agent_id)

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Run structured elicitation pipeline.

        Steps:
          1. Circuit breaker + hallucination checks
          2. Load existing assumptions for context
          3. Elicit new assumptions from expert input
          4. Encode qualitative judgments as quantified priors
          5. Surface disagreements between expert and data-driven proposals
          6. Return updated state with new assumptions
        """
        try:
            state = self._check_circuit_breaker(state)
        except CircuitBreakerError:
            return state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "circuit_breaker"],
                    "draft_explanation": (
                        "Circuit breaker opened: maximum reasoning cycles reached. "
                        "Human escalation required."
                    ),
                }
            )
        state = self._check_hallucination_risk(state)
        if state.cynefin_domain == CynefinDomain.CHAOTIC:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "chaotic_elicitation_skip"],
                    "draft_explanation": (
                        "CHAOTIC domain: Expert elicitation suspended. "
                        "Stabilise situation before structured knowledge capture."
                    ),
                }
            )
            return state

        # Step 1: Load existing assumptions
        existing_out, state = await self._call_skill(
            "semantic_layer_read",
            {
                "fragment_id": state.active_graph_fragment_id,
                "object_type": "assumptions",
            },
            state,
        )
        existing_assumptions: list[dict[str, Any]] = existing_out.get("assumptions", [])

        # Step 2: Elicit new assumptions
        elicit_inputs = {
            "proposed_edges": [e.model_dump() for e in state.proposed_edges],
            "existing_assumptions": existing_assumptions,
            "domain": state.cynefin_domain,
        }
        elicit_out, state = await self._call_skill(
            "assumption_elicit",
            elicit_inputs,
            state,
        )

        new_assumptions_raw: list[dict[str, Any]] = elicit_out.get("assumptions", [])

        new_assumptions: list[Assumption] = []
        for raw in new_assumptions_raw:
            try:
                assumption = Assumption(
                    statement=raw.get("statement", "Expert-elicited assumption"),
                    assumption_type=AssumptionType(raw.get("type", "DOMAIN")),
                    justification=raw.get("justification", "Domain expert judgment"),
                    elicited_from=ElicitedFrom.DOMAIN_EXPERT,
                    elicited_by=UserRef(id=raw.get("expert_id", "unknown")),
                    review_status=ReviewStatus.PENDING,
                    domain=raw.get("domain", "general"),
                )
                new_assumptions.append(assumption)
            except (ValueError, KeyError) as exc:
                self._log.warning(
                    "elicitation.assumption_parse_failed",
                    error=str(exc),
                    raw=raw,
                )

        # Step 3: Encode priors from qualitative judgments
        for assumption in new_assumptions:
            prior_out, state = await self._call_skill(
                "prior_encode",
                {
                    "statement": assumption.statement,
                    "assumption_type": assumption.assumption_type,
                },
                state,
            )

        # Step 4: Surface disagreements
        disagreement_out, state = await self._call_skill(
            "disagreement_surface",
            {
                "new_assumptions": [a.model_dump() for a in new_assumptions],
                "proposed_edges": [e.model_dump() for e in state.proposed_edges],
                "existing_assumptions": existing_assumptions,
            },
            state,
        )
        disagreements: list[str] = disagreement_out.get("disagreements", [])

        # Build recommendations
        recommendations: list[Recommendation] = []
        new_flags = list(state.flags)

        if new_assumptions:
            recommendations.append(
                Recommendation(
                    rank=1,
                    action=f"{len(new_assumptions)} new assumption(s) elicited — pending review",
                    rationale=(
                        "Domain expert knowledge captured in structured format. "
                        "All assumptions require governance review before acceptance."
                    ),
                    confidence=0.7,
                    risk_tier="MEDIUM",
                )
            )

        if disagreements:
            new_flags.append("expert_data_disagreement")
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action="Resolve expert-data disagreements before proceeding",
                    rationale=f"Disagreements: {'; '.join(disagreements[:3])}.",
                    confidence=0.5,
                    risk_tier="HIGH",
                )
            )

        if state.cynefin_domain == CynefinDomain.COMPLEX:
            new_flags.append("complex_domain_elicitation_done")

        state = state.model_copy(
            update={
                "active_assumptions": [*state.active_assumptions, *new_assumptions],
                "flags": new_flags,
                "requires_human_review": True,
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": (
                    f"Expert elicitation complete. "
                    f"{len(new_assumptions)} new assumption(s) captured. "
                    f"{len(disagreements)} disagreement(s) with data-driven proposals. "
                    "All elicited assumptions pending governance review."
                ),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="DomainExpertElicitation",
            action="ELICIT_ASSUMPTIONS",
            inputs=elicit_inputs,
            outputs={
                "new_assumption_count": len(new_assumptions),
                "disagreement_count": len(disagreements),
                "existing_assumption_count": len(existing_assumptions),
            },
        )

        self._log.info(
            "domain_expert_elicitation.complete",
            session_id=state.session_id,
            new_assumptions=len(new_assumptions),
            disagreements=len(disagreements),
        )
        return state

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        if skill_name == "assumption_elicit":
            return await self._skill_assumption_elicit(inputs)
        if skill_name == "prior_encode":
            return await self._skill_prior_encode(inputs)
        if skill_name == "disagreement_surface":
            return await self._skill_disagreement_surface(inputs)
        if skill_name == "semantic_layer_read":
            return await self._skill_semantic_layer_read(inputs)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_assumption_elicit(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Conduct structured assumption elicitation.

        Production: drives a guided interview UI (UIX.md US-23),
        presenting existing edges and asking the expert to confirm,
        challenge, or add assumptions.
        Stub: returns empty (awaiting expert input).
        """
        return {"assumptions": []}

    async def _skill_prior_encode(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Convert qualitative expert judgment to quantified prior.

        Production: maps verbal probability ("likely", "very likely") to
        Beta distribution parameters for Bayesian estimation.
        Stub: returns neutral prior.
        """
        return {"prior_alpha": 1.0, "prior_beta": 1.0}

    async def _skill_disagreement_surface(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Identify conflicts between expert and data-driven proposals.

        Production: compares expert assumptions with statistically
        derived edge proposals to find directional or structural conflicts.
        Stub: returns no disagreements.
        """
        return {"disagreements": []}

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        return {"assumptions": []}

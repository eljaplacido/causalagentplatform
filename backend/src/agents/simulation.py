"""
SimulationAgent — runs causal effect estimation over approved graph fragments.

Responsibilities (AGENTS.md §2.1 SimulationAgent):
  - Load APPROVED graph fragments from the semantic layer
  - Run causal-core identification and estimation
  - Compute ATE with confidence intervals
  - Validate identification strategy against active assumptions
  - Store results in simulation_results on EpistemicState
  - NEVER modify graph assets — read-only access to semantic layer

Implemented skills:
  semantic_layer_read      — load approved graph fragment
  identification_check     — validate causal effect identifiability (causal-core)
  effect_estimate          — compute ATE via causal-core
  sensitivity_analysis     — Rosenbaum bounds / E-value computation

EpistemicState constraints enforced:
  - require_approved_fragment: Only APPROVED fragments usable
  - guardian_verdict_required: All outputs Guardian-gated
  - audit_log_required: Every estimation logged
  - circuit_breaker_enforced: Max 5 cycles
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.semantic_layer.graph_context import load_fragment_with_edges
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    Recommendation,
    SimulationResult,
)
from src.skills import (
    EffectEstimationError,
    IdentificationError,
    SensitivityAnalysisError,
    effect_estimate,
    identification_check,
    sensitivity_analysis,
)

log = structlog.get_logger(__name__)

_MAX_FRAGMENT_EDGES = 500


class SimulationAgent(BaseAgent):
    """
    Run causal effect estimation over approved graph fragments.

    Only APPROVED graph fragments can be used.  Draft or deprecated fragments
    are rejected at the semantic_layer_read step.

    Usage::

        agent = SimulationAgent()
        state = await agent.process(state)
        # state.simulation_results contains ATE estimates
    """

    def __init__(self, agent_id: str = "simulation-agent") -> None:
        super().__init__(agent_id=agent_id)

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Run simulation pipeline.

        Steps:
          1. Circuit breaker + hallucination checks
          2. Load approved graph fragment
          3. Identification check (backdoor criterion)
          4. Effect estimation (ATE)
          5. Sensitivity analysis (E-value / Rosenbaum)
          6. Generate explanation
        """
        state = self._check_circuit_breaker(state)
        state = self._check_hallucination_risk(state)

        if state.cynefin_domain == CynefinDomain.CHAOTIC:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "chaotic_simulation_skip"],
                    "draft_explanation": (
                        "CHAOTIC domain: Simulation suspended. "
                        "Stabilise situation before running causal estimation."
                    ),
                }
            )
            return state

        if not state.active_graph_fragment_id:
            state = state.model_copy(
                update={
                    "draft_explanation": (
                        "No active graph fragment. "
                        "Select an APPROVED fragment before running simulations."
                    ),
                }
            )
            return state

        # Step 1: Load approved fragment
        fragment_out, state = await self._call_skill(
            "semantic_layer_read",
            {
                "fragment_id": state.active_graph_fragment_id,
                "require_approved": True,
            },
            state,
        )
        fragment = fragment_out.get("fragment", {})
        if not fragment:
            state = state.model_copy(
                update={
                    "draft_explanation": "Fragment not found or not APPROVED.",
                    "flags": [*state.flags, "fragment_not_approved"],
                }
            )
            return state

        # Step 2: Identification check
        ident_out, state = await self._call_skill(
            "identification_check",
            {
                "fragment": fragment,
                "assumptions": [a.model_dump() for a in state.active_assumptions],
            },
            state,
        )
        identification_status = ident_out.get("status", "UNIDENTIFIED")

        if identification_status == "UNIDENTIFIED":
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "unidentified_effect"],
                    "draft_explanation": (
                        "Causal effect is not identified under current assumptions. "
                        "Additional assumptions or instruments required."
                    ),
                }
            )
            state = self._add_reasoning_step(
                state,
                node="Simulation",
                action="IDENTIFICATION_FAILED",
                inputs={"fragment_id": state.active_graph_fragment_id},
                outputs={"status": identification_status},
            )
            return state

        # Step 3: Effect estimation
        estimate_out, state = await self._call_skill(
            "effect_estimate",
            {
                "fragment": fragment,
                "identification_strategy": ident_out.get("strategy", "backdoor"),
                "assumptions": [a.model_dump() for a in state.active_assumptions],
            },
            state,
        )

        if estimate_out.get("ate") is None:
            # No estimate (no data, a design check failed, or a typed refusal): say why and
            # stop. A simulation result without an estimate would be a fabricated number.
            detail = str(
                estimate_out.get("detail") or estimate_out.get("error") or "no estimate returned"
            )
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "no_effect_estimate"],
                    "draft_explanation": (
                        f"Identification: {identification_status}. No effect estimate: {detail}"
                    ),
                }
            )
            return self._add_reasoning_step(
                state,
                node="Simulation",
                action="ESTIMATE_REFUSED",
                inputs={"fragment_id": state.active_graph_fragment_id},
                outputs={
                    "status": estimate_out.get("status"),
                    "reason": estimate_out.get("refusal_reason"),
                    "detail": detail,
                },
            )

        ate = float(estimate_out["ate"])
        ci = list(estimate_out.get("confidence_interval") or [None, None])
        estimator = estimate_out.get("estimator", "unknown")

        # Step 4: Sensitivity analysis
        sensitivity_out, state = await self._call_skill(
            "sensitivity_analysis",
            {
                "ate": ate,
                "confidence_interval": ci,
                "assumptions": [a.model_dump() for a in state.active_assumptions],
            },
            state,
        )
        e_value = sensitivity_out.get("e_value")
        robust = sensitivity_out.get("robust_to_unmeasured_confounding")
        # The kernel computed its sensitivity analysis from the data (it knows the
        # outcome's scale); prefer it to the skill's scale-limited version.
        kernel_sens = (estimate_out.get("outcome") or {}).get("sensitivity") or {}
        if kernel_sens.get("e_value") is not None:
            e_value = kernel_sens["e_value"]
            e_ci = kernel_sens.get("e_value_ci")
            robust = None if e_ci is None else bool(e_ci >= 2.0)
        ci_ok = len(ci) == 2 and None not in ci
        ci_text = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci_ok else "unavailable"
        sensitivity_label = {True: "ROBUST", False: "SENSITIVE"}.get(robust, "UNASSESSED")

        from uuid import UUID

        fragment_uuid = UUID(str(state.active_graph_fragment_id))
        treatment_id = UUID(str(estimate_out.get("treatment_id", "00000000-0000-0000-0000-000000000000")))
        outcome_id = UUID(str(estimate_out.get("outcome_id", "00000000-0000-0000-0000-000000000000")))

        sim_result = SimulationResult(
            fragment_id=fragment_uuid,
            treatment_id=treatment_id,
            outcome_id=outcome_id,
            ate_estimate=ate,
            confidence_interval=[float(c) for c in ci] if ci_ok else [],
            identification_status=identification_status,
            sensitivity_label=sensitivity_label,
        )

        recommendations = [
            Recommendation(
                rank=1,
                action=f"Estimated ATE: {ate:.4f} (CI: {ci_text})",
                rationale=(
                    f"Estimated by {estimator}. "
                    f"E-value: {e_value or 'N/A'}. "
                    f"Sensitivity to unmeasured confounding: {sensitivity_label.lower()}."
                ),
                confidence=min(abs(ate), 1.0),
                risk_tier="LOW" if robust else "HIGH",
            )
        ]

        state = state.model_copy(
            update={
                "simulation_results": [*state.simulation_results, sim_result],
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": (
                    f"Simulation complete. ATE estimate: {ate:.4f}. "
                    f"Identification: {identification_status}. "
                    f"Sensitivity: {sensitivity_label.lower()}."
                ),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="Simulation",
            action="ESTIMATE_COMPLETE",
            inputs={"fragment_id": str(fragment_uuid)},
            outputs={
                "ate": ate,
                "ci": ci,
                "identification": identification_status,
                "e_value": e_value,
            },
        )

        self._log.info(
            "simulation.complete",
            session_id=state.session_id,
            ate=ate,
            identification=identification_status,
            robust=robust,
        )
        return state

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        if skill_name == "semantic_layer_read":
            return await self._skill_semantic_layer_read(inputs)
        if skill_name == "identification_check":
            return await self._skill_identification_check(inputs)
        if skill_name == "effect_estimate":
            return await self._skill_effect_estimate(inputs)
        if skill_name == "sensitivity_analysis":
            return await self._skill_sensitivity_analysis(inputs)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations (stubs for Phase 2 integration) ─────────────────

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Load an APPROVED graph fragment from the semantic layer.

        Production: queries SemanticLayerRegistry with approval_status filter.
        """
        fragment_id = inputs.get("fragment_id")
        if not fragment_id:
            return {"fragment": None}

        try:
            fragment = await load_fragment_with_edges(
                fragment_id,
                require_approved=bool(inputs.get("require_approved")),
            )
            return {"fragment": fragment}
        except Exception:
            return {"fragment": None}

    async def _skill_identification_check(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Check if the causal effect is identified under current assumptions.

        Delegates to causal-core identification (src/skills/identification_check.py).
        """
        try:
            return await identification_check(
                fragment=inputs.get("fragment", {}),
                assumptions=inputs.get("assumptions", []),
            )
        except IdentificationError as e:
            self._log.error("identification_check.failed", error=str(e))
            return {"status": "UNIDENTIFIED", "strategy": "none", "reason": str(e)}

    async def _skill_effect_estimate(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Estimate Average Treatment Effect (ATE).

        Delegates to causal-core estimation (src/skills/effect_estimate.py).
        """
        try:
            return await effect_estimate(
                fragment=inputs.get("fragment", {}),
                identification_strategy=inputs.get("identification_strategy", "backdoor"),
                assumptions=inputs.get("assumptions", []),
                data=inputs.get("data"),  # Optional data for estimation
            )
        except EffectEstimationError as e:
            self._log.error("effect_estimate.failed", error=str(e))
            return {
                "ate": None,
                "confidence_interval": [None, None],
                "estimator": "failed",
                "treatment_id": None,
                "outcome_id": None,
                "error": str(e),
            }

    async def _skill_sensitivity_analysis(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Run Rosenbaum / E-value sensitivity analysis.

        Production: computes E-value and Rosenbaum gamma bounds.
        """
        try:
            return await sensitivity_analysis(
                ate=inputs.get("ate"),
                confidence_interval=inputs.get("confidence_interval", [None, None]),
                assumptions=inputs.get("assumptions", []),
                data=inputs.get("data"),
            )
        except SensitivityAnalysisError as e:
            self._log.error("sensitivity_analysis.failed", error=str(e))
            return {
                "e_value": None,
                "rosenbaum_gamma": None,
                "robust_to_unmeasured_confounding": False,
                "method": "failed",
                "error": str(e),
            }

"""
ChangeImpactAgent — assess causal impact of system changes on downstream outcomes.

Responsibilities (AGENTS.md §2.1 ChangeImpactAgent):
  - Trace the causal propagation of a proposed change through the graph
  - Estimate direct and indirect effects on downstream outcomes
  - Surface affected services, SLOs, and operational processes
  - Produce risk-tiered impact assessment for human review
  - Designed for the DevOps vertical (deployment impact, config change impact)

Implemented skills:
  semantic_layer_read      — load relevant graph context
  causal_trace             — trace downstream causal paths from a change point
  effect_estimate          — estimate change impact on each downstream outcome
  slo_impact_check         — cross-reference impact with SLO thresholds
  explanation_generate     — produce impact narrative for stakeholders
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.exceptions import CircuitBreakerError
from src.semantic_layer.graph_context import load_fragment_with_edges
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    Recommendation,
)

log = structlog.get_logger(__name__)


class ChangeImpactAgent(BaseAgent):
    """
    Assess the causal impact of system changes on downstream outcomes.

    Maps to the DevOps vertical and the Platform Engineer / SRE personas.
    Trace change propagation through the causal graph and estimate
    effects on SLOs and dependent services.

    Usage::

        agent = ChangeImpactAgent()
        state = await agent.process(state)
        # state.recommendations contains tiered impact assessment
        # state.draft_explanation contains stakeholder narrative
    """

    def __init__(self, agent_id: str = "change-impact-agent") -> None:
        super().__init__(agent_id=agent_id)

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Run change impact assessment.

        Steps:
          1. Circuit breaker + hallucination checks
          2. Load graph context for the change domain
          3. Trace causal propagation from the change point
          4. Estimate effect on each downstream outcome
          5. Cross-reference with SLO thresholds
          6. Produce tiered impact assessment
        """
        try:
            state = self._check_circuit_breaker(state)
        except CircuitBreakerError:
            return state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "circuit_breaker"],
                    "draft_explanation": (
                        "Circuit breaker opened during impact assessment. "
                        "Escalating to human reviewer."
                    ),
                }
            )
        state = self._check_hallucination_risk(state)

        if state.cynefin_domain == CynefinDomain.CHAOTIC:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "chaotic_impact_skip"],
                    "draft_explanation": (
                        "CHAOTIC domain: Automated impact assessment unreliable. "
                        "Routing to human incident commander for manual assessment."
                    ),
                }
            )
            return state

        # Step 1: Load graph context
        context_out, state = await self._call_skill(
            "semantic_layer_read",
            {
                "fragment_id": state.active_graph_fragment_id,
                "domain": state.cynefin_domain,
            },
            state,
        )
        graph_context = context_out.get("graph_context", {})

        # Step 2: Trace causal propagation
        trace_out, state = await self._call_skill(
            "causal_trace",
            {
                "change_description": state.draft_explanation or "system change",
                "graph_context": graph_context,
                "proposed_edges": [e.model_dump() for e in state.proposed_edges],
            },
            state,
        )
        affected_paths: list[dict[str, Any]] = trace_out.get("affected_paths", [])
        downstream_outcomes: list[str] = trace_out.get("downstream_outcomes", [])

        # Step 3: Estimate effect per downstream outcome
        impact_estimates: list[dict[str, Any]] = []
        for outcome in downstream_outcomes:
            est_out, state = await self._call_skill(
                "effect_estimate",
                {
                    "change_context": graph_context,
                    "target_outcome": outcome,
                    "affected_paths": affected_paths,
                },
                state,
            )
            impact_estimates.append({
                "outcome": outcome,
                "heuristic_score": est_out.get("heuristic_score", 0.0),
                "direction": est_out.get("direction", "unknown"),
                "confidence": est_out.get("confidence", 0.0),
            })

        # Step 4: SLO impact check
        slo_out, state = await self._call_skill(
            "slo_impact_check",
            {
                "impact_estimates": impact_estimates,
                "domain": state.cynefin_domain,
            },
            state,
        )
        slo_violations: list[str] = slo_out.get("slo_violations", [])
        overall_risk_tier: str = slo_out.get("overall_risk_tier", "MEDIUM")

        # Build recommendations
        recommendations: list[Recommendation] = []
        new_flags = list(state.flags)

        if slo_violations:
            new_flags.append("slo_impact_risk")
            recommendations.append(
                Recommendation(
                    rank=1,
                    action=f"SLO impact detected: {len(slo_violations)} threshold(s) at risk",
                    rationale=(
                        f"Potential SLO violations: {', '.join(slo_violations[:5])}. "
                        "Review before proceeding with change."
                    ),
                    confidence=0.8,
                    risk_tier="HIGH" if len(slo_violations) >= 2 else "MEDIUM",
                )
            )

        for i, impact in enumerate(impact_estimates):
            score = abs(impact["heuristic_score"])
            risk = "HIGH" if score > 0.5 else "MEDIUM" if score > 0.1 else "LOW"
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action=(
                        f"Impact on {impact['outcome']}: {impact['direction']} "
                        f"(heuristic path score {impact['heuristic_score']:.2f}, not an effect "
                        "estimate)"
                    ),
                    rationale=f"Confidence: {impact['confidence']:.0%}. Via {len(affected_paths)} causal path(s).",
                    confidence=impact["confidence"],
                    risk_tier=risk,
                )
            )

        if not impact_estimates:
            recommendations.append(
                Recommendation(
                    rank=1,
                    action="No downstream outcomes identified in current graph context",
                    rationale="Graph fragment may not cover the affected domain. Manual assessment recommended.",
                    confidence=0.3,
                    risk_tier="MEDIUM",
                )
            )

        requires_human = state.requires_human_review or bool(slo_violations) or overall_risk_tier in ("HIGH", "CRITICAL")

        explanation_parts = [
            "Change impact assessment complete.",
            f"{len(affected_paths)} causal path(s) traced.",
            f"{len(downstream_outcomes)} downstream outcome(s) evaluated.",
        ]
        if slo_violations:
            explanation_parts.append(
                f"{len(slo_violations)} potential SLO violation(s) identified."
            )
        explanation_parts.append(f"Overall risk tier: {overall_risk_tier}.")

        state = state.model_copy(
            update={
                "flags": new_flags,
                "requires_human_review": requires_human,
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": " ".join(explanation_parts),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="ChangeImpact",
            action="IMPACT_ASSESSMENT",
            inputs={
                "fragment_id": state.active_graph_fragment_id,
                "proposed_edge_count": len(state.proposed_edges),
            },
            outputs={
                "affected_paths": len(affected_paths),
                "downstream_outcomes": len(downstream_outcomes),
                "slo_violations": len(slo_violations),
                "risk_tier": overall_risk_tier,
            },
        )

        self._log.info(
            "change_impact.complete",
            session_id=state.session_id,
            affected_paths=len(affected_paths),
            downstream_outcomes=len(downstream_outcomes),
            slo_violations=len(slo_violations),
            risk_tier=overall_risk_tier,
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
        if skill_name == "causal_trace":
            return await self._skill_causal_trace(inputs)
        if skill_name == "effect_estimate":
            return await self._skill_effect_estimate(inputs)
        if skill_name == "slo_impact_check":
            return await self._skill_slo_impact_check(inputs)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Load graph context for the change domain.

        Production: queries SemanticLayerRegistry.
        """
        fragment_id = inputs.get("fragment_id")
        if not fragment_id:
            return {"graph_context": {}}
        try:
            fragment = await load_fragment_with_edges(fragment_id, require_approved=False)
            if fragment is None:
                return {"graph_context": {}}
            exit_nodes = fragment.get("exit_nodes", [])
            outcome_nodes = [n for n in exit_nodes if n.get("object_type") == "Outcome"]
            return {
                "graph_context": {
                    "fragment_id": fragment.get("id"),
                    "fragment_name": fragment.get("name"),
                    "fragment_domain": fragment.get("domain"),
                    "entry_nodes": fragment.get("entry_nodes", []),
                    "exit_nodes": exit_nodes,
                    "outcome_nodes": outcome_nodes,
                    "edges": fragment.get("edges", []),
                    "edge_count": len(fragment.get("edges", [])),
                }
            }
        except Exception:
            return {"graph_context": {}}

    async def _skill_causal_trace(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Trace downstream causal paths from a change point.

        Production-hardened fallback: traces bounded downstream paths through the
        resolved fragment edge list.
        """
        graph_context = inputs.get("graph_context", {})
        edges: list[dict[str, Any]] = graph_context.get("edges", [])
        proposed_edges: list[dict[str, Any]] = inputs.get("proposed_edges", [])

        start_nodes = {
            str(edge.get("source", {}).get("id"))
            for edge in proposed_edges
            if edge.get("source", {}).get("id")
        }
        if not start_nodes:
            start_nodes = {
                str(node.get("id"))
                for node in graph_context.get("entry_nodes", [])
                if node.get("id")
            }

        outcome_nodes = {
            str(node.get("id"))
            for node in graph_context.get("outcome_nodes", [])
            if node.get("id")
        }
        if not outcome_nodes:
            outcome_nodes = {
                str(edge.get("target", {}).get("id"))
                for edge in edges
                if edge.get("target", {}).get("object_type") == "Outcome"
            }

        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            source_id = str(edge.get("source", {}).get("id"))
            adjacency.setdefault(source_id, []).append(edge)

        affected_paths: list[dict[str, Any]] = []
        downstream_outcomes: set[str] = set()
        for start_node in start_nodes:
            queue: list[tuple[str, list[str], list[dict[str, Any]]]] = [(start_node, [start_node], [])]
            while queue:
                current, nodes, path_edges = queue.pop(0)
                if len(path_edges) >= 4:
                    continue
                for edge in adjacency.get(current, []):
                    target_id = str(edge.get("target", {}).get("id"))
                    if target_id in nodes:
                        continue
                    next_nodes = [*nodes, target_id]
                    next_edges = [*path_edges, edge]
                    if target_id in outcome_nodes:
                        downstream_outcomes.add(target_id)
                        affected_paths.append(
                            {
                                "start_node": start_node,
                                "outcome": target_id,
                                "path": next_nodes,
                                "edge_confidences": [float(e.get("confidence", 0.0)) for e in next_edges],
                                "net_confidence": _path_confidence(next_edges),
                            }
                        )
                    queue.append((target_id, next_nodes, next_edges))

        return {
            "affected_paths": affected_paths,
            "downstream_outcomes": sorted(downstream_outcomes),
        }

    async def _skill_effect_estimate(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Estimate the effect of the change on a downstream outcome.

        A heuristic path score from traced edge confidences and the number of
        reinforcing paths -- labelled as such; it is not an effect estimate.
        """
        target_outcome = str(inputs.get("target_outcome", "unknown"))
        paths = [p for p in inputs.get("affected_paths", []) if str(p.get("outcome")) == target_outcome]
        if not paths:
            return {
                "heuristic_score": 0.0,
                "heuristic": True,
                "direction": "unknown",
                "confidence": 0.0,
            }

        strongest_path = max(float(p.get("net_confidence", 0.0)) for p in paths)
        aggregate_signal = sum(float(p.get("net_confidence", 0.0)) for p in paths)
        heuristic_score = min(1.0, round((strongest_path * 0.7) + (aggregate_signal * 0.15), 4))
        confidence = min(0.95, round(0.35 + strongest_path + (0.05 * min(len(paths), 3)), 4))

        lower_target = target_outcome.lower()
        direction = (
            "negative"
            if any(keyword in lower_target for keyword in ("latency", "error", "cpu", "memory", "timeout"))
            else "positive"
        )

        return {
            "heuristic_score": heuristic_score,
            "heuristic": True,
            "direction": direction,
            "confidence": confidence,
        }

    async def _skill_slo_impact_check(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Cross-reference impact estimates with SLO thresholds.

        Production-hardened fallback: classify common operational metrics against
        conservative heuristic thresholds until the full SLO registry is wired in.
        """
        impact_estimates: list[dict[str, Any]] = inputs.get("impact_estimates", [])
        slo_violations: list[str] = []
        overall_risk_tier = "LOW"

        for estimate in impact_estimates:
            outcome = str(estimate.get("outcome", "unknown"))
            magnitude = abs(float(estimate.get("heuristic_score", 0.0)))
            confidence = float(estimate.get("confidence", 0.0))
            lower_outcome = outcome.lower()

            if any(keyword in lower_outcome for keyword in ("latency", "error", "timeout", "availability")) and magnitude >= 0.25 or any(keyword in lower_outcome for keyword in ("cpu", "memory", "saturation")) and magnitude >= 0.4:
                slo_violations.append(f"{outcome} projected effect {magnitude:.2f}")

            if magnitude >= 0.6 or (magnitude >= 0.35 and confidence >= 0.75):
                overall_risk_tier = "HIGH"
            if magnitude >= 0.8:
                overall_risk_tier = "CRITICAL"

        if slo_violations and overall_risk_tier == "LOW":
            overall_risk_tier = "MEDIUM"

        return {
            "slo_violations": slo_violations,
            "overall_risk_tier": overall_risk_tier,
        }


def _path_confidence(path_edges: list[dict[str, Any]]) -> float:
    """Aggregate a path into a bounded confidence score."""
    if not path_edges:
        return 0.0
    score = 1.0
    for edge in path_edges:
        score *= max(0.05, min(1.0, float(edge.get("confidence", 0.0))))
    return round(score, 4)

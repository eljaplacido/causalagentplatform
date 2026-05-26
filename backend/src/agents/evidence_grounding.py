"""
EvidenceGroundingAgent — grounds causal claims in supporting evidence.

Responsibilities (AGENTS.md §2.1 EvidenceGroundingAgent):
  - Retrieve and rank evidence for proposed causal edges
  - Run statistical validity checks on evidence citations
  - Detect contradictory evidence across sources
  - Compute relevance scores for evidence-to-claim linkage
  - NEVER fabricate citations — flag insufficient evidence instead
  - Read-only access to semantic layer and evidence store

Implemented skills:
  evidence_link           — retrieve evidence for a causal claim
  statistical_validate    — run statistical validity check
  contradiction_detect    — detect contradictions in evidence set
  semantic_layer_read     — read existing evidence citations
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.exceptions import CircuitBreakerError
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    Recommendation,
)

log = structlog.get_logger(__name__)

_MIN_RELEVANCE_SCORE = 0.3
_CONTRADICTION_THRESHOLD = 0.7


class EvidenceGroundingAgent(BaseAgent):
    """
    Ground proposed causal edges in supporting evidence.

    Critical invariant: this agent NEVER fabricates citations.
    If evidence is insufficient, it flags the claim for manual review
    rather than generating plausible-sounding references.

    Usage::

        agent = EvidenceGroundingAgent()
        state = await agent.process(state)
        # state.evidence_citations contains linked evidence
        # state.flags may include 'insufficient_evidence'
    """

    def __init__(self, agent_id: str = "evidence-grounding-agent") -> None:
        super().__init__(agent_id=agent_id)

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Ground proposed edges in evidence.

        Steps:
          1. Circuit breaker + hallucination checks
          2. For each proposed edge, retrieve evidence
          3. Run statistical validity on each citation
          4. Detect contradictions across evidence set
          5. Score overall evidence strength
          6. Flag insufficiently grounded claims
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

        if not state.proposed_edges:
            state = state.model_copy(
                update={
                    "draft_explanation": "No proposed edges to ground in evidence.",
                }
            )
            return state

        total_evidence_count = 0
        grounded_edge_count = 0
        weak_edges: list[str] = []

        for edge in state.proposed_edges:
            # Retrieve evidence for this edge
            evidence_out, state = await self._call_skill(
                "evidence_link",
                {
                    "source_id": str(edge.source.id),
                    "target_id": str(edge.target.id),
                    "edge_type": edge.edge_type,
                    "rationale": edge.rationale,
                },
                state,
            )

            citations = evidence_out.get("citations", [])
            total_evidence_count += len(citations)

            if not citations:
                weak_edges.append(f"{edge.source.id} → {edge.target.id}")
                continue

            # Validate statistical quality of each citation
            valid_citations = []
            for citation in citations:
                validity_out, state = await self._call_skill(
                    "statistical_validate",
                    {"citation": citation},
                    state,
                )
                if validity_out.get("valid", False):
                    valid_citations.append(citation)

            if valid_citations:
                grounded_edge_count += 1
            else:
                weak_edges.append(f"{edge.source.id} → {edge.target.id}")

        # Contradiction detection across all evidence
        contradiction_out, state = await self._call_skill(
            "contradiction_detect",
            {
                "edge_count": len(state.proposed_edges),
                "evidence_count": total_evidence_count,
            },
            state,
        )
        contradictions: list[str] = contradiction_out.get("contradictions", [])

        # Build recommendations and flags
        recommendations: list[Recommendation] = []
        new_flags = list(state.flags)

        if weak_edges:
            new_flags.append("insufficient_evidence")
            recommendations.append(
                Recommendation(
                    rank=1,
                    action=f"Review {len(weak_edges)} edge(s) lacking sufficient evidence",
                    rationale=(
                        f"Edges without valid evidence: {', '.join(weak_edges[:5])}. "
                        "Manual evidence collection or expert review recommended."
                    ),
                    confidence=0.4,
                    risk_tier="HIGH",
                )
            )

        if contradictions:
            new_flags.append("contradictory_evidence")
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action="Resolve contradictory evidence before promotion",
                    rationale=f"Contradictions found: {'; '.join(contradictions[:3])}.",
                    confidence=0.3,
                    risk_tier="HIGH",
                )
            )

        if grounded_edge_count > 0 and not contradictions:
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action=f"{grounded_edge_count}/{len(state.proposed_edges)} edges grounded in evidence",
                    rationale="Evidence passes statistical validity checks.",
                    confidence=grounded_edge_count / max(len(state.proposed_edges), 1),
                    risk_tier="LOW" if not weak_edges else "MEDIUM",
                )
            )

        requires_human = state.requires_human_review or bool(weak_edges) or bool(contradictions)

        state = state.model_copy(
            update={
                "flags": new_flags,
                "requires_human_review": requires_human,
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": (
                    f"Evidence grounding complete. "
                    f"{grounded_edge_count}/{len(state.proposed_edges)} edges grounded. "
                    f"{total_evidence_count} total citations evaluated. "
                    f"{len(contradictions)} contradiction(s) detected."
                ),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="EvidenceGrounding",
            action="GROUND_EDGES",
            inputs={"proposed_edge_count": len(state.proposed_edges)},
            outputs={
                "grounded": grounded_edge_count,
                "total_citations": total_evidence_count,
                "weak_edges": len(weak_edges),
                "contradictions": len(contradictions),
            },
        )

        self._log.info(
            "evidence_grounding.complete",
            session_id=state.session_id,
            grounded=grounded_edge_count,
            weak=len(weak_edges),
            contradictions=len(contradictions),
        )
        return state

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        if skill_name == "evidence_link":
            return await self._skill_evidence_link(inputs)
        if skill_name == "statistical_validate":
            return await self._skill_statistical_validate(inputs)
        if skill_name == "contradiction_detect":
            return await self._skill_contradiction_detect(inputs)
        if skill_name == "semantic_layer_read":
            return await self._skill_semantic_layer_read(inputs)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_evidence_link(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Retrieve evidence for a causal claim from the evidence store.

        Production: queries evidence registry by source/target/edge_type.
        Stub: returns empty (no evidence store populated yet).
        """
        return {"citations": []}

    async def _skill_statistical_validate(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate the statistical quality of an evidence citation.

        Production: checks p-value thresholds, sample sizes, test assumptions.
        Stub: returns valid for any non-empty citation.
        """
        citation = inputs.get("citation", {})
        return {"valid": bool(citation)}

    async def _skill_contradiction_detect(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Detect contradictions across the evidence set.

        Production: compares directions, effect sizes, and statistical
        significance across citations for the same causal claim.
        Stub: returns no contradictions.
        """
        return {"contradictions": []}

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        return {"graph_context": {}}

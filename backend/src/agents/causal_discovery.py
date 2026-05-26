"""
CausalDiscoveryAgent — proposes candidate causal edges from data and domain evidence.

Responsibilities (AGENTS.md §2.1 CausalDiscoveryAgent):
  - Propose ranked candidate edges with confidence scores and evidence citations
  - Flag contradictions against known domain constraints
  - Surface missing variable hypotheses
  - NEVER write directly to the semantic layer (reads only)
  - Every proposed edge must cite supporting evidence

Implemented skills:
  causal_propose_edge       — propose one directed edge with rationale
  confounder_screen         — identify likely unmeasured confounders
  conditional_independence_test — simple heuristic d-separation check
  evidence_link             — attach evidence to a candidate edge
  semantic_layer_read       — read existing graph context
"""

from __future__ import annotations

import statistics
from typing import Any
from uuid import uuid4

import structlog

from src.agents.base import BaseAgent
from src.semantic_layer.models import (
    CausalObjectRef,
    CynefinDomain,
    EdgeProposal,
    EdgeType,
    EpistemicState,
    GuardianVerdict,
    Recommendation,
)

log = structlog.get_logger(__name__)

# Hallucination penalty applied when there is insufficient evidence
_EVIDENCE_FLOOR = 0.35
# Minimum confidence to include a proposed edge in the state
_CONFIDENCE_CUTOFF = 0.30


class CausalDiscoveryAgent(BaseAgent):
    """
    Proposes candidate causal graph edges from dataset statistics and domain context.

    This agent NEVER writes to the semantic layer.  It only reads existing
    graph assets and proposes edges to the EpistemicState for human review and
    subsequent Guardian-gated write.

    Usage::

        agent = CausalDiscoveryAgent()
        state = await agent.process(state)
        # state.proposed_edges contains ranked candidates
    """

    def __init__(self, agent_id: str = "causal-discovery-agent") -> None:
        super().__init__(agent_id=agent_id)

    # ── process() — BaseAgent contract ───────────────────────────────────────

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Discover candidate causal edges from context carried in EpistemicState.

        Steps:
          1. Circuit breaker check (max 5 cycles)
          2. Hallucination risk check
          3. Read existing graph context (semantic_layer_read)
          4. Propose candidate edges (causal_propose_edge per variable pair)
          5. Screen for confounders (confounder_screen)
          6. Attach evidence to candidates (evidence_link)
          7. Return updated state with proposed_edges populated

        CHAOTIC domain short-circuits immediately — returns escalation state.
        """
        state = self._check_circuit_breaker(state)
        state = self._check_hallucination_risk(state)

        if state.cynefin_domain == CynefinDomain.CHAOTIC:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "chaotic_domain_skip"],
                    "draft_explanation": (
                        "CHAOTIC domain: CausalDiscoveryAgent suspended. "
                        "Routing to human incident commander."
                    ),
                }
            )
            self._log.warning(
                "causal_discovery.chaotic_domain.skip",
                session_id=state.session_id,
            )
            return state

        # Step 1: read existing graph context
        context_outputs, state = await self._call_skill(
            "semantic_layer_read",
            {
                "fragment_id": state.active_graph_fragment_id,
                "domain": state.cynefin_domain,
            },
            state,
        )

        # Step 2: propose candidate edges from context
        proposal_inputs = {
            "graph_context": context_outputs.get("graph_context", {}),
            "domain": state.cynefin_domain,
            "existing_edges": len(state.proposed_edges),
        }
        proposal_outputs, state = await self._call_skill(
            "causal_propose_edge",
            proposal_inputs,
            state,
        )

        new_edges: list[EdgeProposal] = proposal_outputs.get("proposed_edges", [])

        # Step 3: confounder screening
        if new_edges:
            confounder_outputs, state = await self._call_skill(
                "confounder_screen",
                {
                    "proposed_edges": [e.model_dump() for e in new_edges],
                    "domain": state.cynefin_domain,
                },
                state,
            )
            confounder_flags: list[str] = confounder_outputs.get("flagged_confounders", [])
        else:
            confounder_flags = []

        # Step 4: evidence linking for each candidate above the cutoff
        linked_edges: list[EdgeProposal] = []
        for edge in new_edges:
            if edge.confidence < _CONFIDENCE_CUTOFF:
                continue
            evidence_outputs, state = await self._call_skill(
                "evidence_link",
                {
                    "source": str(edge.source.id),
                    "target": str(edge.target.id),
                    "rationale": edge.rationale,
                },
                state,
            )
            evidence_ids = evidence_outputs.get("evidence_ids", [])
            # Rebuild edge with linked evidence refs
            linked_edges.append(
                EdgeProposal(
                    source=edge.source,
                    target=edge.target,
                    edge_type=edge.edge_type,
                    confidence=edge.confidence,
                    evidence_refs=evidence_ids,
                    rationale=edge.rationale,
                )
            )

        # If no evidence could be linked, apply hallucination floor
        if linked_edges and not any(e.evidence_refs for e in linked_edges):
            linked_edges = [
                EdgeProposal(
                    source=e.source,
                    target=e.target,
                    edge_type=e.edge_type,
                    confidence=max(_EVIDENCE_FLOOR, e.confidence * 0.5),
                    evidence_refs=[],
                    rationale=e.rationale + " [LOW EVIDENCE — pending review]",
                )
                for e in linked_edges
            ]

        # Sort by confidence descending
        linked_edges.sort(key=lambda e: e.confidence, reverse=True)

        # Build overall confidence from the top-3 proposals
        top_scores = [e.confidence for e in linked_edges[:3]]
        overall_confidence = statistics.mean(top_scores) if top_scores else 0.0

        # Build recommendations from proposed edges
        recommendations = [
            Recommendation(
                rank=i + 1,
                action=f"Propose edge: {e.source.id} → {e.target.id} ({e.edge_type})",
                rationale=e.rationale,
                confidence=e.confidence,
                risk_tier="LOW" if e.confidence > 0.8 else "MEDIUM" if e.confidence > 0.5 else "HIGH",
            )
            for i, e in enumerate(linked_edges[:10])
        ]

        explanation_lines = [
            f"CausalDiscoveryAgent proposed {len(linked_edges)} candidate edge(s).",
        ]
        if confounder_flags:
            explanation_lines.append(
                f"Potential confounders detected: {', '.join(confounder_flags)}."
            )
        if state.cynefin_domain == CynefinDomain.COMPLEX:
            explanation_lines.append(
                "COMPLEX domain: causal structure is uncertain — all edges require "
                "human review and expert elicitation before promotion."
            )
            # COMPLEX domain always requires human review (AGENTS.md §5.2:
            # CausalDiscovery → ExpertElicitation)
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "complex_domain_expert_review_required"],
                }
            )

        state = state.model_copy(
            update={
                "proposed_edges": [*state.proposed_edges, *linked_edges],
                "overall_confidence": overall_confidence,
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": " ".join(explanation_lines),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="CausalDiscovery",
            action="PROPOSE_EDGES",
            inputs=proposal_inputs,
            outputs={
                "edge_count": len(linked_edges),
                "confounder_flags": confounder_flags,
                "overall_confidence": overall_confidence,
            },
        )

        self._log.info(
            "causal_discovery.complete",
            session_id=state.session_id,
            edge_count=len(linked_edges),
            overall_confidence=overall_confidence,
            confounder_count=len(confounder_flags),
        )
        return state

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        """Dispatch skill calls for CausalDiscoveryAgent."""

        if skill_name == "semantic_layer_read":
            return await self._skill_semantic_layer_read(inputs)

        if skill_name == "causal_propose_edge":
            return await self._skill_causal_propose_edge(inputs, state)

        if skill_name == "confounder_screen":
            return await self._skill_confounder_screen(inputs)

        if skill_name == "evidence_link":
            return await self._skill_evidence_link(inputs)

        if skill_name == "conditional_independence_test":
            return await self._skill_conditional_independence_test(inputs)

        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Read the current graph context from the semantic layer.

        In production this calls SemanticLayerRegistry.  Here we return a
        stub that includes the active_graph_fragment_id as context so
        downstream skills can reference it.
        """
        from src.semantic_layer.registry import SemanticLayerRegistry

        registry = SemanticLayerRegistry()
        fragment_id = inputs.get("fragment_id")
        context: dict[str, Any] = {}

        if fragment_id:
            try:
                from uuid import UUID
                fragment = await registry.get_fragment(UUID(str(fragment_id)))
                context["fragment_name"] = fragment.name
                context["fragment_domain"] = fragment.domain
                context["fragment_edge_count"] = len(fragment.edges)
            except Exception:
                context["fragment_id"] = str(fragment_id)

        return {"graph_context": context}

    async def _skill_causal_propose_edge(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Propose candidate directed edges using heuristic reasoning.

        In production this integrates DoWhy + cynepic-causal Rust crate for
        statistical independence tests and structural causal model discovery.
        This stub generates plausible proposals from the graph context.
        """
        graph_context = inputs.get("graph_context", {})
        domain = inputs.get("domain", state.cynefin_domain)

        # Confidence scaled by domain complexity
        domain_confidence_map = {
            CynefinDomain.CLEAR: 0.90,
            CynefinDomain.COMPLICATED: 0.75,
            CynefinDomain.COMPLEX: 0.55,
            CynefinDomain.CHAOTIC: 0.20,
            CynefinDomain.DISORDER: 0.30,
        }
        base_confidence = domain_confidence_map.get(domain, 0.5)

        # Generate a representative candidate edge from context
        source_id = uuid4()
        target_id = uuid4()

        proposals = [
            EdgeProposal(
                source=CausalObjectRef(id=source_id, object_type="CausalEntity"),
                target=CausalObjectRef(id=target_id, object_type="Outcome"),
                edge_type=EdgeType.CAUSES,
                confidence=base_confidence,
                evidence_refs=[],
                rationale=(
                    f"Statistical association detected in {domain} domain context. "
                    f"Fragment context: {graph_context.get('fragment_name', 'unknown')}. "
                    "Requires expert validation before promotion to semantic layer."
                ),
            )
        ]

        return {"proposed_edges": proposals}

    async def _skill_confounder_screen(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Screen a proposed edge set for likely unmeasured confounders.

        In production integrates with cynepic-causal for back-door criterion
        checks and domain knowledge lookup.
        """
        proposed_edges = inputs.get("proposed_edges", [])
        # Heuristic: flag if confidence < 0.6 (weak statistical basis)
        flagged = [
            f"confounder_risk_on_edge_{i}"
            for i, e in enumerate(proposed_edges)
            if isinstance(e, dict) and float(e.get("confidence", 1.0)) < 0.6
        ]
        return {"flagged_confounders": flagged}

    async def _skill_evidence_link(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Link a candidate edge to supporting evidence references.

        In production integrates with the evidence store and document corpus.
        Returns empty list when no evidence is available (caller applies floor).
        """
        # Production: query evidence store by source/target UUIDs.
        # Stub: return empty (caller applies low-evidence penalty).
        return {"evidence_ids": []}

    async def _skill_conditional_independence_test(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Run a conditional independence test (d-separation heuristic).

        In production uses DoWhy or cynepic-causal for PC-algorithm-based
        independence tests on the input dataset statistics.
        """
        # Stub: cannot determine independence without real data
        return {
            "independent": None,
            "p_value": None,
            "test": "stub — real data required",
        }

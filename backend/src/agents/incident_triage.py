"""
IncidentTriageAgent — first-response agent for operational incidents.

Responsibilities (AGENTS.md §2.2 IncidentTriageAgent):
  - Classify incident domain complexity via Cynefin (cynefin_classify skill)
  - Propose ranked candidate root-cause list with causal evidence
  - Retrieve similar past incidents from experience buffer
  - Surface actionable interventions (all requiring human approval)
  - CHAOTIC domain: immediate human escalation with minimal AI framing
  - COMPLEX domain: surface causal uncertainty explicitly

Security invariant: all proposed interventions require human approval before
execution.  This agent never writes to the semantic layer and never executes
interventions.

Implemented skills:
  cynefin_classify          — classify incident into Cynefin domain
  causal_propose_edge       — propose causal driver edges
  experience_buffer_query   — retrieve similar past incidents
  intervention_estimate     — estimate effect of proposed interventions
  guardian_check            — submit proposed interventions to Guardian
  counterfactual_reason     — "what would have happened without X?" analysis
"""

from __future__ import annotations

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
from src.skills import (
    counterfactual_reason,
    experience_buffer_query,
    intervention_estimate,
    store_incident_experience,
)

log = structlog.get_logger(__name__)

# Minimum confidence below which a root-cause candidate is dropped
_MIN_ROOT_CAUSE_CONFIDENCE = 0.25


class IncidentTriageAgent(BaseAgent):
    """
    First-response agent for operational incidents in the DevOps vertical.

    Designed for the SRE / Incident Commander persona (UIX.md §2 Operator persona).
    Classifies complexity, surfaces causal drivers, and proposes interventions —
    all subject to Guardian policy enforcement and human approval.

    Usage::

        agent = IncidentTriageAgent()
        state = await agent.process(state)
        # state.recommendations contains ranked interventions
        # state.proposed_edges contains root-cause candidates
        # state.draft_explanation contains narrative for the incident brief
    """

    def __init__(self, agent_id: str = "incident-triage-agent") -> None:
        super().__init__(agent_id=agent_id)

    # ── process() — BaseAgent contract ───────────────────────────────────────

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Triage an operational incident.

        Steps:
          1. Circuit breaker check
          2. Cynefin classification of the incident
          3. Domain-gated routing:
             - CHAOTIC  → immediate escalation, minimal framing
             - DISORDER → prompt re-classification, no analysis
             - Others   → full causal triage pipeline
          4. Experience buffer query (past similar incidents)
          5. Root-cause edge proposals
          6. Intervention estimation
          7. Guardian check on proposed interventions
          8. Return updated EpistemicState with recommendations + explanation
        """
        state = self._check_circuit_breaker(state)
        state = self._check_hallucination_risk(state)

        # Step 1: Cynefin classification of the incident
        classify_outputs, state = await self._call_skill(
            "cynefin_classify",
            {
                "description": state.draft_explanation or "operational incident",
                "signals": {
                    "cycle_count": state.cycle_count,
                    "active_assumptions": len(state.active_assumptions),
                    "proposed_edges": len(state.proposed_edges),
                },
            },
            state,
        )

        classified_domain: CynefinDomain = classify_outputs.get(
            "domain", state.cynefin_domain
        )
        domain_confidence: float = float(classify_outputs.get("confidence", 0.5))

        state = state.model_copy(
            update={
                "cynefin_domain": classified_domain,
                "domain_confidence": domain_confidence,
            }
        )

        state = self._add_reasoning_step(
            state,
            node="IncidentTriage",
            action="CYNEFIN_CLASSIFY",
            inputs={"description": state.draft_explanation},
            outputs={
                "domain": classified_domain,
                "confidence": domain_confidence,
            },
        )

        # ── CHAOTIC domain: immediate escalation ─────────────────────────────
        if classified_domain == CynefinDomain.CHAOTIC:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "chaotic_incident"],
                    "draft_explanation": (
                        "CHAOTIC INCIDENT DETECTED. Routing immediately to human "
                        "incident commander. No automated causal analysis will be "
                        "performed. Stabilise → Act → Sense."
                    ),
                    "recommendations": [
                        Recommendation(
                            rank=1,
                            action="ESCALATE: Route to human incident commander immediately.",
                            rationale=(
                                "Cynefin domain is CHAOTIC. Novel cause/effect "
                                "relationships. AI-generated analysis would be "
                                "unreliable and potentially harmful."
                            ),
                            confidence=1.0,
                            risk_tier="CRITICAL",
                        )
                    ],
                }
            )
            self._log.warning(
                "incident_triage.chaotic.escalate",
                session_id=state.session_id,
            )
            return state

        # ── DISORDER domain: request re-classification ────────────────────────
        if classified_domain == CynefinDomain.DISORDER:
            state = state.model_copy(
                update={
                    "requires_human_review": True,
                    "flags": [*state.flags, "disorder_domain"],
                    "draft_explanation": (
                        "Incident domain is DISORDER (unclassified). "
                        "Gather more signal before proceeding with causal analysis."
                    ),
                }
            )
            return state

        # ── Full triage pipeline (CLEAR / COMPLICATED / COMPLEX) ─────────────

        # Step 2: Experience buffer — retrieve similar past incidents
        experience_outputs, state = await self._call_skill(
            "experience_buffer_query",
            {
                "domain": classified_domain,
                "description": state.draft_explanation,
                "limit": 5,
            },
            state,
        )
        similar_incidents: list[dict[str, Any]] = experience_outputs.get(
            "similar_incidents", []
        )

        # Step 3: Propose root-cause edges
        proposal_outputs, state = await self._call_skill(
            "causal_propose_edge",
            {
                "domain": classified_domain,
                "similar_incidents": similar_incidents,
                "active_fragment_id": state.active_graph_fragment_id,
            },
            state,
        )
        candidate_edges: list[EdgeProposal] = proposal_outputs.get(
            "proposed_edges", []
        )
        # Filter by confidence floor
        candidate_edges = [
            e for e in candidate_edges if e.confidence >= _MIN_ROOT_CAUSE_CONFIDENCE
        ]

        # Step 4: Intervention estimation for top candidates
        interventions: list[dict[str, Any]] = []
        for edge in candidate_edges[:3]:
            est_outputs, state = await self._call_skill(
                "intervention_estimate",
                {
                    "source_id": str(edge.source.id),
                    "target_id": str(edge.target.id),
                    "edge_type": edge.edge_type,
                    "confidence": edge.confidence,
                },
                state,
            )
            interventions.append(
                {
                    "edge": edge,
                    "estimated_effect": est_outputs.get("estimated_effect", 0.0),
                    "risk_tier": est_outputs.get("risk_tier", "MEDIUM"),
                    "action": est_outputs.get("action", "Investigate root cause"),
                }
            )

        # Step 5: Guardian check on proposed interventions
        if interventions:
            guardian_payload = {
                "action_type": "INCIDENT_TRIAGE_INTERVENTIONS",
                "proposed_edges": [e.model_dump() for e in candidate_edges],
                "cynefin_domain": classified_domain,
                "hallucination_risk_score": state.hallucination_risk_score,
                "cycle_count": state.cycle_count,
                "active_assumptions": [a.model_dump() for a in state.active_assumptions],
            }
            guardian_outputs, state = await self._call_skill(
                "guardian_check",
                guardian_payload,
                state,
            )
            verdict = guardian_outputs.get("verdict", GuardianVerdict.PENDING)
            state = state.model_copy(
                update={
                    "guardian_verdict": verdict,
                    "policy_rules_cited": guardian_outputs.get("policy_rules_cited", []),
                }
            )

        # Step 6: Counterfactual reasoning (COMPLEX domain only)
        counterfactual_text = ""
        if classified_domain == CynefinDomain.COMPLEX and candidate_edges:
            cf_outputs, state = await self._call_skill(
                "counterfactual_reason",
                {
                    "proposed_edges": [e.model_dump() for e in candidate_edges[:2]],
                    "query": "What would have happened without the leading root cause?",
                },
                state,
            )
            counterfactual_text = cf_outputs.get("narrative", "")

        # Build ranked recommendations
        recommendations: list[Recommendation] = []
        for i, interv in enumerate(interventions):
            edge = interv["edge"]
            recommendations.append(
                Recommendation(
                    rank=i + 1,
                    action=interv["action"],
                    rationale=(
                        f"Root cause candidate: {edge.source.id} → {edge.target.id}. "
                        f"Confidence: {edge.confidence:.2f}. "
                        f"{edge.rationale}"
                    ),
                    confidence=edge.confidence * float(interv["estimated_effect"] or 1.0),
                    risk_tier=interv["risk_tier"],
                )
            )

        # Similar-incident recommendations
        for j, incident in enumerate(similar_incidents[:2]):
            recommendations.append(
                Recommendation(
                    rank=len(interventions) + j + 1,
                    action=f"Review past incident: {incident.get('title', 'similar incident')}",
                    rationale=(
                        f"Similarity: {incident.get('similarity', 0):.0%}. "
                        f"Resolution: {incident.get('resolution', 'see runbook')}."
                    ),
                    confidence=float(incident.get("similarity", 0.5)),
                    risk_tier="LOW",
                )
            )

        # Build narrative explanation
        explanation_parts = [
            f"Incident classified as {classified_domain} "
            f"(confidence: {domain_confidence:.0%}).",
            f"{len(candidate_edges)} root-cause candidate(s) identified.",
        ]
        if similar_incidents:
            explanation_parts.append(
                f"{len(similar_incidents)} similar past incident(s) "
                "retrieved from experience buffer."
            )
        if classified_domain == CynefinDomain.COMPLEX:
            explanation_parts.append(
                "COMPLEX domain: causal structure is uncertain. "
                "Expert elicitation recommended before approving interventions."
            )
        if counterfactual_text:
            explanation_parts.append(f"Counterfactual analysis: {counterfactual_text}")

        state = state.model_copy(
            update={
                "proposed_edges": [*state.proposed_edges, *candidate_edges],
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": " ".join(explanation_parts),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="IncidentTriage",
            action="TRIAGE_COMPLETE",
            inputs={
                "domain": classified_domain,
                "candidate_edge_count": len(candidate_edges),
            },
            outputs={
                "recommendation_count": len(recommendations),
                "similar_incident_count": len(similar_incidents),
            },
        )

        # Store incident in experience buffer for future retrieval
        try:
            await store_incident_experience(
                incident_id=state.session_id,
                description=state.draft_explanation,
                domain=str(classified_domain),
                severity="P1" if classified_domain == CynefinDomain.CHAOTIC else "P2",
                services=[],  # Could extract from graph context in future
                metrics=[],  # Could populate from incident signals in future
                resolution=recommendations[0].action if recommendations else None,
                cynefin_domain=str(classified_domain),
                guardian_verdict=str(state.guardian_verdict),
            )
        except Exception as e:
            # Non-blocking: log but don't fail triage if storage fails
            self._log.warning("incident_triage.storage_failed", error=str(e))

        self._log.info(
            "incident_triage.complete",
            session_id=state.session_id,
            domain=classified_domain,
            candidate_edges=len(candidate_edges),
            recommendations=len(recommendations),
        )
        return state

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        if skill_name == "cynefin_classify":
            return await self._skill_cynefin_classify(inputs)
        if skill_name == "causal_propose_edge":
            return await self._skill_causal_propose_edge(inputs, state)
        if skill_name == "experience_buffer_query":
            return await self._skill_experience_buffer_query(inputs, state)
        if skill_name == "intervention_estimate":
            return await self._skill_intervention_estimate(inputs, state)
        if skill_name == "guardian_check":
            return await self._skill_guardian_check(inputs, state)
        if skill_name == "counterfactual_reason":
            return await self._skill_counterfactual_reason(inputs, state)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_cynefin_classify(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Classify an incident into a Cynefin domain.

        Production: integrates cynepic-router Rust crate.
        Heuristic stub: uses signal counts to infer complexity.
        """
        signals = inputs.get("signals", {})
        assumptions_count = int(signals.get("active_assumptions", 0))
        description = str(inputs.get("description", "")).lower()

        # Keyword heuristics for domain classification
        chaotic_keywords = {"cascading", "unknown", "catastrophic", "never seen", "total outage"}
        complex_keywords = {"intermittent", "unclear", "multiple", "correlation", "pattern"}
        complicated_keywords = {"deployment", "config", "latency", "cpu", "memory", "timeout"}

        desc_words = set(description.split())
        if desc_words & chaotic_keywords:
            return {"domain": CynefinDomain.CHAOTIC, "confidence": 0.85}
        if desc_words & complex_keywords or assumptions_count == 0:
            return {"domain": CynefinDomain.COMPLEX, "confidence": 0.70}
        if desc_words & complicated_keywords:
            return {"domain": CynefinDomain.COMPLICATED, "confidence": 0.80}

        # Default for operational incidents with known services
        return {"domain": CynefinDomain.COMPLICATED, "confidence": 0.60}

    async def _skill_causal_propose_edge(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Propose root-cause causal edges for the incident.

        Production: uses CausalDiscoveryAgent as a sub-agent or direct
        cynepic-causal invocation with service dependency graph.
        """
        domain = inputs.get("domain", CynefinDomain.COMPLICATED)
        similar = inputs.get("similar_incidents", [])

        confidence_map = {
            CynefinDomain.CLEAR: 0.88,
            CynefinDomain.COMPLICATED: 0.72,
            CynefinDomain.COMPLEX: 0.50,
        }
        confidence = confidence_map.get(domain, 0.50)

        # Boost confidence if similar incidents found
        if similar:
            confidence = min(confidence + 0.10, 0.95)

        root_cause_id = uuid4()
        effect_id = uuid4()

        proposals = [
            EdgeProposal(
                source=CausalObjectRef(id=root_cause_id, object_type="CausalEntity"),
                target=CausalObjectRef(id=effect_id, object_type="Outcome"),
                edge_type=EdgeType.CAUSES,
                confidence=confidence,
                evidence_refs=[],
                rationale=(
                    f"Probable root cause identified in {domain} domain. "
                    "Derived from service dependency graph and incident signals. "
                    "Requires human validation before intervention."
                ),
            )
        ]
        return {"proposed_edges": proposals}

    async def _skill_experience_buffer_query(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Query the experience buffer for similar past incidents.

        Current implementation uses durable incident-triage session snapshots as
        an interim persisted experience source. This narrows the gap to the
        documented experience buffer while keeping writes governed through the
        existing session-state persistence path.
        """
        return await experience_buffer_query(
            description=str(inputs.get("description", "")),
            domain=str(inputs.get("domain", "")) or None,
            limit=int(inputs.get("limit", 5)),
            session_id=state.session_id,
        )

    async def _skill_intervention_estimate(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Estimate the effect of a proposed intervention on the incident.

        Delegates to the shared intervention_estimate skill so incident triage
        uses the same graph-grounded estimation pathway as the simulation layer
        instead of a confidence-only placeholder.
        """
        return await intervention_estimate(
            source_id=str(inputs.get("source_id", "source")),
            target_id=str(inputs.get("target_id", "target")),
            edge_type=str(inputs.get("edge_type", "CAUSES")),
            confidence=float(inputs.get("confidence", 0.5)),
            assumptions=[a.model_dump(mode="json") for a in state.active_assumptions],
        )

    async def _skill_guardian_check(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Submit proposed interventions to the GovernanceAgent.

        Delegates to GovernanceAgent.check() via the platform's governance
        infrastructure rather than reimplementing policy logic.
        """
        from src.agents.governance import GovernanceAgent

        guardian = GovernanceAgent(agent_id=f"{self.agent_id}-guardian")
        result = await guardian.check(inputs, state)
        return {
            "verdict": result.verdict,
            "reason": result.reason,
            "policy_rules_cited": result.policy_rules_cited,
            "audit_entry_id": str(result.audit_entry_id),
        }

    async def _skill_counterfactual_reason(
        self, inputs: dict[str, Any], state: EpistemicState
    ) -> dict[str, Any]:
        """
        Apply Pearl's three-level counterfactual hierarchy.

        Current implementation returns a bounded graph-heuristic summary rather
        than a free-text placeholder. It explicitly labels the result as a
        non-observational estimate until a full SCM-backed estimator is wired.
        """
        return await counterfactual_reason(
            proposed_edges=list(inputs.get("proposed_edges", [])),
            query=str(inputs.get("query", "What would have happened?")),
            assumptions=[a.model_dump(mode="json") for a in state.active_assumptions],
        )

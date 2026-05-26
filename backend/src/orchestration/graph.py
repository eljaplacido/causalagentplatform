"""
LangGraph StateGraph for the Unified Enterprise Causal Platform.

Topology (AGENTS.md §5.1):

    Intake → CynefinClassify → [Router]
        ├── CLEAR        → DirectAnswer → GuardianCheck → Output
        ├── COMPLICATED  → CausalAnalysis → GuardianCheck → Output
        ├── COMPLEX      → CausalDiscovery → GuardianCheck → Output
        ├── CHAOTIC      → EscalateHuman → END
        └── DISORDER     → DisorderProtocol → CynefinClassify [retry, max 1]

Security invariants:
  - GuardianCheck node runs before every Output node
  - CHAOTIC domain routes immediately to human escalation
  - CircuitBreakerError terminates the graph and marks requires_human_review
  - All state transitions are appended to reasoning_steps (immutable audit)
"""

from __future__ import annotations

from typing import Literal, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from src.agents.causal_discovery import CausalDiscoveryAgent
from src.agents.governance import GovernanceAgent
from src.agents.incident_triage import IncidentTriageAgent
from src.exceptions import CircuitBreakerError, GuardianBlockedError
from src.orchestration.planning import node_plan_ahead
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
    Recommendation,
)

log = structlog.get_logger(__name__)

# Maximum disorder retries before forcing escalation
_MAX_DISORDER_RETRIES = 1


# ── LangGraph state schema ────────────────────────────────────────────────────


class PlatformState(TypedDict):
    """
    LangGraph state wrapper holding the full EpistemicState.

    LangGraph requires TypedDict for state; we embed the Pydantic EpistemicState
    as a single key so all state flows through the standard EpistemicState contract.
    """

    epistemic: EpistemicState
    disorder_retries: int  # tracks DISORDER retry count to prevent infinite loops


# ── Node implementations ──────────────────────────────────────────────────────


async def node_intake(state: PlatformState) -> PlatformState:
    """
    Intake node — validate and prepare the EpistemicState for the pipeline.

    Adds the 'intake_complete' flag and logs the session start.
    """
    epistemic = state["epistemic"]
    log.info(
        "graph.intake",
        session_id=epistemic.session_id,
        agent_id=epistemic.agent_id,
        initial_domain=epistemic.cynefin_domain,
    )
    epistemic = epistemic.model_copy(
        update={"flags": [*epistemic.flags, "intake_complete"]}
    )
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_cynefin_classify(state: PlatformState) -> PlatformState:
    """
    CynefinClassify node — classify the problem domain.

    Uses IncidentTriageAgent's cynefin_classify skill for the classification,
    as the triage agent owns domain classification for operational contexts.
    For non-incident workflows, the same heuristic applies.
    """
    epistemic = state["epistemic"]
    agent = IncidentTriageAgent(agent_id="cynefin-classify-node")

    try:
        classify_outputs, updated = await agent._call_skill(
            "cynefin_classify",
            {
                "description": epistemic.draft_explanation or epistemic.agent_id,
                "signals": {
                    "cycle_count": epistemic.cycle_count,
                    "active_assumptions": len(epistemic.active_assumptions),
                    "proposed_edges": len(epistemic.proposed_edges),
                },
            },
            epistemic,
        )
        classified_domain = classify_outputs.get("domain", epistemic.cynefin_domain)
        domain_confidence = float(classify_outputs.get("confidence", 0.5))
        updated = updated.model_copy(
            update={
                "cynefin_domain": classified_domain,
                "domain_confidence": domain_confidence,
            }
        )
        log.info(
            "graph.cynefin_classify",
            session_id=epistemic.session_id,
            domain=classified_domain,
            confidence=domain_confidence,
        )
    except CircuitBreakerError:
        updated = _circuit_breaker_escalation(epistemic)

    return {"epistemic": updated, "disorder_retries": state.get("disorder_retries", 0)}


async def node_direct_answer(state: PlatformState) -> PlatformState:
    """
    DirectAnswer node — CLEAR domain.

    For well-understood problems with established best-practices, return a
    direct recommendation without deep causal analysis.
    """
    epistemic = state["epistemic"]
    recommendation = Recommendation(
        rank=1,
        action="Apply standard operating procedure for this well-understood problem.",
        rationale=(
            "Problem classified as CLEAR domain. "
            "Established causal patterns and best-practices apply directly."
        ),
        confidence=0.90,
        risk_tier="LOW",
    )
    epistemic = epistemic.model_copy(
        update={
            "recommendations": [*epistemic.recommendations, recommendation],
            "overall_confidence": 0.90,
            "draft_explanation": (
                "CLEAR domain: direct answer generated from established patterns. "
                "No deep causal analysis required."
            ),
        }
    )
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_causal_analysis(state: PlatformState) -> PlatformState:
    """
    CausalAnalysis node — COMPLICATED domain.

    Runs CausalDiscoveryAgent for structured causal discovery.
    Complicated problems have knowable causes — analysis by experts.
    """
    epistemic = state["epistemic"]
    agent = CausalDiscoveryAgent(agent_id="causal-analysis-node")
    try:
        epistemic = await agent.process(epistemic)
    except CircuitBreakerError:
        epistemic = _circuit_breaker_escalation(epistemic)
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_causal_discovery(state: PlatformState) -> PlatformState:
    """
    CausalDiscovery node — COMPLEX domain.

    Runs CausalDiscoveryAgent with uncertainty surfacing.
    Complex problems have emergent causes — must surface uncertainty explicitly.
    """
    epistemic = state["epistemic"]
    agent = CausalDiscoveryAgent(agent_id="causal-discovery-node")
    try:
        epistemic = await agent.process(epistemic)
        # COMPLEX domain: explicitly flag all proposed edges for expert review
        epistemic = epistemic.model_copy(
            update={
                "flags": [*epistemic.flags, "complex_domain_expert_review_required"],
                "requires_human_review": True,
            }
        )
    except CircuitBreakerError:
        epistemic = _circuit_breaker_escalation(epistemic)
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_guardian_check(state: PlatformState) -> PlatformState:
    """
    GuardianCheck node — mandatory pre-Output policy enforcement.

    All outputs must pass the Guardian before being surfaced to the user.
    Writes an audit entry for every check (immutability contract).
    """
    epistemic = state["epistemic"]
    agent = GovernanceAgent(agent_id="guardian-check-node")

    action_payload = {
        "action_type": "GRAPH_NODE_GUARDIAN_CHECK",
        "proposed_edges": [e.model_dump() for e in epistemic.proposed_edges],
        "cynefin_domain": epistemic.cynefin_domain,
        "hallucination_risk_score": epistemic.hallucination_risk_score,
        "cycle_count": epistemic.cycle_count,
        "active_assumptions": [a.model_dump() for a in epistemic.active_assumptions],
    }

    try:
        result = await agent.check(action_payload, epistemic)
        epistemic = epistemic.model_copy(
            update={
                "guardian_verdict": result.verdict,
                "policy_rules_cited": result.policy_rules_cited,
            }
        )
        log.info(
            "graph.guardian_check",
            session_id=epistemic.session_id,
            verdict=result.verdict,
            audit_entry_id=str(result.audit_entry_id),
        )
    except Exception as exc:
        log.error(
            "graph.guardian_check.error",
            error=str(exc),
            session_id=epistemic.session_id,
        )
        epistemic = epistemic.model_copy(
            update={
                "guardian_verdict": GuardianVerdict.REJECTED,
                "flags": [*epistemic.flags, "guardian_check_error"],
                "requires_human_review": True,
            }
        )

    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_escalate_human(state: PlatformState) -> PlatformState:
    """
    EscalateHuman node — CHAOTIC domain mandatory human escalation.

    Adds minimal framing and marks for immediate human review.
    Per AGENTS.md: no automated approval permitted in CHAOTIC domain.
    """
    epistemic = state["epistemic"]
    epistemic = epistemic.model_copy(
        update={
            "requires_human_review": True,
            "flags": [*epistemic.flags, "human_escalation_required"],
            "guardian_verdict": GuardianVerdict.PENDING,
            "recommendations": [
                Recommendation(
                    rank=1,
                    action="ESCALATE: Route to human decision-maker immediately.",
                    rationale=(
                        "CHAOTIC domain detected. Novel situation with no established "
                        "causal patterns. AI analysis suppressed. "
                        "Human must: Stabilise → Act → Sense."
                    ),
                    confidence=1.0,
                    risk_tier="CRITICAL",
                ),
                *epistemic.recommendations,
            ],
            "draft_explanation": (
                "CHAOTIC domain: automated analysis suspended. "
                "Route this incident to your human incident commander now."
            ),
        }
    )
    log.warning(
        "graph.escalate_human",
        session_id=epistemic.session_id,
        domain=epistemic.cynefin_domain,
    )
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


async def node_disorder_protocol(state: PlatformState) -> PlatformState:
    """
    DisorderProtocol node — DISORDER domain.

    Prompts for re-classification. After _MAX_DISORDER_RETRIES, escalates to human.
    """
    epistemic = state["epistemic"]
    retries = state.get("disorder_retries", 0) + 1

    if retries > _MAX_DISORDER_RETRIES:
        # Too many retries — escalate
        epistemic = epistemic.model_copy(
            update={
                "requires_human_review": True,
                "flags": [*epistemic.flags, "disorder_max_retries"],
                "draft_explanation": (
                    "DISORDER domain unresolved after re-classification attempt. "
                    "Escalating to human for domain clarification."
                ),
            }
        )
    else:
        epistemic = epistemic.model_copy(
            update={
                "flags": [*epistemic.flags, f"disorder_retry_{retries}"],
                "draft_explanation": (
                    "DISORDER domain: gathering more signal before classification. "
                    "Please provide additional context."
                ),
            }
        )
    log.info(
        "graph.disorder_protocol",
        session_id=epistemic.session_id,
        retry=retries,
    )
    return {"epistemic": epistemic, "disorder_retries": retries}


async def node_output(state: PlatformState) -> PlatformState:
    """
    Output node — finalize state for return to caller.

    Adds 'pipeline_complete' flag. If Guardian rejected, marks for review.
    """
    epistemic = state["epistemic"]
    flags = list(epistemic.flags)
    flags.append("pipeline_complete")

    if epistemic.guardian_verdict == GuardianVerdict.REJECTED:
        flags.append("guardian_rejected_output")
        epistemic = epistemic.model_copy(
            update={
                "requires_human_review": True,
                "flags": flags,
                "draft_explanation": epistemic.draft_explanation + " [REJECTED by Guardian — human review required]",
            }
        )
    elif epistemic.guardian_verdict == GuardianVerdict.REQUIRES_ESCALATION:
        flags.append("guardian_escalation_output")
        epistemic = epistemic.model_copy(
            update={
                "requires_human_review": True,
                "flags": flags,
            }
        )
    else:
        epistemic = epistemic.model_copy(update={"flags": flags})

    log.info(
        "graph.output",
        session_id=epistemic.session_id,
        verdict=epistemic.guardian_verdict,
        recommendation_count=len(epistemic.recommendations),
        edge_count=len(epistemic.proposed_edges),
    )
    return {"epistemic": epistemic, "disorder_retries": state.get("disorder_retries", 0)}


# ── Routing functions ─────────────────────────────────────────────────────────


def route_after_cynefin(
    state: PlatformState,
) -> Literal["direct_answer", "causal_analysis", "causal_discovery", "escalate_human", "disorder_protocol"]:
    """Route to the appropriate analysis node based on Cynefin domain."""
    domain = state["epistemic"].cynefin_domain
    retries = state.get("disorder_retries", 0)

    if domain == CynefinDomain.CLEAR:
        return "direct_answer"
    elif domain == CynefinDomain.COMPLICATED:
        return "causal_analysis"
    elif domain == CynefinDomain.COMPLEX:
        return "causal_discovery"
    elif domain == CynefinDomain.CHAOTIC:
        return "escalate_human"
    else:  # DISORDER
        if retries >= _MAX_DISORDER_RETRIES:
            return "escalate_human"
        return "disorder_protocol"


def route_after_disorder(
    state: PlatformState,
) -> Literal["cynefin_classify", "escalate_human"]:
    """After disorder protocol, retry classify or escalate if max retries hit."""
    retries = state.get("disorder_retries", 0)
    if retries >= _MAX_DISORDER_RETRIES or state["epistemic"].requires_human_review:
        return "escalate_human"
    return "cynefin_classify"


def route_after_plan_ahead(
    state: PlatformState,
) -> Literal["guardian_check", "escalate_human"]:
    """Route to guardian check, or escalate if planning found no safe action."""
    epistemic = state["epistemic"]
    if epistemic.requires_human_review and "no_safe_action_found" in epistemic.flags:
        return "escalate_human"
    return "guardian_check"


def route_after_guardian(
    state: PlatformState,
) -> Literal["output"]:
    """Always proceed to output after guardian check (output node handles verdict)."""
    return "output"


# ── Graph assembly ────────────────────────────────────────────────────────────


def build_platform_graph() -> StateGraph:
    """
    Build and return the compiled LangGraph StateGraph for the platform.

    Topology mirrors AGENTS.md §5.1 exactly.

    Returns an uncompiled StateGraph — call .compile() to get a runnable graph.
    """
    graph = StateGraph(PlatformState)

    # Register nodes
    graph.add_node("intake", node_intake)
    graph.add_node("cynefin_classify", node_cynefin_classify)
    graph.add_node("direct_answer", node_direct_answer)
    graph.add_node("causal_analysis", node_causal_analysis)
    graph.add_node("causal_discovery", node_causal_discovery)
    graph.add_node("plan_ahead", node_plan_ahead)             # Phase 2C: FLARE planning
    graph.add_node("guardian_check", node_guardian_check)
    graph.add_node("escalate_human", node_escalate_human)
    graph.add_node("disorder_protocol", node_disorder_protocol)
    graph.add_node("output", node_output)

    # Entry point
    graph.set_entry_point("intake")

    # Fixed edges
    graph.add_edge("intake", "cynefin_classify")

    # Cynefin router
    graph.add_conditional_edges(
        "cynefin_classify",
        route_after_cynefin,
        {
            "direct_answer": "direct_answer",
            "causal_analysis": "causal_analysis",
            "causal_discovery": "causal_discovery",
            "escalate_human": "escalate_human",
            "disorder_protocol": "disorder_protocol",
        },
    )

    # CLEAR / COMPLICATED / COMPLEX all flow to plan_ahead before guardian
    graph.add_edge("direct_answer", "plan_ahead")
    graph.add_edge("causal_analysis", "plan_ahead")
    graph.add_edge("causal_discovery", "plan_ahead")

    # Plan ahead → guardian (or escalate if no safe action)
    graph.add_conditional_edges(
        "plan_ahead",
        route_after_plan_ahead,
        {
            "guardian_check": "guardian_check",
            "escalate_human": "escalate_human",
        },
    )

    # Guardian → output
    graph.add_edge("guardian_check", "output")

    # Output → END
    graph.add_edge("output", END)

    # CHAOTIC → END (human only)
    graph.add_edge("escalate_human", END)

    # DISORDER retry loop
    graph.add_conditional_edges(
        "disorder_protocol",
        route_after_disorder,
        {
            "cynefin_classify": "cynefin_classify",
            "escalate_human": "escalate_human",
        },
    )

    return graph


def compile_platform_graph():
    """Return a compiled, runnable LangGraph graph for the platform."""
    return build_platform_graph().compile()


# ── Helper ────────────────────────────────────────────────────────────────────


def _circuit_breaker_escalation(epistemic: EpistemicState) -> EpistemicState:
    """Mark state for human escalation when the circuit breaker trips."""
    return epistemic.model_copy(
        update={
            "requires_human_review": True,
            "flags": [*epistemic.flags, "circuit_breaker_open"],
            "draft_explanation": (
                "Circuit breaker open: maximum reasoning cycles reached. "
                "Routing to human for resolution."
            ),
        }
    )

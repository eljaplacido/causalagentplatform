"""
LangGraph StateGraph for the Unified Enterprise Causal Platform.

Topology (AGENTS.md §5.1):

    Intake → CynefinClassify → [Router]
        ├── CLEAR        → DirectAnswer ────┐
        ├── COMPLICATED  → CausalAnalysis ──┼→ PlanAhead ─┬───────────────→ GuardianCheck → Output
        ├── COMPLEX      → CausalDiscovery ─┘             └→ EscalateHuman → GuardianCheck → Output
        ├── CHAOTIC      → EscalateHuman → GuardianCheck → Output
        └── DISORDER     → DisorderProtocol → CynefinClassify [retry once, then EscalateHuman]

Security invariants:
  - Every path ends GuardianCheck → Output: nothing reaches the caller without a
    Guardian verdict written to audit_log. An escalation is ruled on and recorded
    too (action_type GRAPH_HUMAN_ESCALATION): PR-003 for CHAOTIC, PR-006 for
    DISORDER. Escalating was never meant to mean "ungoverned" -- it used to end
    at END, leaving no record of the escalation or of why.
  - CHAOTIC domain routes immediately to human escalation
  - CircuitBreakerError terminates the graph and marks requires_human_review
  - All state transitions are appended to reasoning_steps (immutable audit)
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from src.agents.causal_discovery import CausalDiscoveryAgent
from src.agents.governance import GovernanceAgent
from src.agents.incident_triage import IncidentTriageAgent
from src.exceptions import CircuitBreakerError
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
    # The problem as submitted. CynefinClassify classifies this, not
    # epistemic.draft_explanation, which agents overwrite with their own narrative:
    # /incident used to re-classify the triage agent's "Incident classified as
    # COMPLICATED ..." -- no keyword, so DISORDER -- instead of the incident.
    description: NotRequired[str]
    # Set by PlanAhead for this run (planning.node_plan_ahead documents the values).
    plan_outcome: NotRequired[str | None]
    # Set by EscalateHuman: why this run went to a person. GuardianCheck records it.
    escalation_reason: NotRequired[str | None]


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
                "description": (
                    state.get("description") or epistemic.draft_explanation or epistemic.agent_id
                ),
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

    All outputs must pass the Guardian before being surfaced to the user, the
    escalations included. Writes an audit entry for every check (immutability
    contract).
    """
    epistemic = state["epistemic"]
    agent = GovernanceAgent(agent_id="guardian-check-node")
    escalation_reason = state.get("escalation_reason")

    # The payload is what the verdict is about, and its hash is what audit_log
    # binds: the recommendations the caller will see are part of it, as are the
    # plan's status and, on an escalation, why the run went to a person.
    action_payload = {
        "action_type": (
            "GRAPH_HUMAN_ESCALATION" if escalation_reason else "GRAPH_NODE_GUARDIAN_CHECK"
        ),
        "proposed_edges": [e.model_dump() for e in epistemic.proposed_edges],
        "recommendations": [r.model_dump() for r in epistemic.recommendations],
        "cynefin_domain": epistemic.cynefin_domain,
        "hallucination_risk_score": epistemic.hallucination_risk_score,
        "cycle_count": epistemic.cycle_count,
        "active_assumptions": [a.model_dump() for a in epistemic.active_assumptions],
        "plan_outcome": state.get("plan_outcome"),
        "escalation_reason": escalation_reason,
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


#: Why a run went to a person: (explanation, directive rationale), by reason.
_ESCALATIONS: dict[str, tuple[str, str]] = {
    "chaotic_domain": (
        "CHAOTIC domain: automated analysis suspended. "
        "Route this incident to your human incident commander now.",
        "CHAOTIC domain detected. Novel situation with no established causal "
        "patterns. AI analysis suppressed. Human must: Stabilise → Act → Sense.",
    ),
    "disorder_unresolved": (
        "DISORDER domain: the problem could not be classified, after one "
        "re-classification attempt, so no analysis ran. A person must classify it "
        "before anything is recommended.",
        "The Cynefin domain is unresolved (DISORDER). No analysis ran and nothing "
        "can be approved until a person classifies the problem.",
    ),
    "planning_no_safe_action": (
        "Planning found no safe action: the lookahead estimated each candidate's "
        "consequences and none survived. Escalating to human review.",
        "FLARE lookahead found no candidate action safe to commit.",
    ),
    "circuit_breaker": (
        "Circuit breaker open: maximum reasoning cycles reached. "
        "Routing to human for resolution.",
        "The reasoning-cycle limit was reached before an answer could be governed.",
    ),
}


def _escalation_reason(state: PlatformState) -> str:
    """Why this run is escalating -- from this run's state, not flags a resumed session carried."""
    epistemic = state["epistemic"]
    if state.get("plan_outcome") == "no_safe_action":
        return "planning_no_safe_action"
    if epistemic.cynefin_domain == CynefinDomain.CHAOTIC:
        return "chaotic_domain"
    if epistemic.cynefin_domain == CynefinDomain.DISORDER:
        return "disorder_unresolved"
    return "circuit_breaker"


async def node_escalate_human(state: PlatformState) -> PlatformState:
    """
    EscalateHuman node — route the run to a person, and say why.

    CHAOTIC (no automated approval, AGENTS.md), DISORDER still unresolved after one
    retry, or a lookahead that found no safe action. The run then goes on to
    GuardianCheck, which rules on the escalation and writes it to audit_log: an
    escalation is a decision about the run and is recorded like one. The Guardian
    cannot approve CHAOTIC (PR-003) or DISORDER (PR-006).
    """
    epistemic = state["epistemic"]
    reason = _escalation_reason(state)
    explanation, rationale = _ESCALATIONS[reason]
    directive = Recommendation(
        rank=1,
        action="ESCALATE: Route to human decision-maker immediately.",
        rationale=rationale,
        confidence=1.0,
        risk_tier="CRITICAL",
    )
    if reason == "chaotic_domain":
        # AI analysis suppressed: the directive alone. /incident's triage agent may
        # already have put its own escalation first; one directive, not two.
        leading = epistemic.recommendations[:1]
        if leading and leading[0].action.startswith("ESCALATE"):
            recommendations = leading
        else:
            recommendations = [directive]
    else:
        recommendations = [
            directive,
            *(r.model_copy(update={"rank": r.rank + 1}) for r in epistemic.recommendations),
        ]
    epistemic = epistemic.model_copy(
        update={
            "requires_human_review": True,
            "flags": [*epistemic.flags, "human_escalation_required", f"escalation:{reason}"],
            "guardian_verdict": GuardianVerdict.PENDING,
            "recommendations": recommendations,
            "draft_explanation": explanation,
        }
    )
    log.warning(
        "graph.escalate_human",
        session_id=epistemic.session_id,
        domain=epistemic.cynefin_domain,
        reason=reason,
    )
    return {
        "epistemic": epistemic,
        "disorder_retries": state.get("disorder_retries", 0),
        "escalation_reason": reason,
    }


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
                "draft_explanation": (
                    epistemic.draft_explanation
                    + " [REJECTED by Guardian — human review required]"
                ),
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
) -> Literal[
    "direct_answer", "causal_analysis", "causal_discovery", "escalate_human", "disorder_protocol"
]:
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
    """After disorder protocol, retry classify once; escalate past the retry limit.

    This escalated whenever ``retries >= 1`` -- which it always is here, the
    protocol having just counted itself -- so the documented retry never ran.
    ``route_after_cynefin`` escalates a second DISORDER.
    """
    if state.get("disorder_retries", 0) > _MAX_DISORDER_RETRIES:
        return "escalate_human"
    return "cynefin_classify"


def route_after_plan_ahead(
    state: PlatformState,
) -> Literal["guardian_check", "escalate_human"]:
    """Route to guardian check, or escalate if the lookahead found no safe action.

    Reads this run's plan_outcome, not the flags: a resumed session carries the
    flags of every earlier run. A plan nothing could simulate is not a finding
    that no action is safe, and goes to the Guardian (planning.node_plan_ahead).
    """
    if state.get("plan_outcome") == "no_safe_action":
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

    Topology mirrors AGENTS.md §5.1: every path ends GuardianCheck → Output.

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

    # Escalation → Guardian: the escalation is ruled on and written to audit_log
    # (PR-003 for CHAOTIC, PR-006 for DISORDER) before the run returns.
    graph.add_edge("escalate_human", "guardian_check")

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

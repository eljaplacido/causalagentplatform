"""
Session orchestration API — runs the LangGraph platform graph.

Endpoints:
  POST /api/v1/session/run        — run an EpistemicState through the full platform graph
  POST /api/v1/session/incident   — create and run an incident triage session
  GET  /api/v1/session/{id}/state — retrieve session state from cache or durable store

The session API is the primary entry point for the frontend.  Every response
includes the full EpistemicState (minus internal tool_call_log for brevity),
plus a human-readable draft_explanation and the Guardian verdict.

Security: all sessions must carry a valid auth token.  The Guardian
runs inside every graph execution — it cannot be bypassed via this API.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from src.agents.governance import GovernanceAgent
from src.agents.incident_triage import IncidentTriageAgent
from src.exceptions import CircuitBreakerError
from src.middleware.rbac import guard, may_use_session
from src.orchestration.graph import INCIDENT_TRIAGE, PlatformState, compile_platform_graph
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
)
from src.semantic_layer.provenance_capture import session_run_records
from src.semantic_layer.provenance_store import ProvenanceStore

log = structlog.get_logger(__name__)
router = APIRouter()

# Compiled graph singleton (thread-safe; LangGraph graphs are immutable once compiled)
_graph = compile_platform_graph()

# In-process hot cache (session_id → last EpistemicState)
_session_cache: dict[str, EpistemicState] = {}
#: session_id -> the uid that started it (None: saved without one), beside the cache.
_session_owner: dict[str, str | None] = {}
_session_store = SessionStateStore()
_provenance = ProvenanceStore()


async def _record_provenance(
    initial: EpistemicState,
    final: EpistemicState,
    *,
    route: str,
    user: dict[str, Any],
    resumed: bool,
) -> None:
    """Append the run's PROV records (src/semantic_layer/provenance_capture.py).

    A failure raises: the session state is already saved, but a run whose
    provenance could not be written is reported as failed rather than returned
    as if its trace were complete.
    """
    await _provenance.append(
        session_run_records(
            initial, final, route=route, user_id=user.get("uid"), resumed=resumed
        )
    )


# ── Request / Response models ─────────────────────────────────────────────────


class RunSessionRequest(BaseModel):
    """
    Request body for POST /api/v1/session/run.

    Provide an existing session_id to continue a session, or omit for a new one.
    """

    session_id: str | None = Field(
        None,
        description="Existing session ID to continue. Omit to start a new session.",
    )
    agent_id: str = Field(
        "platform-agent",
        description="Identifier for the agent driving this session.",
    )
    description: str = Field(
        ...,
        description="Problem description or incident summary to analyse.",
        min_length=1,
        max_length=2000,
    )
    active_graph_fragment_id: str | None = Field(
        None,
        description="Graph fragment to load as context for the session.",
    )

    @model_validator(mode="before")
    @classmethod
    def _no_declared_domain(cls, data: Any) -> Any:
        # `cynefin_domain` was offered as an override and overrode nothing: the
        # graph's CynefinClassify node always replaces the domain with its own. It
        # is not honoured instead, because the domain is what PR-003 and PR-006
        # judge and classifying it is the platform's (docs/AGENTS.md): /check takes
        # a caller's domain only when no session has one, and a session run always
        # has one. Refused loudly, as /check refuses the scores it retired, so a
        # client still sending it learns that it does not count.
        if isinstance(data, dict) and "cynefin_domain" in data:
            raise ValueError(
                "cynefin_domain is classified by the platform (CynefinClassify), not "
                "supplied by the caller; a declared domain is judged only by "
                "POST /api/governance/check without a session_id"
            )
        return data


class IncidentSessionRequest(BaseModel):
    """
    Request body for POST /api/v1/session/incident — fast-path incident triage.
    """

    incident_id: str = Field(..., description="External incident ID (from PagerDuty, Alertmanager, etc.)")
    title: str = Field(..., description="Incident title")
    severity: str = Field("P2", description="P0 | P1 | P2 | P3")
    affected_services: list[str] = Field(default_factory=list)
    metrics_snapshot: dict[str, float] = Field(default_factory=dict)
    agent_id: str = Field("incident-triage-agent", description="Agent driving this triage")


class SessionStateResponse(BaseModel):
    """Trimmed EpistemicState returned to the client."""

    session_id: str
    agent_id: str
    cynefin_domain: str
    domain_confidence: float
    guardian_verdict: str
    policy_rules_cited: list[str]
    overall_confidence: float
    requires_human_review: bool
    flags: list[str]
    draft_explanation: str
    proposed_edge_count: int
    recommendation_count: int
    recommendations: list[dict[str, Any]]
    cycle_count: int
    timestamp: str


class IncidentSessionResponse(SessionStateResponse):
    """The triage result, and whether the incident itself was stored.

    Triage and its Guardian verdict are recorded either way (audit log, session
    store, provenance); `stored` says whether the incident row the dashboard lists
    was written. It used to be dropped silently on failure: 200, nothing stored.

    `interventions` are the proposed interventions as the incident row stores them,
    each with the verdict the Guardian gave that intervention (`guardianVerdict`)
    and the audit_log row it wrote (`guardianVerdictRef`).
    """

    stored: bool
    store_error: str | None = None
    interventions: list[dict[str, Any]] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _state_to_response(state: EpistemicState) -> SessionStateResponse:
    """Trim EpistemicState to client-safe response (strips tool_call_log)."""
    return SessionStateResponse(
        session_id=state.session_id,
        agent_id=state.agent_id,
        cynefin_domain=str(state.cynefin_domain),
        domain_confidence=state.domain_confidence,
        guardian_verdict=str(state.guardian_verdict),
        policy_rules_cited=state.policy_rules_cited,
        overall_confidence=state.overall_confidence,
        requires_human_review=state.requires_human_review,
        flags=state.flags,
        draft_explanation=state.draft_explanation,
        proposed_edge_count=len(state.proposed_edges),
        recommendation_count=len(state.recommendations),
        recommendations=[
            {
                "rank": r.rank,
                "action": r.action,
                "rationale": r.rationale,
                "confidence": r.confidence,
                "risk_tier": r.risk_tier,
            }
            for r in state.recommendations[:10]
        ],
        cycle_count=state.cycle_count,
        timestamp=state.timestamp.isoformat() if hasattr(state.timestamp, "isoformat") else str(state.timestamp),
    )


#: The agent_id of the unsigned audit row a fallback in _run_graph writes.
_FALLBACK_RECORDER = "session-graph-fallback"


async def _run_graph(
    initial_state: EpistemicState,
    description: str,
    *,
    analysis: str | None = None,
) -> tuple[EpistemicState, str | None]:
    """
    Run the platform LangGraph pipeline on the problem ``description``.

    Returns the final state and this run's plan outcome (None when the run never
    planned). ``analysis`` selects the analysis a classified run gets
    (graph.PlatformState).

    Every path through the graph ends GuardianCheck → Output, so a returned state
    carries a Guardian verdict that was written to audit_log.

    Wraps circuit breaker and graph errors into a terminal EpistemicState rather
    than raising HTTP exceptions — the state itself communicates the outcome. Such
    a state never reached the Guardian, so it carries no recommendation: nothing
    ungoverned is presented. Its verdict is the API's, not a ruling, and is written
    to audit_log as one: unsigned, with why (GovernanceAgent.record_unruled). It
    used to leave no record at all. If that row cannot be written, the run fails
    (AuditWriteError) rather than answer with a verdict nothing records.
    """
    platform_state: PlatformState = {
        "epistemic": initial_state,
        "disorder_retries": 0,
        "description": description,
        "plan_outcome": None,
        "escalation_reason": None,
        "analysis": analysis,
    }

    try:
        result: PlatformState = await _graph.ainvoke(platform_state)  # type: ignore[arg-type]
        return result["epistemic"], result.get("plan_outcome")
    except CircuitBreakerError as exc:
        log.warning(
            "session.circuit_breaker",
            session_id=initial_state.session_id,
            cycle_count=exc.cycle_count,
        )
        final = initial_state.model_copy(
            update={
                "requires_human_review": True,
                "flags": [*initial_state.flags, "circuit_breaker_open"],
                "recommendations": [],
                "draft_explanation": (
                    f"Circuit breaker opened after {exc.cycle_count} cycles. "
                    "Escalating to human reviewer."
                ),
                "guardian_verdict": GuardianVerdict.REQUIRES_ESCALATION,
            }
        )
        action_type = "GRAPH_CIRCUIT_BREAKER"
        why = (
            f"Session run stopped by the circuit breaker after {exc.cycle_count} cycles, "
            "before GuardianCheck: no policy ran. REQUIRES_ESCALATION was set by the "
            "session API, not ruled by the Guardian."
        )
    except Exception as exc:
        log.error(
            "session.graph_error",
            session_id=initial_state.session_id,
            error=str(exc),
        )
        final = initial_state.model_copy(
            update={
                "requires_human_review": True,
                "flags": [*initial_state.flags, "graph_internal_error"],
                "recommendations": [],
                "draft_explanation": (
                    "Internal platform error during session execution. "
                    "Escalating to human reviewer."
                ),
                "guardian_verdict": GuardianVerdict.REJECTED,
            }
        )
        action_type = "GRAPH_INTERNAL_ERROR"
        why = (
            f"Session run failed before GuardianCheck ({type(exc).__name__}: {exc}): "
            "no policy ran. REJECTED was set by the session API, not ruled by the "
            "Guardian."
        )

    await GovernanceAgent(agent_id=_FALLBACK_RECORDER).record_unruled(
        action_type=action_type,
        verdict=final.guardian_verdict,
        notes=why,
        session_id=final.session_id,
    )
    return final, None


async def _owned_session(session_id: str, user: dict[str, Any]) -> EpistemicState | None:
    """The session's state, or None when there is none; 403 when it is another user's."""
    state = _session_cache.get(session_id)
    if state is None or session_id not in _session_owner:
        found = await _session_store.load_with_owner(session_id)
        if found is None:
            return None
        state, _session_owner[session_id] = found
        _session_cache[session_id] = state
    if not may_use_session(user, _session_owner[session_id]):
        log.warning("session.access_refused", session_id=session_id, uid=user.get("uid"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Session '{session_id}' belongs to another user.",
        )
    return state


async def _remember(state: EpistemicState, user: dict[str, Any]) -> None:
    """Cache and persist `state`; a new session's owner is `user`."""
    _session_cache[state.session_id] = state
    _session_owner.setdefault(state.session_id, user.get("uid"))
    await _session_store.save(state, user.get("uid"))


def _resumed(existing: EpistemicState, body: RunSessionRequest) -> EpistemicState:
    """The state a resumed run starts from: the session's context, not its last result.

    A run's outputs -- flags, recommendations, proposed edges, verdict, the
    review flag -- and its cycle count are the run's own, and start afresh. They
    used to carry into the next run and pile up: every resume added its flags and
    recommendations to the last run's, and the cycle count grew until PR-005
    escalated (and the circuit breaker refused) every run of the session, five
    runs in. The session keeps what it is a session for: its graph fragment,
    assumptions and evidence, and its history (reasoning steps, tool calls).
    """
    return existing.model_copy(
        update={
            "draft_explanation": body.description,
            "agent_id": body.agent_id,
            "flags": [],
            "recommendations": [],
            "proposed_edges": [],
            "simulation_results": [],
            "cycle_count": 0,
            "guardian_verdict": GuardianVerdict.PENDING,
            "policy_rules_cited": [],
            "requires_human_review": False,
            "overall_confidence": 0.0,
        }
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post(
    "/run",
    response_model=SessionStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a platform analysis session",
    description=(
        "Runs the full LangGraph platform pipeline: Intake → CynefinClassify → "
        "[domain-specific analysis] → GuardianCheck → Output. "
        "Returns the final EpistemicState with recommendations and Guardian verdict."
    ),
)
async def run_session(
    body: RunSessionRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/session/run")),
) -> SessionStateResponse:
    """
    Run an EpistemicState through the full platform graph.

    The session is cached in-process and persisted to the SQL session store by
    session_id. Provide the same session_id to continue a multi-turn session
    (e.g., after expert elicitation).
    """
    session_id = body.session_id or str(uuid.uuid4())

    # Resume existing session if available
    existing = await _owned_session(session_id, user)

    if existing:
        initial_state = _resumed(existing, body)
    else:
        initial_state = EpistemicState(
            session_id=session_id,
            agent_id=body.agent_id,
            cynefin_domain=CynefinDomain.DISORDER,  # CynefinClassify decides
            active_graph_fragment_id=body.active_graph_fragment_id,
            draft_explanation=body.description,
        )

    log.info(
        "session.run.started",
        session_id=session_id,
        user_id=user.get("uid", "unknown"),
        agent_id=body.agent_id,
        resumed=existing is not None,
    )

    final, _ = await _run_graph(initial_state, body.description)

    # Cache for potential continuation
    await _remember(final, user)
    await _record_provenance(
        initial_state,
        final,
        route="POST /api/v1/session/run",
        user=user,
        resumed=existing is not None,
    )

    log.info(
        "session.run.complete",
        session_id=session_id,
        verdict=final.guardian_verdict,
        recommendations=len(final.recommendations),
    )

    return _state_to_response(final)


@router.post(
    "/incident",
    response_model=IncidentSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Triage an operational incident",
    description=(
        "Fast-path incident triage endpoint for the DevOps vertical. "
        "Creates an EpistemicState from the incident payload and runs it through the "
        "platform graph, whose IncidentTriage node is the incident's one analysis, "
        "then GuardianCheck + Output. Each proposed intervention gets a Guardian "
        "verdict of its own (`interventions`). "
        "CHAOTIC-domain incidents escalate without AI analysis; the escalation is "
        "ruled on by the Guardian and recorded in the audit log. `stored` is false "
        "(with `store_error`) when the incident itself could not be saved: the triage "
        "stands, but the incident will not be listed."
    ),
)
async def triage_incident(
    body: IncidentSessionRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/session/incident")),
) -> IncidentSessionResponse:
    """
    Dedicated incident triage endpoint for the SRE / Incident Commander persona.

    Severity P0 incidents pre-seed the description with an urgency signal that
    biases Cynefin classification toward CHAOTIC for immediate escalation.
    """
    from src.api.incidents import incident_submitter  # lazy import — avoids circular dependency

    submitter = _submitter(user)
    taken_by = await incident_submitter(body.incident_id)
    if taken_by is not None and taken_by != submitter:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident '{body.incident_id}' was submitted by another user.",
        )

    session_id = str(uuid.uuid4())
    urgency_prefix = "CRITICAL CASCADING " if body.severity == "P0" else ""
    description = (
        f"{urgency_prefix}{body.title}. "
        f"Affected services: {', '.join(body.affected_services) or 'unknown'}. "
        f"Incident ID: {body.incident_id}. Severity: {body.severity}."
    )

    initial_state = EpistemicState(
        session_id=session_id,
        agent_id=body.agent_id,
        cynefin_domain=CynefinDomain.DISORDER,
        draft_explanation=description,
    )

    log.info(
        "session.incident.started",
        session_id=session_id,
        incident_id=body.incident_id,
        severity=body.severity,
        user_id=user.get("uid", "unknown"),
    )

    # Every incident goes through the graph, CHAOTIC included, and every path ends
    # in a Guardian verdict written to audit_log. The triage is the graph's analysis
    # (its IncidentTriage node), so an incident is analysed once and ruled on once.
    # It used to be analysed twice -- IncidentTriageAgent.process here, with a
    # Guardian check of its own, then the graph's CausalAnalysis over its output,
    # with another -- and CHAOTIC incidents used to skip the graph entirely.
    final, plan_outcome = await _run_graph(
        initial_state, description, analysis=INCIDENT_TRIAGE
    )
    if "pipeline_complete" in final.flags and final.cynefin_domain in _TRIAGED_DOMAINS:
        await IncidentTriageAgent(agent_id=body.agent_id).store_experience(final)
    await _remember(final, user)
    await _record_provenance(
        initial_state,
        final,
        route="POST /api/v1/session/incident",
        user=user,
        resumed=False,
    )
    interventions = await _intervention_verdicts(final, plan_outcome)
    store_error = await _persist_incident(body, final, user, interventions)

    log.info(
        "session.incident.complete",
        session_id=session_id,
        incident_id=body.incident_id,
        verdict=final.guardian_verdict,
        domain=final.cynefin_domain,
        stored=store_error is None,
    )

    return IncidentSessionResponse(
        **_state_to_response(final).model_dump(),
        stored=store_error is None,
        store_error=store_error,
        interventions=interventions,
    )


#: The domains an incident run analyses (IncidentTriage); the rest escalate.
_TRIAGED_DOMAINS = (CynefinDomain.CLEAR, CynefinDomain.COMPLICATED, CynefinDomain.COMPLEX)

#: The agent_id of the Guardian checks that rule on each proposed intervention.
_INTERVENTION_GUARDIAN = "guardian-intervention-check"


async def _intervention_verdicts(
    state: EpistemicState, plan_outcome: str | None
) -> list[dict[str, Any]]:
    """The run's proposed interventions, each with a Guardian verdict of its own.

    The Guardian rules on every intervention the incident row will store, one
    check and one audit_log row each, on a payload that names that intervention
    beside the run's context (domain, edges, assumptions, plan outcome) -- the
    same context the run's own check was given, so an intervention is never
    judged on less. Each stored intervention used to carry the whole session's
    verdict instead, which the Guardian had never given that intervention.

    The checks name no subject: their verdicts are read, never spent on a write.
    """
    guardian = GovernanceAgent(agent_id=_INTERVENTION_GUARDIAN)
    context = {
        "cynefin_domain": state.cynefin_domain,
        "hallucination_risk_score": state.hallucination_risk_score,
        "cycle_count": state.cycle_count,
        "proposed_edges": [e.model_dump() for e in state.proposed_edges],
        "active_assumptions": [a.model_dump() for a in state.active_assumptions],
        "plan_outcome": plan_outcome,
    }
    interventions: list[dict[str, Any]] = []
    for rec in state.recommendations[:10]:
        intervention = {
            "id": f"int-{rec.rank}-{state.session_id}",
            "title": rec.action,
            "description": rec.rationale,
            "confidence": rec.confidence,
            "riskTier": rec.risk_tier,
            "rank": rec.rank,
        }
        result = await guardian.check(
            {
                "action_type": "INCIDENT_INTERVENTION_CHECK",
                "intervention": intervention,
                **context,
            },
            state,
        )
        interventions.append(
            {
                **intervention,
                "guardianVerdict": str(result.verdict),
                "guardianVerdictRef": str(result.audit_entry_id),
                "policyRulesCited": list(result.policy_rules_cited),
            }
        )
    return interventions


def _submitter(user: dict[str, Any]) -> str:
    """The id an incident records as its submitter."""
    return str(user.get("uid") or user.get("email") or "unknown")


async def _persist_incident(
    body: IncidentSessionRequest,
    state: EpistemicState,
    user: dict[str, Any],
    proposed_interventions: list[dict[str, Any]],
) -> str | None:
    """Durably store the incident row so the dashboard rehydrates after reload.

    Returns None when it was stored, else why not. The triage response still
    goes out (its verdict is already audited), saying the incident was not stored.
    """
    from src.api.incidents import save_incident  # lazy import — avoids circular dependency

    causal_hypotheses = (
        [
            {
                "id": f"hyp-{state.session_id}",
                "rank": 1,
                "description": state.draft_explanation,
                "confidence": state.overall_confidence,
            }
        ]
        if state.draft_explanation
        else []
    )
    try:
        saved = await save_incident(
            incident_id=body.incident_id,
            title=body.title,
            severity=body.severity,
            affected_services=body.affected_services,
            session_id=state.session_id,
            cynefin_domain=str(state.cynefin_domain),
            domain_confidence=state.domain_confidence,
            overall_confidence=state.overall_confidence,
            guardian_verdict=str(state.guardian_verdict),
            draft_explanation=state.draft_explanation,
            causal_hypotheses=causal_hypotheses,
            proposed_interventions=proposed_interventions,
            submitted_by=_submitter(user),
            requires_human_review=state.requires_human_review,
        )
    except Exception as exc:
        # The triage response still goes out -- its verdict is audited -- but says
        # the incident was not stored, instead of a 200 with nothing behind it.
        log.error(
            "incident.persist_failed",
            incident_id=body.incident_id,
            error=str(exc),
        )
        return f"the incident could not be saved: {type(exc).__name__}: {exc}"
    if not saved:
        # Another submitter took the id between the route's 409 check and the save.
        log.error("incident.persist_refused", incident_id=body.incident_id)
        return f"incident '{body.incident_id}' is stored under another submitter"
    return None


@router.get(
    "/{session_id}/state",
    response_model=SessionStateResponse,
    summary="Retrieve cached session state",
)
async def get_session_state(
    session_id: str,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/session/{session_id}/state")),
) -> SessionStateResponse:
    """Return the cached or durably persisted EpistemicState for a session_id.

    Its owner and governance roles only (403 for anyone else)."""
    state = await _owned_session(session_id, user)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found in cache or durable session store.",
        )
    return _state_to_response(state)

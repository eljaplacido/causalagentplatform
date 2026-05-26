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
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.agents.incident_triage import IncidentTriageAgent
from src.exceptions import CircuitBreakerError, GuardianBlockedError
from src.middleware.auth import get_current_user
from src.orchestration.graph import PlatformState, compile_platform_graph
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
)

log = structlog.get_logger(__name__)
router = APIRouter()

# Compiled graph singleton (thread-safe; LangGraph graphs are immutable once compiled)
_graph = compile_platform_graph()

# In-process hot cache (session_id → last EpistemicState)
_session_cache: dict[str, EpistemicState] = {}
_session_store = SessionStateStore()


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
    cynefin_domain: CynefinDomain | None = Field(
        None,
        description="Override Cynefin domain. Omit to let the agent classify.",
    )
    active_graph_fragment_id: str | None = Field(
        None,
        description="Graph fragment to load as context for the session.",
    )


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


async def _run_graph(initial_state: EpistemicState) -> EpistemicState:
    """
    Run the platform LangGraph pipeline.

    Wraps circuit breaker and Guardian errors into a terminal EpistemicState
    rather than raising HTTP exceptions — the state itself communicates the outcome.
    """
    platform_state: PlatformState = {
        "epistemic": initial_state,
        "disorder_retries": 0,
    }

    try:
        result: PlatformState = await _graph.ainvoke(platform_state)  # type: ignore[arg-type]
        final: EpistemicState = result["epistemic"]
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
                "draft_explanation": (
                    f"Circuit breaker opened after {exc.cycle_count} cycles. "
                    "Escalating to human reviewer."
                ),
                "guardian_verdict": GuardianVerdict.REQUIRES_ESCALATION,
            }
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
                "draft_explanation": (
                    f"Internal platform error during session execution. "
                    "Escalating to human reviewer."
                ),
                "guardian_verdict": GuardianVerdict.REJECTED,
            }
        )

    return final


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
    user: dict[str, Any] = Depends(get_current_user),
) -> SessionStateResponse:
    """
    Run an EpistemicState through the full platform graph.

    The session is cached in-process and persisted to the SQL session store by
    session_id. Provide the same session_id to continue a multi-turn session
    (e.g., after expert elicitation).
    """
    session_id = body.session_id or str(uuid.uuid4())

    # Resume existing session if available
    existing = _session_cache.get(session_id)
    if existing is None:
        existing = await _session_store.load(session_id)
        if existing is not None:
            _session_cache[session_id] = existing

    if existing:
        initial_state = existing.model_copy(
            update={
                "draft_explanation": body.description,
                "agent_id": body.agent_id,
            }
        )
    else:
        initial_state = EpistemicState(
            session_id=session_id,
            agent_id=body.agent_id,
            cynefin_domain=body.cynefin_domain or CynefinDomain.DISORDER,
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

    final = await _run_graph(initial_state)

    # Cache for potential continuation
    _session_cache[session_id] = final
    await _session_store.save(final, user.get("uid"))

    log.info(
        "session.run.complete",
        session_id=session_id,
        verdict=final.guardian_verdict,
        recommendations=len(final.recommendations),
    )

    return _state_to_response(final)


@router.post(
    "/incident",
    response_model=SessionStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Triage an operational incident",
    description=(
        "Fast-path incident triage endpoint for the DevOps vertical. "
        "Creates an EpistemicState from the incident payload, runs IncidentTriageAgent "
        "directly, then routes to the full platform graph for GuardianCheck + Output. "
        "CHAOTIC-domain incidents escalate immediately without AI analysis."
    ),
)
async def triage_incident(
    body: IncidentSessionRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> SessionStateResponse:
    """
    Dedicated incident triage endpoint for the SRE / Incident Commander persona.

    Severity P0 incidents pre-seed the description with an urgency signal that
    biases Cynefin classification toward CHAOTIC for immediate escalation.
    """
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

    # Run IncidentTriageAgent first (builds root-cause proposals + cynefin domain)
    triage_agent = IncidentTriageAgent(agent_id=body.agent_id)
    try:
        triaged_state = await triage_agent.process(initial_state)
    except CircuitBreakerError:
        triaged_state = initial_state.model_copy(
            update={
                "requires_human_review": True,
                "flags": ["circuit_breaker_open"],
                "guardian_verdict": GuardianVerdict.REQUIRES_ESCALATION,
                "draft_explanation": "Circuit breaker: escalating to human.",
            }
        )

    # If CHAOTIC, skip the graph (already escalated by IncidentTriageAgent)
    if triaged_state.cynefin_domain == CynefinDomain.CHAOTIC:
        _session_cache[session_id] = triaged_state
        await _session_store.save(triaged_state, user.get("uid"))
        await _persist_incident(body, triaged_state, user)
        return _state_to_response(triaged_state)

    # Otherwise run through the full graph for GuardianCheck + Output
    final = await _run_graph(triaged_state)
    _session_cache[session_id] = final
    await _session_store.save(final, user.get("uid"))
    await _persist_incident(body, final, user)

    log.info(
        "session.incident.complete",
        session_id=session_id,
        incident_id=body.incident_id,
        verdict=final.guardian_verdict,
        domain=final.cynefin_domain,
    )

    return _state_to_response(final)


async def _persist_incident(
    body: "IncidentSessionRequest",
    state: EpistemicState,
    user: dict[str, Any],
) -> None:
    """Durably store the incident row so the dashboard rehydrates after reload."""
    from src.api.incidents import save_incident  # lazy import — avoids circular dependency

    proposed_interventions = [
        {
            "id": f"int-{rec.rank}-{state.session_id}",
            "title": rec.action,
            "description": rec.rationale,
            "confidence": rec.confidence,
            "riskTier": rec.risk_tier,
            "guardianVerdict": str(state.guardian_verdict),
            "rank": rec.rank,
        }
        for rec in state.recommendations[:10]
    ]
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
        await save_incident(
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
            submitted_by=user.get("uid") or user.get("email") or "unknown",
            requires_human_review=state.requires_human_review,
        )
    except Exception as exc:
        # Persistence failure must not break the triage response — log and continue
        log.warning(
            "incident.persist_failed",
            incident_id=body.incident_id,
            error=str(exc),
        )


@router.get(
    "/{session_id}/state",
    response_model=SessionStateResponse,
    summary="Retrieve cached session state",
)
async def get_session_state(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> SessionStateResponse:
    """Return the cached or durably persisted EpistemicState for a session_id."""
    state = _session_cache.get(session_id)
    if state is None:
        state = await _session_store.load(session_id)
        if state is not None:
            _session_cache[session_id] = state
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found in cache or durable session store.",
        )
    return _state_to_response(state)

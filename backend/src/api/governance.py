"""
Governance API routes.

Endpoints:
  POST /api/governance/check                — invoke Guardian check
  POST /api/governance/approve/{object_id}  — approve a draft (GOVERNANCE_OFFICER only)
  POST /api/governance/reject/{object_id}   — reject with reason
  GET  /api/governance/review-queue         — list pending drafts by risk tier
  GET  /api/governance/audit-log            — paginated audit log

Role requirements (the rows of src/middleware/rbac.py ROUTE_ROLES):
  - /check          → graph_author, governance_officer, platform_engineer or agent
  - /approve        → governance_officer or platform_engineer
  - /reject         → governance_officer or platform_engineer
  - /review-queue   → governance_officer or platform_engineer
  - /audit-log      → governance_officer or platform_engineer

/approve, /reject and /review-queue are also bounded by the user's domain scope:
an object outside it is not found, and is not listed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import bindparam, column, func, select, table

from src.agents.governance import GovernanceAgent
from src.agents.grounding_risk import grounding_risk
from src.db.factory import CloudSqlFactory
from src.exceptions import GuardianBlockedError, InvalidTransitionError, ObjectNotFoundError
from src.middleware.rbac import GOVERNANCE_ROLES, domain_scope, guard
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import (
    AuditEntry,
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry

log = structlog.get_logger(__name__)
router = APIRouter()

#: The audit_log columns GET /audit-log returns, as a lightweight Core table.
#: Untyped on purpose: values are bound through bindparam() and typed by the driver,
#: as they would be in text() (a typed bind renders a cast under asyncpg).
_AUDIT_LOG = table(
    "audit_log",
    column("id"),
    column("agent_id"),
    column("action_type"),
    column("payload_hash"),
    column("guardian_verdict"),
    column("policy_rules_cited"),
    column("session_id"),
    column("created_at"),
    column("subject_id"),
    column("notes"),
)

_registry = SemanticLayerRegistry()
_governance = GovernanceAgent(agent_id="governance-agent-api")
_sessions = SessionStateStore()

__all__ = ["GOVERNANCE_ROLES", "router"]


# ── Request / Response schemas ────────────────────────────────────────────────


class GuardianCheckRequest(BaseModel):
    """Request body for POST /api/governance/check."""

    action_type: str = Field(..., description="Type of action being checked")
    payload: dict[str, Any] = Field(
        ..., description="The action payload to be evaluated by the Guardian"
    )
    subject_id: UUID | None = Field(
        None,
        description="The object this verdict would authorise a write to. Without it the "
        "verdict records what was checked but authorises nothing, and /approve will "
        "refuse it.",
    )
    cynefin_domain: CynefinDomain | None = Field(
        None,
        description="The situation's Cynefin domain, as classified by the caller. "
        "Omitting it means DISORDER, which rule PR-006 rejects: the platform does not "
        "approve actions in a situation nobody has classified. Ignored when `session_id` "
        "names a session: the session's own classification is used.",
    )
    narrative: str | None = Field(
        None,
        description="Generated prose this action would publish or act on. Guardian rule "
        "PR-002 scores it: every number must be a claim of `outcome`, and causal wording "
        "needs a supported causal result.",
    )
    outcome: dict[str, Any] | None = Field(
        None,
        description="The kernel outcome (as causal_core.analyze returns it) the narrative "
        "reports. A narrative with numbers or causal wording and no outcome is ungrounded.",
    )
    session_id: str | None = Field(
        None,
        description="A reasoning session (POST /api/v1/session). When it names one, PR-005 "
        "reads that session's cycle count and PR-003/PR-006 its classified domain.",
    )

    @model_validator(mode="before")
    @classmethod
    def _no_self_assessment(cls, data: Any) -> Any:
        # These two were read straight into the policy state, so a caller chose the
        # numbers PR-002 and PR-005 judged. Refusing them loudly beats ignoring them:
        # a client still sending a score should learn that it no longer counts.
        if isinstance(data, dict):
            retired = sorted(k for k in ("hallucination_risk_score", "cycle_count") if k in data)
            if retired:
                raise ValueError(
                    f"{retired} are computed by the platform, not supplied by the caller: "
                    "send `narrative` and `outcome` for PR-002, and a `session_id` for PR-005"
                )
        return data


class GuardianCheckResponse(BaseModel):
    verdict: str
    reason: str
    policy_rules_cited: list[str]
    audit_entry_id: str
    session_id: str | None


class ApproveRequest(BaseModel):
    """Request body for POST /api/governance/approve/{object_id}."""

    object_type: str = Field(
        ...,
        description="CausalEdge | GraphFragment | CausalEntity",
    )
    verdict_id: UUID = Field(
        ...,
        description="ID of the Guardian APPROVED audit_log entry authorising this approval",
    )
    notes: str | None = Field(None, description="Optional approval notes")


class RejectRequest(BaseModel):
    """Request body for POST /api/governance/reject/{object_id}."""

    object_type: str
    reason: str = Field(..., min_length=10, description="Reason for rejection")
    verdict_id: UUID = Field(
        ...,
        description="ID of the Guardian REJECTED audit_log entry",
    )


class ReviewQueueItem(BaseModel):
    id: str
    object_type: str
    domain: str
    name: str
    approval_status: str
    created_at: str | None


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    total: int
    page: int
    page_size: int


# ── Guardian check ────────────────────────────────────────────────────────────


@router.post(
    "/check",
    response_model=GuardianCheckResponse,
    summary="Invoke the Guardian policy check",
)
async def guardian_check(
    body: GuardianCheckRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/governance/check")),
) -> GuardianCheckResponse:
    """
    Submit an action payload to the Guardian for policy evaluation.

    Returns APPROVED / REJECTED / REQUIRES_ESCALATION.
    Every call writes an immutable audit_log entry regardless of verdict.
    """
    uid = user.get("uid", "unknown")
    session_id = body.session_id or f"api-check-{uid}"

    # The state is what the policy rules read, and none of its judged numbers come
    # from this request. The risk score PR-002 reads is computed from the narrative
    # and the outcome it reports. The cycle count PR-005 reads, and a domain the
    # platform classified, come from the named session when there is one. Without a
    # session there are no reasoning cycles to count, and the domain is the
    # caller's declaration -- omitted, it is DISORDER, which PR-006 rejects.
    risk = grounding_risk(body.narrative, body.outcome)
    stored = await _sessions.load(body.session_id) if body.session_id else None
    domain = stored.cynefin_domain if stored is not None else body.cynefin_domain
    state = EpistemicState(
        session_id=session_id,
        agent_id=f"api-user-{uid}",
        cycle_count=stored.cycle_count if stored is not None else 0,
        hallucination_risk_score=risk.score,
        **({"cynefin_domain": domain} if domain else {}),
    )

    action_payload: dict[str, Any] = {
        "action_type": body.action_type,
        **body.payload,
    }
    if body.subject_id is not None:
        action_payload["subject_id"] = str(body.subject_id)

    log.info(
        "api.governance.check",
        action_type=body.action_type,
        uid=uid,
        session_id=session_id,
    )

    result = await _governance.check(action_payload, state)
    reason = result.reason
    if "PR-002" in result.policy_rules_cited and risk.reasons:
        reason = f"{reason} {'; '.join(risk.reasons)}"

    return GuardianCheckResponse(
        verdict=str(result.verdict),
        reason=reason,
        policy_rules_cited=result.policy_rules_cited,
        audit_entry_id=str(result.audit_entry_id),
        session_id=result.session_id,
    )


# ── Approve ───────────────────────────────────────────────────────────────────


@router.post(
    "/approve/{object_id}",
    response_model=dict[str, str],
    summary="Approve a draft semantic object",
)
async def approve_object(
    object_id: UUID,
    body: ApproveRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/governance/approve/{object_id}")),
) -> dict[str, str]:
    """
    Promote a DRAFT / UNDER_REVIEW semantic object to APPROVED status.

    Requires governance_officer or platform_engineer role.

    The caller must supply a valid Guardian verdict_id (from a previous
    /check call) that has verdict=APPROVED.  The registry validates this
    against audit_log before making any changes.
    """
    uid = user.get("uid", "unknown")
    approver = UserRef(id=uid, email=user.get("email"), name=user.get("name"))

    log.info(
        "api.governance.approve",
        object_id=str(object_id),
        object_type=body.object_type,
        verdict_id=str(body.verdict_id),
        uid=uid,
    )

    try:
        await _registry.promote_to_approved(
            object_id=object_id,
            object_type=body.object_type,
            verdict_id=body.verdict_id,
            approver=approver,
            domains=domain_scope(user),
        )
    except GuardianBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # What the approver said, in the trail, as text. Written unsigned, so the
    # record of a human decision can never be spent as authority for a write.
    decision_id = await _governance.record_decision(
        action_type="HUMAN_APPROVAL",
        subject_id=str(object_id),
        verdict=GuardianVerdict.APPROVED,
        notes=body.notes,
        actor_id=uid,
        session_id=f"api-approve-{uid}",
    )

    return {
        "status": "approved",
        "object_id": str(object_id),
        "object_type": body.object_type,
        "approved_by": uid,
        "verdict_id": str(body.verdict_id),
        "audit_entry_id": str(decision_id),
    }


# ── Reject ────────────────────────────────────────────────────────────────────


@router.post(
    "/reject/{object_id}",
    response_model=dict[str, str],
    summary="Reject a draft semantic object",
)
async def reject_object(
    object_id: UUID,
    body: RejectRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/governance/reject/{object_id}")),
) -> dict[str, str]:
    """
    Mark a draft semantic object as REJECTED and write an audit entry.

    A REJECTED object cannot be promoted without submitting a new draft.
    The reason is stored in the audit trail.
    """
    uid = user.get("uid", "unknown")

    log.info(
        "api.governance.reject",
        object_id=str(object_id),
        object_type=body.object_type,
        uid=uid,
        reason=body.reason[:100],
    )

    # The verdict is checked, not merely required. This endpoint used to accept any
    # UUID here and instead run a *fresh* Guardian check to manufacture an audit row
    # -- one whose comment claimed it would be APPROVED, while in fact every check
    # returned REJECTED for having no classified domain. Two bugs cancelling out.
    rejector = UserRef(id=uid, email=user.get("email"), name=user.get("name"))
    try:
        await _registry.reject_draft(
            object_id=object_id,
            object_type=body.object_type,
            verdict_id=body.verdict_id,
            rejector=rejector,
            reason=body.reason,
            domains=domain_scope(user),
        )
    except GuardianBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    decision_id = await _governance.record_decision(
        action_type="HUMAN_REJECTION",
        subject_id=str(object_id),
        verdict=GuardianVerdict.REJECTED,
        notes=body.reason,
        actor_id=uid,
        session_id=f"api-reject-{uid}",
    )

    return {
        "status": "rejected",
        "object_id": str(object_id),
        "object_type": body.object_type,
        "rejected_by": uid,
        "audit_entry_id": str(decision_id),
        "reason": body.reason,
    }


# ── Review queue ──────────────────────────────────────────────────────────────


@router.get(
    "/review-queue",
    response_model=list[ReviewQueueItem],
    summary="List pending drafts ordered by risk tier",
)
async def review_queue(
    domain: Annotated[str | None, Query(description="Filter by domain")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user: dict[str, Any] = Depends(guard("GET", "/api/governance/review-queue")),
) -> list[ReviewQueueItem]:
    """
    Return all DRAFT / UNDER_REVIEW semantic objects pending review.

    Ordered chronologically (oldest first = highest priority).
    Requires governance_officer or platform_engineer role.
    """
    log.info("api.governance.review_queue", domain=domain, limit=limit)
    items = await _registry.list_pending_review_queue(limit=limit, domains=domain_scope(user))

    if domain:
        items = [i for i in items if i.get("domain") == domain]

    return [ReviewQueueItem(**item) for item in items]


# ── Audit log ─────────────────────────────────────────────────────────────────


@router.get(
    "/audit-log",
    response_model=AuditLogResponse,
    summary="Query the governance audit log",
)
async def get_audit_log(
    from_ts: Annotated[
        datetime | None,
        Query(alias="from", description="Start timestamp (ISO 8601)"),
    ] = None,
    to_ts: Annotated[
        datetime | None,
        Query(alias="to", description="End timestamp (ISO 8601)"),
    ] = None,
    agent_id: Annotated[
        str | None,
        Query(description="Filter by agent_id"),
    ] = None,
    verdict: Annotated[
        GuardianVerdict | None,
        Query(description="Filter by Guardian verdict"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    user: dict[str, Any] = Depends(guard("GET", "/api/governance/audit-log")),
) -> AuditLogResponse:
    """
    Query the append-only audit log with optional time range, agent, and verdict filters.

    Requires governance_officer or platform_engineer role.
    Results are paginated.  Maximum page_size=500.
    """
    # Built with SQLAlchemy Core, so no SQL is assembled from strings (bandit B608).
    # Every value is a named, untyped bindparam() given at execution, as with
    # text(): a literal would take a type from the Python value, and asyncpg would
    # render a cast PostgreSQL may refuse (see _AUDIT_LOG).
    filters = []
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if from_ts:
        filters.append(_AUDIT_LOG.c.created_at >= bindparam("from_ts"))
        params["from_ts"] = from_ts
    if to_ts:
        filters.append(_AUDIT_LOG.c.created_at <= bindparam("to_ts"))
        params["to_ts"] = to_ts
    if agent_id:
        filters.append(_AUDIT_LOG.c.agent_id == bindparam("agent_id"))
        params["agent_id"] = agent_id
    if verdict:
        filters.append(_AUDIT_LOG.c.guardian_verdict == bindparam("verdict"))
        params["verdict"] = str(verdict.value if hasattr(verdict, "value") else verdict)

    async with CloudSqlFactory.get_session_factory()() as session:
        # Total count
        count_row = await session.execute(
            select(func.count().label("cnt")).select_from(_AUDIT_LOG).where(*filters),
            params,
        )
        total = count_row.scalar() or 0

        # Paginated data
        rows = await session.execute(
            select(_AUDIT_LOG)
            .where(*filters)
            .order_by(_AUDIT_LOG.c.created_at.desc())
            .limit(bindparam("limit"))
            .offset(bindparam("offset")),
            params,
        )
        import json as _json
        entries = []
        for r in rows.mappings():
            row_dict = dict(r)
            raw_rules = row_dict.get("policy_rules_cited", "[]")
            if isinstance(raw_rules, str):
                try:
                    row_dict["policy_rules_cited"] = _json.loads(raw_rules)
                except Exception:
                    row_dict["policy_rules_cited"] = []
            entries.append(AuditEntry.model_validate(row_dict))

    log.info(
        "api.governance.audit_log_query",
        total=total,
        page=page,
        page_size=page_size,
        uid=user.get("uid"),
    )

    return AuditLogResponse(
        entries=entries,
        total=int(total),
        page=page,
        page_size=page_size,
    )

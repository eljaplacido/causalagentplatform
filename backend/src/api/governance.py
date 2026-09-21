"""
Governance API routes.

Endpoints:
  POST /api/governance/check                — invoke Guardian check
  POST /api/governance/approve/{object_id}  — approve a draft (GOVERNANCE_OFFICER only)
  POST /api/governance/reject/{object_id}   — reject with reason
  GET  /api/governance/review-queue         — list pending drafts by risk tier
  GET  /api/governance/audit-log            — paginated audit log

Role requirements:
  - /check          → any authenticated user
  - /approve        → governance_officer or platform_engineer
  - /reject         → governance_officer or platform_engineer
  - /review-queue   → governance_officer or platform_engineer
  - /audit-log      → governance_officer or platform_engineer
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.agents.governance import GovernanceAgent
from src.db.factory import CloudSqlFactory
from src.exceptions import GuardianBlockedError, ObjectNotFoundError
from src.middleware.auth import get_current_user, require_role
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

_registry = SemanticLayerRegistry()
_governance = GovernanceAgent(agent_id="governance-agent-api")

GOVERNANCE_ROLES = ("governance_officer", "platform_engineer")


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
        "approve actions in a situation nobody has classified.",
    )
    cycle_count: int = Field(
        0, ge=0, description="Reasoning cycles so far; PR-005 escalates at 5."
    )
    hallucination_risk_score: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="The caller's own hallucination-risk estimate for this output. "
        "PR-002 rejects above its threshold.",
    )
    session_id: str | None = Field(None, description="Current reasoning session ID")


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
    user: dict[str, Any] = Depends(get_current_user),
) -> GuardianCheckResponse:
    """
    Submit an action payload to the Guardian for policy evaluation.

    Returns APPROVED / REJECTED / REQUIRES_ESCALATION.
    Every call writes an immutable audit_log entry regardless of verdict.
    """
    uid = user.get("uid", "unknown")
    session_id = body.session_id or f"api-check-{uid}"

    # The state is what the policy rules read. Building it bare was the whole of
    # the defect this endpoint carried: `cynefin_domain` then defaulted to DISORDER,
    # PR-006 fired before any other rule, and every call returned REJECTED --
    # including calls whose payload was meant to trip a different rule entirely.
    # Unclassified still means DISORDER and still gets rejected; the difference is
    # that a caller who has classified the situation can now say so.
    state = EpistemicState(
        session_id=session_id,
        agent_id=f"api-user-{uid}",
        cycle_count=body.cycle_count,
        hallucination_risk_score=body.hallucination_risk_score,
        **({"cynefin_domain": body.cynefin_domain} if body.cynefin_domain else {}),
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

    return GuardianCheckResponse(
        verdict=str(result.verdict),
        reason=result.reason,
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
    user: dict[str, Any] = Depends(require_role(*GOVERNANCE_ROLES)),
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
        )
    except GuardianBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    user: dict[str, Any] = Depends(require_role(*GOVERNANCE_ROLES)),
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
        )
    except GuardianBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    user: dict[str, Any] = Depends(require_role(*GOVERNANCE_ROLES)),
) -> list[ReviewQueueItem]:
    """
    Return all DRAFT / UNDER_REVIEW semantic objects pending review.

    Ordered chronologically (oldest first = highest priority).
    Requires governance_officer or platform_engineer role.
    """
    log.info("api.governance.review_queue", domain=domain, limit=limit)
    items = await _registry.list_pending_review_queue(limit=limit)

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
    user: dict[str, Any] = Depends(require_role(*GOVERNANCE_ROLES)),
) -> AuditLogResponse:
    """
    Query the append-only audit log with optional time range, agent, and verdict filters.

    Requires governance_officer or platform_engineer role.
    Results are paginated.  Maximum page_size=500.
    """
    conditions: list[str] = []
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }

    if from_ts:
        conditions.append("created_at >= :from_ts")
        params["from_ts"] = from_ts
    if to_ts:
        conditions.append("created_at <= :to_ts")
        params["to_ts"] = to_ts
    if agent_id:
        conditions.append("agent_id = :agent_id")
        params["agent_id"] = agent_id
    if verdict:
        conditions.append("guardian_verdict = :verdict")
        params["verdict"] = str(verdict.value if hasattr(verdict, "value") else verdict)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    async with CloudSqlFactory.get_session_factory()() as session:
        # Total count
        count_row = await session.execute(
            text(f"SELECT COUNT(*) AS cnt FROM audit_log {where}"),
            params,
        )
        total = count_row.scalar() or 0

        # Paginated data
        rows = await session.execute(
            text(
                f"SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
                f"policy_rules_cited, session_id, created_at, subject_id, notes "
                f"FROM audit_log {where} "
                f"ORDER BY created_at DESC "
                f"LIMIT :limit OFFSET :offset"
            ),
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

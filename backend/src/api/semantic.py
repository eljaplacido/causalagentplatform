"""
Semantic Layer API routes.

Endpoints:
  GET  /api/semantic/entities          — list entities (domain, status filters)
  GET  /api/semantic/entities/{id}     — get entity by ID
  GET  /api/semantic/fragments         — list graph fragments
  GET  /api/semantic/fragments/{id}    — get fragment by ID
  GET  /api/semantic/edges/{id}        — get causal edge by ID
  POST /api/semantic/drafts            — submit a new draft (auth required, goes through governance)
  GET  /api/semantic/edges/{id}/audit-trail — full audit trail for an edge

All write operations require a Guardian APPROVED verdict.  The draft submission
endpoint runs a governance check inline before persisting.

Access (src/middleware/rbac.py): every route needs a platform role; a viewer
reads APPROVED objects only; and every read and draft is bounded by the user's
domain scope. An object outside it is not found (404), so its existence is not
disclosed. This is enforced here and in the registry's queries, not by RLS.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.agents.governance import GovernanceAgent
from src.exceptions import ObjectNotFoundError
from src.middleware.rbac import approved_only, domain_scope, guard, in_scope
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AuditEntry,
    CausalEdge,
    CausalEntity,
    EpistemicState,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry

log = structlog.get_logger(__name__)
router = APIRouter()

# Singletons (lazily created per request in production — here shared for simplicity)
_registry = SemanticLayerRegistry()
_governance = GovernanceAgent(agent_id="governance-agent-api")


# ── Request / Response schemas ────────────────────────────────────────────────


class DraftSubmissionRequest(BaseModel):
    """Request body for POST /api/semantic/drafts."""

    object_type: str = Field(
        ...,
        description="One of: CausalEdge, GraphFragment, CausalEntity, Assumption",
    )
    payload: dict[str, Any] = Field(
        ...,
        description="The draft object payload matching the target Pydantic model.",
    )


class DraftSubmissionResponse(BaseModel):
    object_id: str
    object_type: str
    guardian_verdict: str
    audit_entry_id: str
    message: str


# ── Entities ──────────────────────────────────────────────────────────────────


@router.get(
    "/entities",
    response_model=list[CausalEntity],
    summary="List causal entities",
)
async def list_entities(
    domain: Annotated[str | None, Query(description="Filter by domain")] = None,
    status: Annotated[
        ApprovalStatus | None,
        Query(description="Filter by approval status"),
    ] = None,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/entities")),
) -> list[CausalEntity]:
    """
    List CausalEntity objects, optionally filtered by domain and/or approval status.

    Only entities inside the caller's domain scope are listed, and a viewer sees
    APPROVED ones only.
    """
    log.info("api.semantic.list_entities", domain=domain, status=status)
    if approved_only(user):
        if status not in (None, ApprovalStatus.APPROVED):
            return []
        status = ApprovalStatus.APPROVED
    entities = await _registry.list_entities(
        domain=domain, status=status, domains=domain_scope(user)
    )
    return list(entities)


@router.get(
    "/entities/{entity_id}",
    response_model=CausalEntity,
    summary="Get causal entity by ID",
)
async def get_entity(
    entity_id: UUID,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/entities/{entity_id}")),
) -> CausalEntity:
    """Fetch a single CausalEntity by its UUID."""
    try:
        entity = await _registry.get_entity(entity_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if approved_only(user) and entity.approval_status != ApprovalStatus.APPROVED:
        raise HTTPException(status_code=404, detail=f"CausalEntity {entity_id} not found")
    return entity


# ── Graph Fragments ───────────────────────────────────────────────────────────


@router.get(
    "/fragments",
    response_model=list[GraphFragment],
    summary="List graph fragments",
)
async def list_fragments(
    domain: Annotated[str | None, Query(description="Filter by domain")] = None,
    status: Annotated[
        FragmentApprovalStatus | None,
        Query(description="Filter by approval status"),
    ] = None,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/fragments")),
) -> list[GraphFragment]:
    """
    List GraphFragment objects.

    Note: SimulationAgent only operates on APPROVED fragments.  The status
    filter allows the UI to show DRAFT fragments in the review queue.
    """
    log.info("api.semantic.list_fragments", domain=domain, status=status)
    if approved_only(user):
        if status not in (None, FragmentApprovalStatus.APPROVED):
            return []
        status = FragmentApprovalStatus.APPROVED
    fragments = await _registry.list_fragments(
        domain=domain, status=status, domains=domain_scope(user)
    )
    return list(fragments)


@router.get(
    "/fragments/{fragment_id}",
    response_model=GraphFragment,
    summary="Get graph fragment by ID",
)
async def get_fragment(
    fragment_id: UUID,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/fragments/{fragment_id}")),
) -> GraphFragment:
    """Fetch a single GraphFragment by its UUID."""
    try:
        fragment = await _registry.get_fragment(fragment_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if approved_only(user) and fragment.approval_status != FragmentApprovalStatus.APPROVED:
        raise HTTPException(status_code=404, detail=f"GraphFragment {fragment_id} not found")
    return fragment


@router.get(
    "/edges/{edge_id}",
    response_model=CausalEdge,
    summary="Get causal edge by ID",
)
async def get_edge(
    edge_id: UUID,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/edges/{edge_id}")),
) -> CausalEdge:
    """Fetch a single CausalEdge by its UUID.

    Used by the DAG Workbench to render full edge metadata (source, target,
    confidence, evidence refs, Guardian verdict ref) starting from the edge IDs
    embedded in a GraphFragment.
    """
    return await _visible_edge(edge_id, user)


async def _visible_edge(edge_id: UUID, user: dict[str, Any]) -> CausalEdge:
    """The edge, if this user may see it; otherwise 404 (not 403: see the module doc)."""
    try:
        edge = await _registry.get_edge(edge_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if approved_only(user) and edge.approval_status != ApprovalStatus.APPROVED:
        raise HTTPException(status_code=404, detail=f"CausalEdge {edge_id} not found")
    return edge


# ── Draft submission ──────────────────────────────────────────────────────────


@router.post(
    "/drafts",
    response_model=DraftSubmissionResponse,
    status_code=201,
    summary="Submit a new draft semantic object",
)
async def submit_draft(
    body: DraftSubmissionRequest,
    request: Request,
    user: dict[str, Any] = Depends(guard("POST", "/api/semantic/drafts")),
) -> DraftSubmissionResponse:
    """
    Submit a new draft causal semantic object.

    Flow:
      1. Parse and validate the payload into the appropriate Pydantic model
      2. Run a GovernanceAgent check on the draft payload
      3. If APPROVED: persist the draft via SemanticLayerRegistry
      4. If REJECTED or REQUIRES_ESCALATION: return 403 with reason

    Authentication: Bearer token required.
    Note: Drafts persisted here still have approval_status=DRAFT.  A separate
    /governance/approve/{id} call by a Governance Officer is required to
    promote to APPROVED.
    """
    uid = user.get("uid", "unknown")
    session_id = f"api-draft-{uid}"

    log.info(
        "api.semantic.submit_draft",
        object_type=body.object_type,
        uid=uid,
    )

    # Build the draft object
    draft_obj: CausalEdge | GraphFragment | CausalEntity | Assumption
    user_ref = UserRef(id=uid, email=user.get("email"), name=user.get("name"))

    type_map = {
        "CausalEdge": CausalEdge,
        "GraphFragment": GraphFragment,
        "CausalEntity": CausalEntity,
        "Assumption": Assumption,
    }
    model_class = type_map.get(body.object_type)
    if model_class is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown object_type '{body.object_type}'. "
                f"Must be one of: {list(type_map.keys())}"
            ),
        )

    try:
        draft_obj = model_class.model_validate(body.payload)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {body.object_type} payload: {exc}",
        ) from exc

    # Attribution comes from the token, not from the body. `created_by` is a
    # required field on CausalEntity and GraphFragment, so the submitter has to
    # put *something* there -- and left to itself that something can be anyone.
    # CausalEdge and Assumption have no such field and are left alone.
    if "created_by" in type(draft_obj).model_fields:
        draft_obj = draft_obj.model_copy(update={"created_by": user_ref})

    # A user drafts only inside their domain scope. Checked before the Guardian
    # runs, so an out-of-scope attempt writes no verdict to the audit log.
    if not in_scope(domain_scope(user), draft_obj.domain):
        raise HTTPException(
            status_code=403,
            detail=f"domain '{draft_obj.domain}' is outside this user's domain scope",
        )

    # Build an EpistemicState for the governance check
    state = EpistemicState(
        session_id=session_id,
        agent_id="api-draft-submission",
        proposed_edges=(
            []
        ),
    )

    # Run governance check
    action_payload: dict[str, Any] = {
        "action_type": "DRAFT_SUBMISSION",
        "object_type": body.object_type,
        "object_id": str(draft_obj.id),
        "submitted_by": uid,
        "payload_summary": str(draft_obj.model_dump())[:500],
    }

    governance_result = await _governance.check(action_payload, state)

    if governance_result.verdict not in (
        GuardianVerdict.APPROVED,
        "APPROVED",
    ):
        log.warning(
            "api.semantic.draft_blocked",
            verdict=governance_result.verdict,
            reason=governance_result.reason,
            uid=uid,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "guardian_blocked",
                "verdict": str(governance_result.verdict),
                "reason": governance_result.reason,
                "audit_entry_id": str(governance_result.audit_entry_id),
            },
        )

    # Persist the draft
    object_id = await _registry.propose_draft(draft_obj, governance_result.audit_entry_id)

    log.info(
        "api.semantic.draft_submitted",
        object_type=body.object_type,
        object_id=str(object_id),
        audit_entry_id=str(governance_result.audit_entry_id),
        uid=uid,
    )

    return DraftSubmissionResponse(
        object_id=str(object_id),
        object_type=body.object_type,
        guardian_verdict=str(governance_result.verdict),
        audit_entry_id=str(governance_result.audit_entry_id),
        message=f"{body.object_type} draft submitted. Awaiting Governance Officer review.",
    )


# ── Edge audit trail ──────────────────────────────────────────────────────────


@router.get(
    "/edges/{edge_id}/audit-trail",
    response_model=list[AuditEntry],
    summary="Get audit trail for a causal edge",
)
async def get_edge_audit_trail(
    edge_id: UUID,
    user: dict[str, Any] = Depends(guard("GET", "/api/semantic/edges/{edge_id}/audit-trail")),
) -> list[AuditEntry]:
    """
    Return the full audit trail for a CausalEdge.

    The audit trail consists of all audit_log entries associated with the
    edge's guardian_verdict_ref.  This satisfies EU AI Act Art. 13 lineage
    requirements.
    """
    if approved_only(user):
        await _visible_edge(edge_id, user)
    try:
        trail = await _registry.get_edge_audit_trail(edge_id, domains=domain_scope(user))
        return list(trail)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

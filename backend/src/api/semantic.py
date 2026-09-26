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
  GET  /api/semantic/objects/{type}/{id}/versions — every recorded version of an object
  GET  /api/semantic/objects/{type}/{id}/lineage  — source data → drafter → verdicts → approver
  POST /api/semantic/edges/{id}/sensitivity — run the kernel on a draft edge's question
                                             and record its sensitivity analysis (septa-70)

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

import pandas as pd
import structlog
from causal_core import analyze
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.agents.edge_sensitivity import record_edge_sensitivity, sensitivity_from_outcome
from src.agents.governance import GovernanceAgent
from src.api.provenance import get_provenance_graph
from src.exceptions import GuardianBlockedError, InvalidTransitionError, ObjectNotFoundError
from src.middleware.rbac import approved_only, domain_scope, guard, in_scope
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AuditEntry,
    CausalEdge,
    CausalEntity,
    CynefinDomain,
    EpistemicState,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    LineageChain,
    ReviewStatus,
    SemanticObjectVersion,
    SensitivityResult,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry

log = structlog.get_logger(__name__)
router = APIRouter()

#: The agent a draft submitted here comes through, as its lineage names it.
_DRAFT_AGENT = "api-draft-submission"

# Singletons (lazily created per request in production — here shared for simplicity)
_registry = SemanticLayerRegistry()
_governance = GovernanceAgent(agent_id="governance-agent-api")
_sessions = SessionStateStore()


# ── Request / Response schemas ────────────────────────────────────────────────


class DraftSubmissionRequest(BaseModel):
    """Request body for POST /api/semantic/drafts."""

    object_type: str = Field(
        ...,
        description="One of: CausalEdge, GraphFragment, CausalEntity, Assumption",
    )
    payload: dict[str, Any] = Field(
        ...,
        description="The draft object payload matching the target Pydantic model. "
        "Its `sourceData` ({kind, ref, digest}) names the data the object was drafted "
        "from; approval requires one.",
    )
    cynefin_domain: CynefinDomain | None = Field(
        None,
        description="The situation's Cynefin domain, as classified by the caller, as for "
        "POST /api/governance/check. Omitted, it is DISORDER, which Guardian rule PR-006 "
        "refuses; CHAOTIC is escalated (PR-003). Ignored when `session_id` names a session. "
        "Recorded, with who declared it, in the verdict's audit row.",
    )
    session_id: str | None = Field(
        None,
        description="A reasoning session (POST /api/v1/session) whose classified domain and "
        "cycle count the Guardian reads instead of a declaration. A session that does not "
        "exist is refused (404).",
    )


class DraftSubmissionResponse(BaseModel):
    object_id: str
    object_type: str
    guardian_verdict: str
    audit_entry_id: str
    cynefin_domain: str = Field(description="The domain the Guardian judged the draft in.")
    domain_source: str = Field(
        description="Where that domain came from: `session <id>`, `declared by <uid>`, or "
        "`not declared` (DISORDER)."
    )
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
      2. Classify the situation: the named session's domain (`session_id`), else the
         caller's declaration (`cynefin_domain`), else DISORDER
      3. Run a GovernanceAgent check on the draft payload in that domain (PR-006
         refuses DISORDER, PR-003 escalates CHAOTIC); the domain and its source are
         in the checked payload and the audit row's notes
      4. If APPROVED: persist the draft via SemanticLayerRegistry
      5. If REJECTED or REQUIRES_ESCALATION: return 403 with reason and rules cited

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
    # CausalEdge's (012_edge_created_by.sql) is optional and stamped the same way;
    # Assumption has no such field (elicited_by is the person it was elicited from).
    if "created_by" in type(draft_obj).model_fields:
        draft_obj = draft_obj.model_copy(update={"created_by": user_ref})

    # A user drafts only inside their domain scope. Checked before the Guardian
    # runs, so an out-of-scope attempt writes no verdict to the audit log.
    if not in_scope(domain_scope(user), draft_obj.domain):
        raise HTTPException(
            status_code=403,
            detail=f"domain '{draft_obj.domain}' is outside this user's domain scope",
        )

    # The situation's domain, classified the way POST /api/governance/check does: a
    # named session's own classification, else the caller's declaration, else
    # DISORDER -- which PR-006 refuses. It used to be nothing at all, so every draft
    # submitted here was DISORDER and refused. The domain and its source go into the
    # checked payload (the verdict's payload hash binds them) and the audit row's
    # notes, so a reader of the trail sees what the draft was judged in, and on whose
    # word.
    stored = None
    if body.session_id:
        stored = await _sessions.load(body.session_id)
        if stored is None:
            raise HTTPException(
                status_code=404, detail=f"session '{body.session_id}' not found"
            )
    if stored is not None:
        domain = CynefinDomain(stored.cynefin_domain)
        source = f"session {body.session_id}"
    elif body.cynefin_domain is not None:
        domain = CynefinDomain(body.cynefin_domain)
        source = f"declared by {uid}"
    else:
        domain = CynefinDomain.DISORDER
        source = "not declared"

    state = EpistemicState(
        session_id=body.session_id or session_id,
        agent_id=_DRAFT_AGENT,
        cycle_count=stored.cycle_count if stored is not None else 0,
        cynefin_domain=domain,
    )

    # Run governance check
    action_payload: dict[str, Any] = {
        "action_type": "DRAFT_SUBMISSION",
        "object_type": body.object_type,
        "object_id": str(draft_obj.id),
        "submitted_by": uid,
        "cynefin_domain": domain.value,
        "cynefin_domain_source": source,
        "payload_summary": str(draft_obj.model_dump())[:500],
    }
    notes = f"cynefin_domain={domain.value} ({source})"

    governance_result = await _governance.check(action_payload, state, notes=notes)

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
                "policy_rules_cited": list(governance_result.policy_rules_cited),
                "cynefin_domain": domain.value,
                "domain_source": source,
                "audit_entry_id": str(governance_result.audit_entry_id),
            },
        )

    # Persist the draft
    object_id = await _registry.propose_draft(
        draft_obj,
        governance_result.audit_entry_id,
        submitted_by=user_ref,
        agent_id=_DRAFT_AGENT,
    )

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
        cynefin_domain=domain.value,
        domain_source=source,
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
    Return the audit_log entry the edge's current guardian_verdict_ref names.

    That is the verdict behind the edge's current version only. Every verdict
    issued for it, with the rest of its lineage, is
    GET /api/semantic/objects/CausalEdge/{edge_id}/lineage. Neither is evidence
    of meeting EU AI Act Art. 13; that claim was withdrawn (septa-15).
    """
    if approved_only(user):
        await _visible_edge(edge_id, user)
    try:
        trail = await _registry.get_edge_audit_trail(edge_id, domains=domain_scope(user))
        return list(trail)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Versions and lineage ─────────────────────────────────────────────────────

#: The object types with a version history (014_semantic_object_versions.sql).
_VERSIONED_TYPES = ("CausalEntity", "CausalEdge", "GraphFragment", "Assumption")


async def _visible_object(object_type: str, object_id: UUID, user: dict[str, Any]) -> None:
    """404 unless this user may read the object; 422 for a type with no history.

    The same rule as the object reads: inside the user's domain scope, and for a
    viewer only when the object is approved (an Assumption: ACCEPTED). Its
    history includes the drafts before approval, which is the point of it.
    """
    if object_type not in _VERSIONED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown object_type '{object_type}'. Must be one of: "
            f"{list(_VERSIONED_TYPES)}",
        )
    try:
        obj = await _registry.get_object(object_type, object_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if approved_only(user):
        status = (
            obj.review_status == ReviewStatus.ACCEPTED
            if isinstance(obj, Assumption)
            else obj.approval_status == ApprovalStatus.APPROVED
        )
        if not status:
            raise HTTPException(status_code=404, detail=f"{object_type} {object_id} not found")


@router.get(
    "/objects/{object_type}/{object_id}/versions",
    response_model=list[SemanticObjectVersion],
    summary="Every recorded version of a semantic object",
)
async def get_object_versions(
    object_type: str,
    object_id: UUID,
    user: dict[str, Any] = Depends(
        guard("GET", "/api/semantic/objects/{object_type}/{object_id}/versions")
    ),
) -> list[SemanticObjectVersion]:
    """The object's history, oldest first: each version its complete state at a revision.

    A state change (approval, rejection) writes a new version and never rewrites
    an old one (ADR-001). The object's own endpoint returns the current version.
    """
    await _visible_object(object_type, object_id, user)
    try:
        return await _registry.get_versions(object_type, object_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/objects/{object_type}/{object_id}/lineage",
    response_model=LineageChain,
    summary="Lineage of a semantic object: source data, drafter, verdicts, approver",
)
async def get_object_lineage(
    object_type: str,
    object_id: UUID,
    user: dict[str, Any] = Depends(
        guard("GET", "/api/semantic/objects/{object_type}/{object_id}/lineage")
    ),
) -> LineageChain:
    """The chain from the data the object was drafted from to whoever approved it.

    Assembled from the version history and audit_log, with the provenance
    records the provenance store holds for the sessions its verdicts were issued
    in. A link never recorded is listed in ``gaps``; ``complete`` is true only
    when there are none.
    """
    await _visible_object(object_type, object_id, user)
    try:
        chain = await _registry.get_lineage(object_type, object_id, domains=domain_scope(user))
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # The durable provenance store (011_provenance.sql), as its API shows it: a
    # record an erasure tombstone hides is not listed here either.
    records = []
    for session_id in chain.provenance_sessions:
        graph, _erasures = await get_provenance_graph(session_id)
        records.extend(record.id for record in graph.records)
    return chain.model_copy(update={"provenance_records": records})


# ── An edge's sensitivity analysis (septa-70) ─────────────────────────────────

Cell = float | int | str | bool | None


class EdgeSensitivityRequest(BaseModel):
    """What the kernel analyses: the edge's own question, on the caller's data."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, list[Cell]] = Field(
        ...,
        description="Columns as lists. The edge's source and target ids name two of them "
        "(node ids are used in full, as everywhere the kernel reads a fragment).",
    )
    adjust_for: list[str] = Field(
        default_factory=list,
        description="Measured common causes of the edge's source and target (columns). The "
        "analysis graph is the edge plus an edge from each of these into its source and its "
        "target; the E-value then says how strong confounding *beyond* them would have to be.",
    )
    cynefin_domain: CynefinDomain | None = Field(
        None,
        description="The situation's domain, as for POST /api/governance/check: the "
        "caller's declaration when no session is named; omitted, DISORDER, which PR-006 "
        "refuses. Ignored when `session_id` names a session.",
    )
    session_id: str | None = Field(
        None,
        description="A reasoning session whose classified domain and cycle count the "
        "Guardian reads instead.",
    )


class EdgeSensitivityResponse(BaseModel):
    edge_id: str
    outcome_kind: str = Field(description="result | abstention | apparatus_failure")
    detail: str | None = Field(
        None, description="Why there is no sensitivity result, when there is none."
    )
    sensitivity: SensitivityResult | None
    assumption_id: str | None = Field(
        None, description="The IGNORABILITY assumption the result was recorded on."
    )
    guardian_verdict: str | None
    audit_entry_id: str | None


@router.post(
    "/edges/{edge_id}/sensitivity",
    response_model=EdgeSensitivityResponse,
    summary="Run the kernel on a draft edge's question and record its sensitivity analysis",
)
async def analyse_edge_sensitivity(
    edge_id: UUID,
    body: EdgeSensitivityRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/semantic/edges/{edge_id}/sensitivity")),
) -> EdgeSensitivityResponse:
    """Estimate the effect of the edge's source on its target and record how robust it is.

    The platform runs ``causal_core.analyze`` for source -> target on the supplied data,
    over the edge and the declared common causes. When the kernel returns a sensitivity
    analysis (the E-value; a Rosenbaum gamma* where the kernel reports one), the Guardian
    is asked for a verdict for RECORD_SENSITIVITY on the edge and, if it approves, the
    registry records the result as a new IGNORABILITY assumption the edge rests on --
    what Guardian PR-007 reads before an edge can be approved.

    A kernel refusal is a result: 200 with its reason, nothing written. 403 when the
    Guardian does not approve the recording, 404 for an edge outside the caller's
    scope, 409 for an edge that is APPROVED or DEPRECATED (never changed in place), 422
    for data that lack the edge's columns.
    """
    uid = str(user.get("uid", "unknown"))
    scope = domain_scope(user)
    edge = await _visible_edge(edge_id, user)
    if str(edge.approval_status) not in ("DRAFT", "UNDER_REVIEW"):
        raise HTTPException(
            status_code=409,
            detail=f"CausalEdge {edge_id} is {edge.approval_status}: a sensitivity result is "
            "recorded on a draft under review, never on an approved or deprecated edge",
        )

    source, target = str(edge.source.id), str(edge.target.id)
    if edge.direction == "BACKWARD":
        source, target = target, source
    needed = [source, target, *body.adjust_for]
    missing = [c for c in needed if c not in body.data]
    if missing:
        raise HTTPException(status_code=422, detail=f"data has no column for {missing}")
    try:
        frame = pd.DataFrame(body.data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"data: {exc}") from exc

    graph = [(source, target)]
    for c in body.adjust_for:
        graph += [(c, source), (c, target)]
    outcome = analyze(frame, graph, treatment=source, outcome=target).model_dump(mode="json")
    result = sensitivity_from_outcome(outcome, computed_by=uid)
    log.info(
        "api.semantic.edge_sensitivity",
        edge_id=str(edge_id),
        kind=outcome.get("kind"),
        has_result=result is not None,
        uid=uid,
    )
    if result is None:
        detail = outcome.get("detail") or "the kernel ran no sensitivity analysis"
        return EdgeSensitivityResponse(
            edge_id=str(edge_id),
            outcome_kind=str(outcome.get("kind")),
            detail=str(detail),
            sensitivity=None,
            guardian_verdict=None,
            audit_entry_id=None,
        )

    stored = await _sessions.load(body.session_id) if body.session_id else None
    domain = stored.cynefin_domain if stored is not None else body.cynefin_domain
    state = EpistemicState(
        session_id=body.session_id or f"api-sensitivity-{uid}",
        agent_id=f"api-user-{uid}",
        cycle_count=stored.cycle_count if stored is not None else 0,
        **({"cynefin_domain": domain} if domain else {}),
    )
    try:
        recorded = await record_edge_sensitivity(
            edge_id,
            result,
            governance=_governance,
            state=state,
            actor=UserRef(id=uid, email=user.get("email"), name=user.get("name")),
            evidence={"rows": len(frame), "adjust_for": list(body.adjust_for)},
            registry=_registry,
            domains=scope,
        )
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GuardianBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if recorded.assumption_id is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "guardian_blocked",
                "verdict": str(recorded.verdict),
                "reason": recorded.reason,
                "policy_rules_cited": recorded.policy_rules_cited,
                "audit_entry_id": str(recorded.audit_entry_id),
            },
        )
    return EdgeSensitivityResponse(
        edge_id=str(edge_id),
        outcome_kind=str(outcome.get("kind")),
        sensitivity=result,
        assumption_id=str(recorded.assumption_id),
        guardian_verdict=str(recorded.verdict),
        audit_entry_id=str(recorded.audit_entry_id),
    )

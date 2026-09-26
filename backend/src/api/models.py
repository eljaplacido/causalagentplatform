"""
The assumption registry over HTTP: causal models as versions, and what a revision breaks.

A decision used to carry its causal graph in the request body, so nothing stored
the assumptions a verdict rested on, versioned them, or could say which verdicts a
changed belief affects. Here a model is created once, revised into new immutable
versions, and reviewed edge by edge; `POST /api/v1/decisions` can name a version
instead of sending a graph, and `GET .../impact` answers the question a revision
raises: for each decision made on an older version, does its estimate still stand
if the new graph is true?

The rules -- a revision cannot approve an edge, nobody reviews an edge they
introduced, a version is built on the head -- are enforced in
`causal_decision.assumptions` and `src.semantic_layer.model_store`, not here.
Reviewing needs a governance role (GOVERNANCE_ROLES); proposing and revising need
a drafting role (DRAFT_ROLES: analyst, graph_author, platform_engineer, agent),
because a proposal is a draft: an unreviewed edge makes every result on the model
`provisional`, and `require_approved_graph` refuses it. The rows are in
src/middleware/rbac.py.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from causal_core.contract import CausalGraph  # noqa: TC002 -- pydantic resolves it at runtime
from causal_decision.assumptions import (
    Assumption,
    EdgeReview,
    HistoryReport,
    Impact,
    ModelDiff,
    ModelError,
    ModelRef,
    ModelVersion,
    SelfReviewError,
    create,
    diff,
    impact,
    review,
    revise,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.middleware.rbac import guard
from src.semantic_layer.model_store import ModelConflictError, ModelStore

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/models", tags=["Assumption Registry"])

_store = ModelStore()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _author(user: dict[str, Any]) -> str:
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="the token names no user")
    return str(uid)


class CreateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Lowercase letters, digits, '.', '_' or '-'.")
    graph: str | CausalGraph = Field(..., description="Text syntax ('Z -> X -> Y') or a graph.")
    assumptions: list[Assumption] = Field(default_factory=list)
    why: str = Field(..., min_length=5)


class ReviseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_digest: str = Field(..., description="The version this revision was built on.")
    graph: str | CausalGraph | None = None
    assumptions: list[Assumption] | None = None
    why: str = Field(..., min_length=5)


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_digest: str
    reviews: list[EdgeReview] = Field(..., min_length=1)


class ModelSummary(BaseModel):
    name: str
    version: int
    digest: str
    author_id: str
    created_at: str
    unreviewed_edges: list[str]


class ModelList(BaseModel):
    models: list[ModelSummary]


class ModelHistory(BaseModel):
    head: ModelVersion
    versions: list[ModelRef]


class DecisionImpact(BaseModel):
    entry_digest: str
    model_version: int
    verdict: str
    action: str
    impact: Impact


class ImpactReport(BaseModel):
    model: str
    to: ModelRef
    decisions: list[DecisionImpact]
    by_status: dict[str, int]
    note: str = Field(
        default=(
            "Each decision made on an older version, judged as if the target version were "
            "true. `holds` means the estimate the verdict used is still valid; anything else "
            "means re-analyse before relying on it. Refusals are included: a revision can make "
            "a question answerable that was not."
        )
    )


async def _version_or_404(name: str, version: int | None = None) -> ModelVersion:
    got = await (_store.head(name) if version is None else _store.get(name, version))
    if got is None:
        which = "" if version is None else f" v{version}"
        raise HTTPException(status_code=404, detail=f"no model {name}{which}")
    return got


async def _append(version: ModelVersion) -> ModelVersion:
    try:
        return await _store.append(version)
    except ModelConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("", response_model=ModelVersion, status_code=201, summary="Register a causal model")
async def create_model(
    body: CreateModel, user: dict[str, Any] = Depends(guard("POST", "/api/v1/models"))
) -> ModelVersion:
    try:
        v1 = create(
            body.name,
            body.graph,
            author_id=_author(user),
            created_at=_now(),
            why=body.why,
            assumptions=body.assumptions,
        )
    except (ModelError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = await _append(v1)
    log.info("api.models.created", name=stored.name, digest=stored.digest, uid=user.get("uid"))
    return stored


@router.get("", response_model=ModelList, summary="List registered models (their heads)")
async def list_models(user: dict[str, Any] = Depends(guard("GET", "/api/v1/models"))) -> ModelList:
    return ModelList(
        models=[
            ModelSummary(
                name=m.name,
                version=m.version,
                digest=m.digest,
                author_id=m.author_id,
                created_at=m.created_at,
                unreviewed_edges=m.unreviewed_edges,
            )
            for m in await _store.heads()
        ]
    )


@router.get("/{name}", response_model=ModelHistory, summary="A model's head and history")
async def get_model(
    name: str, user: dict[str, Any] = Depends(guard("GET", "/api/v1/models/{name}"))
) -> ModelHistory:
    history = await _store.history(name)
    if not history:
        raise HTTPException(status_code=404, detail=f"no model {name}")
    return ModelHistory(head=history[-1], versions=[v.ref() for v in history])


@router.get(
    "/{name}/versions/{version}", response_model=ModelVersion, summary="One version of a model"
)
async def get_version(
    name: str,
    version: int,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/models/{name}/versions/{version}")),
) -> ModelVersion:
    return await _version_or_404(name, version)


@router.post(
    "/{name}/versions",
    response_model=ModelVersion,
    status_code=201,
    summary="Revise a model: a new graph and/or new assumptions",
)
async def revise_model(
    name: str,
    body: ReviseModel,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/models/{name}/versions")),
) -> ModelVersion:
    head = await _version_or_404(name)
    if head.digest != body.parent_digest:
        raise HTTPException(
            status_code=409,
            detail=f"this revision was built on {body.parent_digest[:19]}..., but the head is "
            f"v{head.version} ({head.digest[:19]}...): rebuild on the head",
        )
    try:
        new = revise(
            head,
            author_id=_author(user),
            created_at=_now(),
            why=body.why,
            graph=body.graph,
            assumptions=body.assumptions,
        )
    except (ModelError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _append(new)


@router.post(
    "/{name}/reviews",
    response_model=ModelVersion,
    status_code=201,
    summary="Approve or refute edges (governance roles; never your own edges)",
)
async def review_model(
    name: str,
    body: ReviewModel,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/models/{name}/reviews")),
) -> ModelVersion:
    head = await _version_or_404(name)
    if head.digest != body.parent_digest:
        raise HTTPException(
            status_code=409,
            detail=f"this review was made against {body.parent_digest[:19]}..., but the head "
            f"is v{head.version}: review the head",
        )
    try:
        new = review(head, body.reviews, reviewer_id=_author(user), created_at=_now())
    except SelfReviewError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = await _append(new)
    log.info(
        "api.models.reviewed",
        name=name,
        version=stored.version,
        rulings=[f"{r.status}:{r.edge}" for r in body.reviews],
        uid=user.get("uid"),
    )
    return stored


@router.get("/{name}/diff", response_model=ModelDiff, summary="What changed between two versions")
async def diff_versions(
    name: str,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/models/{name}/diff")),
) -> ModelDiff:
    return diff(await _version_or_404(name, from_version), await _version_or_404(name, to_version))


@router.get(
    "/{name}/impact",
    response_model=ImpactReport,
    summary="Which recorded decisions a version invalidates",
)
async def impact_of(
    name: str,
    to_version: Annotated[int | None, Query(alias="to", ge=1)] = None,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/models/{name}/impact")),
) -> ImpactReport:
    target = await _version_or_404(name, to_version)
    cache: dict[int, ModelVersion] = {}
    rows: list[DecisionImpact] = []
    for d in await _store.decisions_on(name, before_version=target.version):
        if d.model_version not in cache:
            cache[d.model_version] = await _version_or_404(name, d.model_version)
        rows.append(
            DecisionImpact(
                entry_digest=d.entry_digest,
                model_version=d.model_version,
                verdict=d.verdict,
                action=d.action,
                impact=impact(
                    cache[d.model_version], target, d.query, unmeasured=d.unmeasured
                ),
            )
        )
    return ImpactReport(
        model=name,
        to=target.ref(),
        decisions=rows,
        by_status=dict(Counter(str(r.impact.status) for r in rows)),
    )


@router.get(
    "/{name}/verify",
    response_model=HistoryReport,
    summary="Recompute a model's history: every digest and parent link",
)
async def verify_model(
    name: str, user: dict[str, Any] = Depends(guard("GET", "/api/v1/models/{name}/verify"))
) -> HistoryReport:
    report = await _store.verify(name)
    if report.length == 0:
        raise HTTPException(status_code=404, detail=f"no model {name}")
    return report

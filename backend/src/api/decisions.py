"""
Decision assurance over HTTP: may this action be taken, and what was decided before?

`POST /api/v1/decisions` analyses and rules in one call. The caller supplies the
data, the graph and what the action is worth; the platform runs `causal_core.analyze`
and then `causal_decision.decide`, and seals the result into the ledger. Nothing
between the estimate and the verdict is the caller's to change -- which is the whole
point of the layer, and would be lost if the endpoint took an outcome as input.

The verdict is the platform's, not the caller's. This endpoint records what the
policy concluded; it does not execute anything, and `act` means "the evidence
supports doing this", never "this was done".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

import causal_core as cc
import pandas as pd
import structlog
from causal_core.contract import AnalysisPolicy, CausalGraph, CausalQuery
from causal_decision import (
    Decision,
    DecisionPolicy,
    DecisionRequest,
    RiskClass,
    Verdict,
    decide,
    explain,
)
from causal_decision.contract import CausalOutcome  # noqa: TC002 -- pydantic resolves it at runtime
from causal_decision.ledger import (
    AnchorReport,
    ChainReport,
    Checkpoint,
    DecisionRecord,
    checkpoint,
    verify_against,
)
from causal_decision.outcome import CalibrationReport, Realization, compare, plan, report
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from src.middleware.rbac import guard
from src.semantic_layer.decision_store import (
    DecisionStore,
    FollowUpConflictError,
    LedgerConflictError,
)
from src.semantic_layer.model_store import ModelStore

if TYPE_CHECKING:
    from causal_decision.assumptions import ModelVersion

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/decisions", tags=["Decision Assurance"])

_store = DecisionStore()
_models = ModelStore()


async def _model_version(selector: ModelSelector) -> ModelVersion:
    got = await (
        _models.head(selector.name)
        if selector.version is None
        else _models.get(selector.name, selector.version)
    )
    if got is None:
        which = "" if selector.version is None else f" v{selector.version}"
        raise HTTPException(status_code=404, detail=f"no model {selector.name}{which}")
    if selector.digest is not None and selector.digest != got.digest:
        raise HTTPException(
            status_code=409,
            detail=f"{selector.name} v{got.version} has digest {got.digest[:19]}..., not the "
            f"{selector.digest[:19]}... the request insisted on",
        )
    return got

Cell = float | int | str | bool | None

#: CausalQuery fields this endpoint sets itself or names at the top level. Every
#: other CausalQuery field is accepted under `query`, derived from the kernel's
#: own model so the HTTP surface cannot fall behind it again: the flat copy this
#: replaced dropped eleven fields, `segment_by` among them, which left the
#: segment-aware verdict unreachable over HTTP while the register said it shipped.
_QUERY_FIELDS_SET_HERE = frozenset({"treatment", "outcome", "estimand", "rung"})

QueryOptions = create_model(  # type: ignore[call-overload]
    "QueryOptions",
    __config__=ConfigDict(extra="forbid", frozen=True),
    __doc__="Everything else the analysis can be asked -- causal_core.contract.CausalQuery.",
    **{
        name: (field.annotation, field)
        for name, field in CausalQuery.model_fields.items()
        if name not in _QUERY_FIELDS_SET_HERE
    },
)


class ModelSelector(BaseModel):
    """Which registered model version to analyse with; the head when `version` is omitted."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int | None = Field(default=None, ge=1)
    digest: str | None = Field(
        default=None,
        description="If given, the version must have this digest: a caller that reviewed a "
        "version can insist on exactly that one.",
    )


class DecideRequest(BaseModel):
    """What the agent proposes, and the data to judge it on."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., min_length=1, description="The action, as it would be executed.")
    graph: str | None = Field(
        default=None,
        min_length=1,
        description="Causal graph inline, e.g. 'Z -> X -> Y; Z -> Y'. Give this or `model`.",
    )
    model: ModelSelector | None = Field(
        default=None,
        description="A registered model version (the assumption registry) instead of an inline "
        "graph. The ledger records which version the verdict rested on, and a later revision "
        "can say whether it still stands.",
    )
    treatment: str
    outcome: str
    direction: Literal["increase", "decrease"] = Field(
        description="Which way the outcome should move, so a benefit is always positive."
    )
    data: dict[str, list[Cell]] = Field(..., description="Columns as lists.")
    estimand: Literal["ate", "att"] = "ate"
    query: QueryOptions = Field(
        default_factory=QueryOptions,
        # Listed from the model, not by hand: the hand list had fallen six fields behind.
        description=f"Further CausalQuery fields: {', '.join(QueryOptions.model_fields)}.",
    )
    risk_class: RiskClass = RiskClass.medium
    reversible: bool = True
    minimum_worthwhile_effect: float = Field(default=0.0, ge=0.0)
    harm_threshold: float = Field(default=0.0, ge=0.0)
    can_randomize: bool = True
    randomization_unit: str = "unit"
    units_per_day: float | None = Field(default=None, gt=0)
    analysis_policy: AnalysisPolicy | None = None
    decision_policy: DecisionPolicy | None = None

    @model_validator(mode="after")
    def _one_graph(self) -> DecideRequest:
        if (self.graph is None) == (self.model is None):
            raise ValueError("give exactly one of `graph` (inline) or `model` (registered)")
        return self


class DecideResponse(BaseModel):
    verdict: Verdict
    explanation: str
    decision: Decision
    outcome: Annotated[CausalOutcome, Field(discriminator="kind")]
    record: DecisionRecord


class RecordList(BaseModel):
    records: list[DecisionRecord]
    total: int


@router.post("", response_model=DecideResponse, summary="Decide whether an action is justified")
async def make_decision(
    body: DecideRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/decisions")),
) -> DecideResponse:
    """Analyse, rule, and record -- in that order, in one call.

    Returns one of `act`, `act_with_approval`, `experiment` or `refuse`, with an
    explanation whose numbers are claims of the analysis, and the ledger entry the
    decision was sealed into.
    """
    options = body.query.model_dump(exclude_unset=True)
    try:
        query = CausalQuery(
            treatment=body.treatment,
            outcome=body.outcome,
            estimand=body.estimand,
            **options,
        )
        request = DecisionRequest(
            action=body.action,
            outcome=body.outcome,
            direction=body.direction,
            risk_class=body.risk_class,
            reversible=body.reversible,
            minimum_worthwhile_effect=body.minimum_worthwhile_effect,
            harm_threshold=body.harm_threshold,
            population=query.population,
            horizon=query.horizon,
            can_randomize=body.can_randomize,
            randomization_unit=body.randomization_unit,
            units_per_day=body.units_per_day,
        )
        frame = pd.DataFrame(body.data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
    policy = body.decision_policy or DecisionPolicy()

    version: ModelVersion | None = None
    graph: str | CausalGraph
    if body.model is not None:
        version = await _model_version(body.model)
        graph = version.graph
    elif body.graph is not None:
        graph = body.graph
    else:
        # DecideRequest._one_graph refuses this before the handler runs. A raise,
        # not an assert: python -O strips asserts.
        raise HTTPException(status_code=422, detail="give exactly one of `graph` or `model`")

    outcome = cc.analyze(
        frame, graph, query=query, policy=body.analysis_policy or AnalysisPolicy()
    )
    decision = decide(outcome, request, policy)

    try:
        record = await _store.record(
            decision,
            outcome,
            recorded_at=datetime.now(UTC).isoformat(),
            model=None if version is None else version.ref(),
        )
    except LedgerConflictError as exc:
        # The decision stands; only its place in the line is contested. Saying so is
        # better than returning a verdict the ledger does not have.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The plan is written now, not reconstructed later: it copies the prediction
    # this verdict used, and a plan rebuilt afterwards would carry whatever a
    # fresh analysis says today -- the target moving after the result is known.
    # Only a result can be re-measured; a refusal predicted nothing.
    if isinstance(outcome, cc.contract.CausalResult):
        await _store.save_plan(
            record.entry_digest,
            plan(
                decision,
                outcome,
                graph=graph,
                observe_from=record.recorded_at,
            ),
        )

    # Which version this verdict rested on, and what it asked -- with adjust_for
    # pinned to the set the analysis actually used and the nodes the data lacked,
    # so a later revision is judged against what was done, not what would be done.
    if version is not None:
        used = query
        if isinstance(outcome, cc.contract.CausalResult) and outcome.certificate.adjustment_set:
            used = query.model_copy(
                update={"adjust_for": outcome.certificate.adjustment_set}
            )
        await _models.record_decision(
            record.entry_digest,
            version.ref(),
            verdict=str(decision.verdict),
            action=body.action,
            query=used,
            unmeasured=tuple(sorted(set(version.graph.nodes) - set(frame.columns))),
            recorded_at=record.recorded_at,
        )

    log.info(
        "api.decisions.decided",
        action=body.action,
        verdict=str(decision.verdict),
        sequence=record.sequence,
        uid=user.get("uid"),
    )

    return DecideResponse(
        verdict=decision.verdict,
        explanation=explain(decision, outcome),
        decision=decision,
        outcome=outcome,
        record=record,
    )


@router.get("", response_model=RecordList, summary="List recorded decisions")
async def list_decisions(
    action: Annotated[str | None, Query(description="Filter to one action")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/decisions")),
) -> RecordList:
    records = (
        await _store.for_subject(action, limit=limit)
        if action
        else await _store.all_records(limit=limit)
    )
    return RecordList(records=records, total=len(records))


@router.get(
    "/verify",
    response_model=ChainReport,
    summary="Verify the decision chain by recomputing it",
)
async def verify_ledger(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/decisions/verify")),
) -> ChainReport:
    """Recompute every digest and link from the stored records.

    A pass means the chain is *internally* consistent. It cannot mean more than
    that: whoever can write these rows can rewrite the whole chain and it will
    verify again. The head digest is in the report because publishing it somewhere
    this database's owner does not control is what turns detection into evidence.
    """
    return await _store.verify()


class AnchorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoints: list[Checkpoint] = Field(
        ..., min_length=1, description="Checkpoints published earlier, from wherever you keep them."
    )


@router.get(
    "/checkpoint",
    response_model=Checkpoint,
    summary="The chain's head, to publish outside this database",
)
async def get_checkpoint(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/decisions/checkpoint")),
) -> Checkpoint:
    """What to publish so that a later rewrite of this chain can be shown, not just suspected.

    Put it somewhere this database's owner cannot edit -- a transparency log, another
    party's records, a signed commit elsewhere. Publishing is the deployment's job; the
    platform does not do it for you, and does not claim to.
    """
    try:
        return checkpoint(await _store.all_records())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/verify-anchored",
    response_model=AnchorReport,
    summary="Verify the chain against checkpoints published earlier",
)
async def verify_anchored(
    body: AnchorRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/decisions/verify-anchored")),
) -> AnchorReport:
    """Recompute the chain, then check it still holds every checkpoint you kept.

    `/verify` alone passes a chain rewritten from an edited entry onward. A checkpoint
    published before the rewrite names a digest the rewritten chain no longer has.
    """
    return verify_against(await _store.all_records(), body.checkpoints)


# ── What happened next ────────────────────────────────────────────────────────


class FollowUpRequest(BaseModel):
    """The later window, to be judged by the plan recorded when the decision was made."""

    data: dict[str, list[Cell]] = Field(
        ...,
        description="Columns as lists, covering the period AFTER the decision. The graph, "
        "the estimand and the prediction come from the stored plan, not from here.",
    )
    analysis_policy: dict[str, Any] | None = None


_ALREADY_MEASURED = (
    "decision {} has already been measured, and the first measurement stands: a second one "
    "would join the calibration average and could outvote the first"
)


class FollowUpResponse(BaseModel):
    realization: Realization
    explanation: str


@router.post(
    "/{entry_digest}/followup",
    response_model=FollowUpResponse,
    summary="Measure what actually happened after a decision",
)
async def record_followup(
    entry_digest: str,
    body: FollowUpRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/decisions/{entry_digest}/followup")),
) -> FollowUpResponse:
    """Re-run the decision's own analysis over a later window and score the prediction.

    **Once per decision.** The first measurement stands and a second is refused
    with 409; otherwise a disappointing window could be outvoted by friendlier ones.

    The caller supplies rows; it does not supply the answer. The graph, the query
    and the effect being scored all come from the plan sealed at decision time, so
    a follow-up cannot be re-aimed at a more flattering estimand after the fact.

    **A refusal here is a result.** If the action was rolled out to everyone there
    is no comparison group left and no later data recovers one; the kernel says so
    with its own typed reason, and that is recorded as an undecidable realization
    rather than dropped. Dropping them would score the platform on the decisions
    whose effects happened to stay measurable.
    """
    followup = await _store.plan_for(entry_digest)
    if followup is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no follow-up plan for {entry_digest}: either no such decision, or it "
                "refused and so predicted nothing to measure"
            ),
        )

    if await _store.has_realization(entry_digest):
        raise HTTPException(status_code=409, detail=_ALREADY_MEASURED.format(entry_digest))

    try:
        analysis = AnalysisPolicy.model_validate(body.analysis_policy or {})
        frame = pd.DataFrame(body.data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc

    later = cc.analyze(frame, followup.graph, query=followup.query, policy=analysis)
    realization = compare(followup, later)
    try:
        await _store.save_realization(entry_digest, realization)
    except FollowUpConflictError as exc:  # a concurrent follow-up got there first
        raise HTTPException(
            status_code=409, detail=_ALREADY_MEASURED.format(entry_digest)
        ) from exc

    log.info(
        "api.decisions.followup",
        entry_digest=entry_digest,
        decidable=realization.decidable,
        reason=realization.undecidable_reason,
        uid=user.get("uid"),
    )

    if realization.decidable:
        detail = (
            f"predicted {followup.predicted_benefit:.4g}, measured "
            f"{realization.realized_benefit:.4g} in the same direction"
            if followup.predicted_benefit is not None and realization.realized_benefit is not None
            else "the later window produced an estimate"
        )
    else:
        detail = (
            f"the later window cannot answer this question ({realization.undecidable_reason}): "
            f"{realization.undecidable_detail}"
        )
    return FollowUpResponse(realization=realization, explanation=detail)


@router.get(
    "/calibration",
    response_model=CalibrationReport,
    summary="Were the decisions right?",
)
async def calibration(
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/decisions/calibration")),
) -> CalibrationReport:
    """Predicted against measured, over every follow-up taken.

    Every rate is null when nothing stands behind it: a report over no decidable
    measurements says so rather than reporting a perfect score. The undecidable
    ones are counted beside the rates, by reason, because they are not missing at
    random.

    This is what separates thresholds that are *consistent* from thresholds that
    are *calibrated*, and until enough follow-ups exist the honest reading of this
    endpoint is the counts, not the rates.
    """
    return report(await _store.realizations(limit=limit))

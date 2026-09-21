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
from typing import Annotated, Any, Literal

import causal_core as cc
import pandas as pd
import structlog
from causal_core.contract import AnalysisPolicy
from causal_decision import DecisionPolicy, DecisionRequest, RiskClass, decide, explain
from causal_decision.ledger import ChainReport, DecisionRecord
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.middleware.auth import get_current_user
from src.semantic_layer.decision_store import DecisionStore, LedgerConflictError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/decisions", tags=["Decision Assurance"])

_store = DecisionStore()

Cell = float | int | str | bool | None


class DecideRequest(BaseModel):
    """What the agent proposes, and the data to judge it on."""

    action: str = Field(..., min_length=1, description="The action, as it would be executed.")
    graph: str = Field(..., min_length=1, description="Causal graph, e.g. 'Z -> X -> Y; Z -> Y'.")
    treatment: str
    outcome: str
    direction: Literal["increase", "decrease"] = Field(
        description="Which way the outcome should move, so a benefit is always positive."
    )
    data: dict[str, list[Cell]] = Field(..., description="Columns as lists.")
    risk_class: Literal["low", "medium", "high"] = "medium"
    reversible: bool = True
    minimum_worthwhile_effect: float = Field(default=0.0, ge=0.0)
    harm_threshold: float = Field(default=0.0, ge=0.0)
    can_randomize: bool = True
    randomization_unit: str = "unit"
    units_per_day: float | None = Field(default=None, gt=0)
    estimand: Literal["ate", "att"] = "ate"
    analysis_policy: dict[str, Any] | None = None
    decision_policy: dict[str, Any] | None = None


class DecideResponse(BaseModel):
    verdict: str
    explanation: str
    decision: dict[str, Any]
    outcome: dict[str, Any]
    record: DecisionRecord


class RecordList(BaseModel):
    records: list[DecisionRecord]
    total: int


@router.post("", response_model=DecideResponse, summary="Decide whether an action is justified")
async def make_decision(
    body: DecideRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> DecideResponse:
    """Analyse, rule, and record -- in that order, in one call.

    Returns one of `act`, `act_with_approval`, `experiment` or `refuse`, with an
    explanation whose numbers are claims of the analysis, and the ledger entry the
    decision was sealed into.
    """
    try:
        request = DecisionRequest(
            action=body.action,
            outcome=body.outcome,
            direction=body.direction,
            risk_class=RiskClass(body.risk_class),
            reversible=body.reversible,
            minimum_worthwhile_effect=body.minimum_worthwhile_effect,
            harm_threshold=body.harm_threshold,
            can_randomize=body.can_randomize,
            randomization_unit=body.randomization_unit,
            units_per_day=body.units_per_day,
        )
        policy = DecisionPolicy.model_validate(body.decision_policy or {})
        analysis = AnalysisPolicy.model_validate(body.analysis_policy or {})
        frame = pd.DataFrame(body.data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc

    outcome = cc.analyze(
        frame,
        body.graph,
        treatment=body.treatment,
        outcome=body.outcome,
        estimand=body.estimand,
        policy=analysis,
    )
    decision = decide(outcome, request, policy)

    try:
        record = await _store.record(
            decision, outcome, recorded_at=datetime.now(UTC).isoformat()
        )
    except LedgerConflictError as exc:
        # The decision stands; only its place in the line is contested. Saying so is
        # better than returning a verdict the ledger does not have.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log.info(
        "api.decisions.decided",
        action=body.action,
        verdict=str(decision.verdict),
        sequence=record.sequence,
        uid=user.get("uid"),
    )

    return DecideResponse(
        verdict=str(decision.verdict),
        explanation=explain(decision, outcome),
        decision=decision.model_dump(mode="json"),
        outcome=outcome.model_dump(mode="json"),
        record=record,
    )


@router.get("", response_model=RecordList, summary="List recorded decisions")
async def list_decisions(
    action: Annotated[str | None, Query(description="Filter to one action")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    user: dict[str, Any] = Depends(get_current_user),
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
async def verify_ledger(user: dict[str, Any] = Depends(get_current_user)) -> ChainReport:
    """Recompute every digest and link from the stored records.

    A pass means the chain is *internally* consistent. It cannot mean more than
    that: whoever can write these rows can rewrite the whole chain and it will
    verify again. The head digest is in the report because publishing it somewhere
    this database's owner does not control is what turns detection into evidence.
    """
    return await _store.verify()

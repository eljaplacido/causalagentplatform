"""
Analysis over HTTP: check a graph, estimate an effect, try an example -- without a verdict.

The analysis page works in three steps, and these are its endpoints:

- `POST /api/v1/analysis/check` parses a graph and, given a treatment and an outcome,
  says whether the effect is identified *before* any data is sent: the adjustment set
  or the reason it is not identified, what would change that, unreviewed edges and the
  graph's testable implications. With the data's column names it applies the rule the
  analysis applies (a graph node with no column is unmeasured), so the check shows what
  the analysis will do.
- `POST /api/v1/analysis/run` runs `causal_core.analyze` and returns its typed outcome
  with the plain-language report. It records nothing and rules on nothing: a verdict is
  sealed only by `POST /api/v1/decisions`, which re-runs the analysis itself rather than
  taking this one's result, so nothing a caller sends can stand in for the estimate.
- `GET /api/v1/analysis/examples[/{id}]` serves datasets simulated from structural
  models with known effects (src.analysis_examples).

These are the same kernel calls the causal-mcp tools make (causal_check_graph,
causal_identify, causal_analyze); the backend serves them so the UI need not speak MCP.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import causal_core as cc
import pandas as pd
from causal_core.contract import (
    AnalysisPolicy,
    CausalGraph,
    CausalQuery,
    IdentificationCertificate,
)
from causal_core.graph import Graph, GraphSyntaxError, parse_graph
from causal_core.identification import (
    IdentificationError,
    InvalidAdjustmentSet,
    identify,
    missing_information,
)
from causal_core.refute import implied_independences
from causal_decision.contract import CausalOutcome  # noqa: TC002 -- pydantic resolves it at runtime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.analysis_examples import Example, example, examples
from src.api.decisions import Cell, ModelSelector, QueryOptions, _model_version
from src.middleware.rbac import guard

router = APIRouter(prefix="/api/v1/analysis", tags=["Analysis"])


# ── check ─────────────────────────────────────────────────────────────────────


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: str = Field(..., min_length=1, description="e.g. 'Z -> X -> Y; Z -> Y'")
    treatment: str | None = None
    outcome: str | None = None
    estimand: Literal["ate", "att"] = "ate"
    columns: list[str] | None = Field(
        default=None,
        description="The data's column names, if known: graph nodes without a column are "
        "then treated as unmeasured, as the analysis will treat them.",
    )
    query: QueryOptions = Field(default_factory=QueryOptions)
    max_implications: int = Field(default=30, ge=0, le=200)


class Implication(BaseModel):
    a: str
    b: str
    given: list[str]


class Identification(BaseModel):
    identified: bool
    certificate: IdentificationCertificate | None = None
    refusal: str | None = None
    missing_information: list[str] = Field(default_factory=list)


class CheckResponse(BaseModel):
    valid: bool
    error: str | None = None
    graph: CausalGraph | None = None
    observed: list[str] = Field(default_factory=list)
    latent: list[str] = Field(default_factory=list)
    unmeasured: list[str] = Field(
        default_factory=list,
        description="Graph nodes the data has no column for: treated as latent.",
    )
    unused_columns: list[str] = Field(
        default_factory=list, description="Data columns the graph does not mention."
    )
    unreviewed_edges: list[str] = Field(default_factory=list)
    testable_implications: list[Implication] = Field(default_factory=list)
    identification: Identification | None = Field(
        default=None, description="Present when both treatment and outcome were given."
    )


def _identification(g: Graph, query: CausalQuery) -> Identification:
    try:
        cert = identify(g, query)
    except InvalidAdjustmentSet as exc:
        hint = (
            [f"a valid adjustment set is {{{', '.join(exc.suggestion)}}}"]
            if exc.suggestion
            else ["no valid adjustment set exists among the measured variables"]
        )
        return Identification(identified=False, refusal=exc.detail, missing_information=hint)
    except (IdentificationError, ValueError) as exc:
        return Identification(identified=False, refusal=str(exc))
    if cert.status.value != "identified":
        return Identification(
            identified=False,
            certificate=cert,
            refusal=f"not identified: {cert.general_id}",
            missing_information=list(missing_information(g, query.treatment, query.outcome)),
        )
    return Identification(identified=True, certificate=cert)


@router.post("/check", response_model=CheckResponse, summary="Check a graph and its question")
async def check(
    body: CheckRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/analysis/check")),
) -> CheckResponse:
    """Parse the graph; with treatment and outcome, identify the effect from it alone."""
    try:
        spec = parse_graph(body.graph)
        unmeasured: list[str] = []
        if body.columns is not None:
            # causal_core.pipeline._effective_graph: the rule the analysis applies.
            present = set(body.columns)
            unmeasured = [n for n in spec.nodes if n not in present and n not in spec.latent]
            if unmeasured:
                spec = spec.model_copy(update={"latent": tuple(spec.latent) + tuple(unmeasured)})
        g = Graph.from_spec(spec)
    except (GraphSyntaxError, ValueError) as exc:
        return CheckResponse(valid=False, error=str(exc))

    observed = sorted(g.observed)
    columns = pd.DataFrame(columns=observed, dtype=float)
    imps = implied_independences(g, columns, set(), body.max_implications)
    response = CheckResponse(
        valid=True,
        graph=spec,
        observed=observed,
        latent=sorted(n for n in g.latent if not n.startswith("⟨") and n not in unmeasured),
        unmeasured=sorted(unmeasured),
        unused_columns=sorted(set(body.columns or ()) - set(spec.nodes)),
        unreviewed_edges=list(g.unreviewed_edges()),
        testable_implications=[Implication(a=a, b=b, given=list(s)) for a, b, s in imps],
    )
    if body.treatment and body.outcome:
        for role, v in (("treatment", body.treatment), ("outcome", body.outcome)):
            if v not in spec.nodes:
                response.identification = Identification(
                    identified=False, refusal=f"{role} {v!r} is not in the graph"
                )
                return response
            if v in unmeasured:
                response.identification = Identification(
                    identified=False, refusal=f"{role} {v!r} is not a column of the data"
                )
                return response
        try:
            query = CausalQuery(
                treatment=body.treatment,
                outcome=body.outcome,
                estimand=body.estimand,
                **body.query.model_dump(exclude_unset=True),
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
        response.identification = _identification(g, query)
    return response


# ── run ───────────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: str | None = Field(default=None, min_length=1)
    model: ModelSelector | None = None
    treatment: str
    outcome: str
    estimand: Literal["ate", "att"] = "ate"
    data: dict[str, list[Cell]] = Field(..., description="Columns as lists.")
    query: QueryOptions = Field(default_factory=QueryOptions)
    analysis_policy: AnalysisPolicy | None = None

    @model_validator(mode="after")
    def _one_graph(self) -> RunRequest:
        if (self.graph is None) == (self.model is None):
            raise ValueError("give exactly one of `graph` (inline) or `model` (registered)")
        return self


class RunResponse(BaseModel):
    outcome: Annotated[CausalOutcome, Field(discriminator="kind")]
    report: str = Field(description="Plain language, built only from the outcome's claims.")


@router.post("/run", response_model=RunResponse, summary="Estimate an effect (no verdict)")
async def run(
    body: RunRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/analysis/run")),
) -> RunResponse:
    """Run the kernel's analysis. Nothing is recorded: deciding is POST /api/v1/decisions."""
    try:
        query = CausalQuery(
            treatment=body.treatment,
            outcome=body.outcome,
            estimand=body.estimand,
            **body.query.model_dump(exclude_unset=True),
        )
        frame = pd.DataFrame(body.data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
    graph: str | CausalGraph
    if body.model is not None:
        graph = (await _model_version(body.model)).graph
    elif body.graph is not None:
        graph = body.graph
    else:  # RunRequest._one_graph refuses this first; a raise because -O strips asserts
        raise HTTPException(status_code=422, detail="give exactly one of `graph` or `model`")
    out = cc.analyze(frame, graph, query=query, policy=body.analysis_policy or AnalysisPolicy())
    return RunResponse(outcome=out, report=cc.report(out))


# ── examples ──────────────────────────────────────────────────────────────────


class ExampleSummary(BaseModel):
    id: str
    title: str
    summary: str
    graph: str
    treatment: str
    outcome: str
    design: Literal["observational", "randomized"]
    direction: Literal["increase", "decrease"]
    action: str
    minimum_worthwhile_effect: float
    true_effect: float | None
    true_effect_note: str
    lesson: str
    n_rows: int
    column_names: list[str]


class ExampleData(ExampleSummary):
    columns: dict[str, list[Cell]]


def _summary(ex: Example) -> dict[str, Any]:
    return {
        "id": ex.id,
        "title": ex.title,
        "summary": ex.summary,
        "graph": ex.graph,
        "treatment": ex.treatment,
        "outcome": ex.outcome,
        "design": ex.design,
        "direction": ex.direction,
        "action": ex.action,
        "minimum_worthwhile_effect": ex.minimum_worthwhile_effect,
        "true_effect": ex.true_effect,
        "true_effect_note": ex.true_effect_note,
        "lesson": ex.lesson,
        "n_rows": ex.n_rows,
        "column_names": list(ex.columns),
    }


@router.get("/examples", response_model=list[ExampleSummary], summary="Example datasets")
async def list_examples(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/analysis/examples")),
) -> list[ExampleSummary]:
    return [ExampleSummary(**_summary(ex)) for ex in examples()]


@router.get("/examples/{example_id}", response_model=ExampleData, summary="One example, with data")
async def get_example(
    example_id: str,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/analysis/examples/{example_id}")),
) -> ExampleData:
    ex = example(example_id)
    if ex is None:
        raise HTTPException(status_code=404, detail=f"no example {example_id!r}")
    return ExampleData(**_summary(ex), columns=ex.columns)

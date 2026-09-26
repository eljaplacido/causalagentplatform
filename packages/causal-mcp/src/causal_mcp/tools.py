"""
Tool implementations. Plain functions over the causal-core contract, with no
MCP types, so they are unit-testable without a transport and reusable from
any other agent framework.

Every tool is read-only: none writes files, calls the network or changes
state. Governance actions (approving an edge, acting on a result) are
deliberately *not* tools here; an agent must never be able to grant itself
an approval.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import causal_core as cc
import pandas as pd
from causal_core.claims import Verification, render, verify
from causal_core.contract import (
    OUTCOME_ADAPTER,
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    ApparatusFailure,
    CausalGraph,
    CausalQuery,
    CausalResult,
    IdentificationCertificate,
    Provenance,
)
from causal_core.graph import Graph, GraphSyntaxError, parse_graph
from causal_core.identification import (
    IdentificationError,
    InvalidAdjustmentSet,
    identify,
    missing_information,
)
from causal_core.refute import implied_independences
from pydantic import BaseModel, Field

from causal_mcp.query_params import IDENTIFICATION_FIELDS, takes_query

Cell = float | int | str | bool | None


class AnalyzeResponse(BaseModel):
    """The typed outcome plus a plain-language report built only from its claims."""

    outcome: CausalResult | Abstention | ApparatusFailure = Field(discriminator="kind")
    report: str


class IdentifyResponse(BaseModel):
    identified: bool
    certificate: IdentificationCertificate | None = None
    refusal: str | None = None
    missing_information: list[str] = Field(default_factory=list)


class Implication(BaseModel):
    a: str
    b: str
    given: list[str]


class GraphCheckResponse(BaseModel):
    valid: bool
    error: str | None = None
    graph: CausalGraph | None = None
    observed: list[str] = Field(default_factory=list)
    latent: list[str] = Field(default_factory=list)
    unreviewed_edges: list[str] = Field(default_factory=list)
    testable_implications: list[Implication] = Field(
        default_factory=list,
        description="Conditional independences the graph implies among observed variables. "
        "Test them on data before trusting the graph; each is a claim of an absent edge.",
    )


class VerifyResponse(BaseModel):
    ok: bool
    rendered: str
    unbound_numbers: list[str]
    unknown_tokens: list[str]
    causal_language: list[str]
    reasons: list[str]


def data_roots() -> list[Path]:
    raw = os.environ.get("CAUSAL_MCP_DATA_ROOTS")
    roots = raw.split(os.pathsep) if raw else [os.getcwd()]
    return [Path(r).resolve() for r in roots if r]


def load_table(data: dict[str, list[Cell]] | None, data_path: str | None) -> pd.DataFrame:
    if (data is None) == (data_path is None):
        raise ValueError("pass exactly one of data (columns as lists) or data_path")
    if data is not None:
        return pd.DataFrame(data)
    assert data_path is not None
    path = Path(data_path).resolve()
    if not any(path.is_relative_to(root) for root in data_roots()):
        raise PermissionError(
            f"{data_path} is outside the allowed data roots; set CAUSAL_MCP_DATA_ROOTS"
        )
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        return pd.read_json(path, lines=suffix == ".jsonl")
    return pd.read_csv(path)


@takes_query()
def causal_analyze(
    graph: str,
    data: dict[str, list[Cell]] | None = None,
    data_path: str | None = None,
    policy: dict[str, Any] | None = None,
    *,
    query: dict[str, Any],
) -> AnalyzeResponse:
    """Estimate the effect of `treatment` on `outcome`, or return a typed refusal saying why not.

    Identification comes first (from `graph`), then design checks, a portfolio of estimators,
    falsification tests and sensitivity analysis. The outcome is exactly one of `result` (an
    estimate with interval, certificate and diagnostics), `refusal` (a typed reason plus what
    information would make the question answerable -- a correct answer, not an error) or
    `apparatus_failure`. Use numbers in your reply only via `outcome.claims`, and pass your
    text to `causal_verify_narrative` before replying to a user.

    Each analysis parameter is described in the input schema, from the kernel's contract.
    How they combine:

    - **Segments** (`segment_by`, declared before you see the answer): quote a segment's
      family-wise interval, and if `heterogeneity.differs` is false, say the segments could
      not be told apart rather than comparing them.
    - **Staggered rollout** (panel data): `design="difference_in_differences"`, `unit`, `time`,
      `estimand="att"`. Parallel trends is not in the graph, so the certificate is tier C and a
      failed pre-trends test makes the result `contested`. `covariates` if trends are parallel
      only given them; `control_group="not_yet_treated"` if every unit is eventually treated.
    - **Watched canary**: `design="randomized"`, `sequence`, `expected_treated_share`. Read and
      stop on the confidence sequence as often as you like; do not re-run a fixed-sample
      analysis after every batch instead -- read that way its error rate is far above 5%.
    - **Randomized by service, measured by request**: `cluster`, or the interval can be several
      times too narrow. **Switchback**: `cluster` = the block, `time` = the minute, `washout`
      chosen from what you know of the system. A carryover still settling after the washout
      makes the result `contested`; the check catches only a large one, so a pass is weak.

    `graph` syntax: `Z -> X -> Y; Z -> Y`; `X <-> Y` means an unmeasured common cause;
    `latent U` declares an unmeasured node. Data: either `data` as {column: [values]} or
    `data_path` (CSV / Parquet / JSON under the server's allowed data roots).
    """
    try:
        cq = CausalQuery(**query)
        pol = AnalysisPolicy.model_validate(policy or {})
        frame = load_table(data, data_path)
    except Exception as exc:
        refusal = Abstention(
            reason=AbstentionReason.INVALID_INPUT,
            detail=f"{type(exc).__name__}: {exc}"[:2000],
            provenance=Provenance(
                contract_version=cc.CONTRACT_VERSION, core_version=cc.__version__
            ),
        )
        return AnalyzeResponse(outcome=refusal, report=cc.report(refusal))
    out = cc.analyze(frame, graph, query=cq, policy=pol)
    return AnalyzeResponse(outcome=out, report=cc.report(out))


@takes_query(IDENTIFICATION_FIELDS)
def causal_identify(graph: str, *, query: dict[str, Any]) -> IdentifyResponse:
    """Decide whether the effect is identifiable from the graph alone -- before collecting data.

    Returns the identification certificate (strategy, adjustment set or instruments, the
    identifying expression, and the result of the complete ID algorithm), or the reason it is
    not identified and what would change that. A user-chosen `adjust_for` is validated.
    """
    try:
        cq = CausalQuery(**query)
        g = Graph.from_spec(parse_graph(graph))
        cert = identify(g, cq)
    except InvalidAdjustmentSet as exc:
        hint = (
            [f"a valid adjustment set is {{{', '.join(exc.suggestion)}}}"]
            if exc.suggestion
            else ["no valid adjustment set exists among the measured variables"]
        )
        return IdentifyResponse(identified=False, refusal=exc.detail, missing_information=hint)
    except (IdentificationError, GraphSyntaxError, ValueError) as exc:
        return IdentifyResponse(identified=False, refusal=str(exc))
    if cert.status.value != "identified":
        return IdentifyResponse(
            identified=False,
            certificate=cert,
            refusal=f"not identified: {cert.general_id}",
            missing_information=list(missing_information(g, cq.treatment, cq.outcome)),
        )
    return IdentifyResponse(identified=True, certificate=cert)


def causal_check_graph(graph: str, max_implications: int = 50) -> GraphCheckResponse:
    """Parse and validate a causal graph, and list the independences it implies.

    Use this while drafting a graph with a user: acyclicity, unreviewed (e.g. LLM-proposed)
    edges, and every testable implication -- which are the graph's riskiest claims.
    """
    try:
        spec = parse_graph(graph)
        g = Graph.from_spec(spec)
    except (GraphSyntaxError, ValueError) as exc:
        return GraphCheckResponse(valid=False, error=str(exc))
    columns = pd.DataFrame(columns=sorted(g.observed), dtype=float)
    imps = implied_independences(g, columns, set(), max_implications)
    return GraphCheckResponse(
        valid=True,
        graph=spec,
        observed=sorted(g.observed),
        latent=sorted(n for n in g.latent if not n.startswith("⟨")),
        unreviewed_edges=g.unreviewed_edges(),
        testable_implications=[Implication(a=a, b=b, given=list(s)) for a, b, s in imps],
    )


def causal_verify_narrative(text: str, outcome: dict[str, Any]) -> VerifyResponse:
    """Check prose about a causal outcome before it reaches a user. Fail-closed.

    Renders claim tokens (`⟦claim:effect⟧` or `[[claim:effect]]`), then rejects any number
    that is not a faithful rounding of a claim in `outcome`, any unknown claim token, and any
    causal wording ("caused", "will reduce", "root cause", ...) unless the outcome is a
    `supported` result. Pass the `outcome` object returned by `causal_analyze` unchanged.
    """
    parsed = OUTCOME_ADAPTER.validate_python(outcome)
    try:
        rendered = render(text, parsed)
    except KeyError:
        rendered = text
    v: Verification = verify(text, parsed)
    return VerifyResponse(
        ok=v.ok,
        rendered=rendered,
        unbound_numbers=v.unbound_numbers,
        unknown_tokens=v.unknown_tokens,
        causal_language=v.causal_language,
        reasons=v.reasons(),
    )


# ── the decision layer (optional: pip install causal-mcp[decision]) ───────────

try:
    from causal_decision import (
        Decision,
        DecisionPolicy,
        DecisionRequest,
        RiskClass,
        decide,
        explain,
    )
    from causal_decision.assumptions import Impact, diff, edge_key, impact

    HAS_DECISION = True
except ImportError:  # pragma: no cover - exercised by the packaging test
    HAS_DECISION = False


if HAS_DECISION:

    class DecideResponse(BaseModel):
        """A verdict, the evidence it rested on, and an explanation whose numbers are claims."""

        decision: Decision
        outcome: CausalResult | Abstention | ApparatusFailure = Field(discriminator="kind")
        explanation: str

    @takes_query()
    def causal_decide(
        graph: str,
        action: str,
        direction: Literal["increase", "decrease"],
        data: dict[str, list[Cell]] | None = None,
        data_path: str | None = None,
        risk_class: Literal["low", "medium", "high"] = "medium",
        reversible: bool = True,
        minimum_worthwhile_effect: float = 0.0,
        harm_threshold: float = 0.0,
        can_randomize: bool = True,
        randomization_unit: str = "unit",
        units_per_day: float | None = None,
        policy: dict[str, Any] | None = None,
        decision_policy: dict[str, Any] | None = None,
        *,
        query: dict[str, Any],
    ) -> DecideResponse:
        """Decide whether a proposed action is justified by the evidence.

        Returns one verdict: `act`, `act_with_approval`, `experiment` or `refuse`.

        Analyses the data and applies the decision policy in a single call, so nothing can
        change the evidence between the estimate and the verdict -- not even you. The verdict
        is a pure function of (outcome, request, policy) and carries the digests of all three,
        so it can be replayed and disputed later.

        State what acting is worth before you ask: `minimum_worthwhile_effect` is the smallest
        benefit, in outcome units, that justifies the action, and `direction` says which way the
        outcome should move (so a benefit is always positive). `risk_class` and `reversible`
        describe the cost of being wrong; high risk always goes to a person.

        Declare `segment_by` BEFORE you see any answer: a pooled effect is an average, and an
        action that helps one group and harms another can pool to something that looks fine.
        The verdict will not automate an action that materially harms a declared subgroup.
        Not declaring any is not evidence that the effect is uniform -- nobody looked.

        The analysis parameters are `causal_analyze`'s, described in the input schema.

        When the evidence cannot settle it, the response carries the experiment that would,
        sized from the data. Relay `explanation` rather than writing your own: its numbers are
        bound to the analysis.
        """
        try:
            cq = CausalQuery(**query)
            request = DecisionRequest(
                action=action,
                outcome=cq.outcome,
                direction=direction,
                risk_class=RiskClass(risk_class),
                reversible=reversible,
                minimum_worthwhile_effect=minimum_worthwhile_effect,
                harm_threshold=harm_threshold,
                can_randomize=can_randomize,
                randomization_unit=randomization_unit,
                units_per_day=units_per_day,
                # The query's labels are the request's, as in the platform's HTTP endpoint.
                population=cq.population,
                horizon=cq.horizon,
            )
            dpol = DecisionPolicy.model_validate(decision_policy or {})
            pol = AnalysisPolicy.model_validate(policy or {})
            frame = load_table(data, data_path)
        except Exception as exc:
            refusal = Abstention(
                reason=AbstentionReason.INVALID_INPUT,
                detail=f"{type(exc).__name__}: {exc}"[:2000],
                provenance=Provenance(
                    contract_version=cc.CONTRACT_VERSION, core_version=cc.__version__
                ),
            )
            bad = DecisionRequest(action=action, outcome=str(query["outcome"]), direction=direction)
            d = decide(refusal, bad, DecisionPolicy())
            return DecideResponse(decision=d, outcome=refusal, explanation=explain(d, refusal))
        out = cc.analyze(frame, graph, query=cq, policy=pol)
        d = decide(out, request, dpol)
        return DecideResponse(decision=d, outcome=out, explanation=explain(d, out))

    class GraphChange(BaseModel):
        """What the revision changed, as edge labels ("a -> b", "a <-> b")."""

        added_edges: list[str]
        removed_edges: list[str] = Field(
            description="Each is a new assumption: that the effect it described does not exist."
        )
        changed_edges: list[str] = Field(description="Same edge; origin or review status changed.")

    class ImpactResponse(BaseModel):
        """Whether the estimate made under the old graph survives the revision, and what changed.

        The diff is edge labels rather than full edge objects: the tool's schema stays small
        enough for clients that cap a tool listing's size.
        """

        impact: Impact | None = None
        changes: GraphChange | None = None
        refusal: str | None = None

    @takes_query(IDENTIFICATION_FIELDS)
    def causal_impact(
        old_graph: str,
        new_graph: str,
        unmeasured: list[str] | None = None,
        *,
        query: dict[str, Any],
    ) -> ImpactResponse:
        """Ask whether a revised causal graph breaks an estimate made under the old one.

        Call this whenever the graph behind an earlier analysis or decision changes: an
        edge added, removed or reversed, a hidden confounder declared. The answer is
        `holds` (the old estimate is still valid if the new graph is true), `invalidated`
        (re-analyse before relying on anything that used it), `unverified` (a front-door,
        IV or proximal estimate whose certificate changed: re-analyse), or, when the old
        graph could not identify the effect, `gained` / `still_unidentified`.

        Pass the earlier query: `adjust_for` as the set its analysis USED (its certificate's
        `adjustment_set`), so that set is judged -- not whatever the new graph would suggest --
        and its `design`, `instruments` and proxies, so its own strategy is re-certified.
        Pass `unmeasured` for graph nodes the data had no column for. "Still back-door" and
        "the new set is valid" are NOT "the old estimate stands"; this tool answers the last.
        """
        try:
            got = impact(old_graph, new_graph, CausalQuery(**query), unmeasured=unmeasured or ())
            d = diff(parse_graph(old_graph), parse_graph(new_graph))
            return ImpactResponse(
                impact=got,
                changes=GraphChange(
                    added_edges=[edge_key(e) for e in d.added_edges],
                    removed_edges=[edge_key(e) for e in d.removed_edges],
                    changed_edges=[c.edge for c in d.changed_edges],
                ),
            )
        except (GraphSyntaxError, IdentificationError, InvalidAdjustmentSet, ValueError) as exc:
            return ImpactResponse(refusal=f"{type(exc).__name__}: {exc}"[:2000])

"""
The canonical pipeline: one call from data + graph + question to a typed outcome.

    query -> identify -> design checks -> estimate (portfolio) -> triangulate
          -> refute -> sensitivity -> claims -> provenance

It returns exactly one of :class:`CausalResult`, :class:`Abstention` or
:class:`ApparatusFailure` and never raises for bad input: a malformed query, an
unknown column or an invalid adjustment set is a refusal with a reason; an
unexpected exception is an apparatus failure that names the stage it happened in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any, TypeVar

import numpy as np
import pandas as pd
from pydantic import ValidationError

from causal_core import estimators as est_registry
from causal_core import refute as refuters
from causal_core.canonical import data_digest, digest
from causal_core.claims import build_claims
from causal_core.contract import (
    CONTRACT_VERSION,
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    ApparatusFailure,
    CausalGraph,
    CausalQuery,
    CausalResult,
    Diagnostics,
    EdgeStatus,
    IdentificationCertificate,
    IdentificationStatus,
    Provenance,
    ResultStatus,
    Rung,
    Triangulation,
    TriangulationPair,
)
from causal_core.design import prepare
from causal_core.estimators import Fit
from causal_core.graph import Graph, GraphSyntaxError, as_causal_graph
from causal_core.identification import (
    IdentificationError,
    IdentificationInconsistency,
    InvalidAdjustmentSet,
    identify,
    missing_information,
)
from causal_core.refute import RefutationContext, holm
from causal_core.sensitivity import analyze_sensitivity

try:
    CORE_VERSION = version("causal-core")
except PackageNotFoundError:  # running from a source checkout
    CORE_VERSION = "0.2.0.dev0+src"

Outcome = CausalResult | Abstention | ApparatusFailure
T = TypeVar("T")

#: Floor on the standard error of a difference between two estimators, as a share
#: of the smaller estimator SE. Two estimators that are first-order equivalent on a
#: design (e.g. AIPW and regression adjustment under randomization) have a
#: near-zero influence-function difference, and without a floor sampling noise of
#: second order would read as disagreement. Pre-registered in benchmarks/t0.
TRIANGULATION_SE_FLOOR = 0.25


def as_frame(data: Any) -> pd.DataFrame:
    """Accept pandas, a column dict, pyarrow, polars, or any dataframe-interchange object."""
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, Mapping):
        return pd.DataFrame(dict(data))
    to_pandas = getattr(data, "to_pandas", None)
    if callable(to_pandas):
        out = to_pandas()
        if isinstance(out, pd.DataFrame):
            return out
    if hasattr(data, "__dataframe__"):
        return pd.api.interchange.from_dataframe(data)
    raise TypeError(f"cannot read {type(data).__name__} as a table")


class _Stage:
    name = "input"


def _effective_graph(spec: CausalGraph, columns: set[str]) -> tuple[CausalGraph, list[str]]:
    """Graph nodes with no data column are unmeasured: mark them latent."""
    unmeasured = [n for n in spec.nodes if n not in columns and n not in spec.latent]
    if not unmeasured:
        return spec, []
    return (
        spec.model_copy(update={"latent": tuple(spec.latent) + tuple(unmeasured)}),
        unmeasured,
    )


def _triangulate(fits: list[Fit], alpha: float) -> Triangulation | None:
    usable = [f for f in fits if f.influence is not None and f.record.se]
    if len(usable) < 2:
        return None
    primary, others = usable[0], usable[1:]
    pairs: list[TriangulationPair] = []
    pvals: list[float] = []
    from scipy import stats

    for other in others:
        a, b = primary.record, other.record
        n = len(primary.influence)  # type: ignore[arg-type]
        diff_if = primary.influence - other.influence  # type: ignore[operator]
        se_if = float(np.sqrt(np.sum((diff_if - diff_if.mean()) ** 2)) / n)
        floor = TRIANGULATION_SE_FLOOR * min(a.se or 0.0, b.se or 0.0)
        se_diff = max(se_if, floor)
        diff = a.point - b.point
        z = diff / se_diff if se_diff > 0 else 0.0
        pairs.append(
            TriangulationPair(
                a=a.estimator, b=b.estimator, difference=diff, se_difference=se_diff, z=z
            )
        )
        pvals.append(float(2 * stats.norm.sf(abs(z))))
    rejected = holm(pvals, alpha)
    agree = not any(rejected)
    detail = (
        "estimators with different modelling assumptions agree within sampling noise"
        if agree
        else "estimators disagree beyond sampling noise: at least one model is misspecified; "
        "the estimate is reported but causal wording is withheld"
    )
    return Triangulation(
        estimators=tuple(f.record.estimator for f in usable),
        agree=agree,
        pairs=tuple(pairs),
        detail=detail,
    )


def analyze(
    data: Any,
    graph: CausalGraph | str | Any,
    *,
    treatment: str | None = None,
    outcome: str | None = None,
    query: CausalQuery | None = None,
    policy: AnalysisPolicy | None = None,
    **query_fields: Any,
) -> Outcome:
    """Answer "what is the effect of ``treatment`` on ``outcome``?" -- or say why not.

    ``graph`` is a :class:`CausalGraph`, the text syntax of :mod:`causal_core.graph`
    (``"Z -> X -> Y; Z -> Y"``), a networkx DiGraph, or a list of edges. Extra
    keyword arguments are :class:`CausalQuery` fields (``design``,
    ``instruments``, ``negative_control_outcomes``, ...).
    """
    stage = _Stage()
    policy = policy or AnalysisPolicy()
    prov: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "core_version": CORE_VERSION,
        "policy_digest": digest(policy),
        "seed": policy.seed,
    }

    def provenance() -> Provenance:
        return Provenance(**prov)

    def refuse(
        reason: AbstentionReason,
        detail: str,
        missing: tuple[str, ...] = (),
        q: CausalQuery | None = None,
        cert: IdentificationCertificate | None = None,
        diag: Diagnostics | None = None,
    ) -> Abstention:
        return Abstention(
            reason=reason,
            detail=detail,
            missing_information=missing,
            query=q,
            certificate=cert,
            diagnostics=diag,
            provenance=provenance(),
        )

    try:
        return _analyze(
            data, graph, treatment, outcome, query, policy, query_fields, stage, prov, refuse
        )
    except Exception as exc:
        return ApparatusFailure(
            stage=stage.name,
            error_type=type(exc).__name__,
            detail=str(exc)[:2000],
            provenance=provenance(),
        )


def _analyze(
    data: Any,
    graph: Any,
    treatment: str | None,
    outcome: str | None,
    query: CausalQuery | None,
    policy: AnalysisPolicy,
    query_fields: dict[str, Any],
    stage: _Stage,
    prov: dict[str, Any],
    refuse: Callable[..., Abstention],
) -> Outcome:
    # ── input ────────────────────────────────────────────────────────────────
    stage.name = "input"
    if query is None:
        try:
            query = CausalQuery(treatment=treatment, outcome=outcome, **query_fields)
        except ValidationError as exc:
            return refuse(AbstentionReason.INVALID_INPUT, f"invalid query: {exc}")
    elif treatment or outcome or query_fields:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            "pass either query= or treatment/outcome keyword arguments, not both",
        )
    prov["query_digest"] = digest(query)
    if query.rung is not Rung.intervention:
        return refuse(
            AbstentionReason.RUNG_MISMATCH,
            f"this kernel answers interventional questions (rung 2); the query declares "
            f"rung '{query.rung.value}'"
            + (
                ". Unit-level counterfactuals need a fully specified structural model, "
                "which is not implemented"
                if query.rung is Rung.counterfactual
                else ". An associational question needs no identification: describe the data"
            ),
            q=query,
        )
    try:
        frame = as_frame(data)
    except Exception as exc:
        return refuse(AbstentionReason.INVALID_INPUT, f"cannot read data: {exc}", q=query)
    frame.columns = [str(c) for c in frame.columns]
    try:
        spec = as_causal_graph(graph)
    except (GraphSyntaxError, ValidationError, TypeError, ValueError) as exc:
        return refuse(AbstentionReason.INVALID_INPUT, f"cannot read graph: {exc}", q=query)
    for role, v in (("treatment", query.treatment), ("outcome", query.outcome)):
        if v not in frame.columns:
            return refuse(
                AbstentionReason.INVALID_INPUT, f"{role} {v!r} is not a column of the data", q=query
            )
        if v not in spec.nodes and not any(v in (e.source, e.target) for e in spec.edges):
            return refuse(
                AbstentionReason.INVALID_INPUT, f"{role} {v!r} is not in the graph", q=query
            )
    effective, unmeasured = _effective_graph(spec, set(frame.columns))
    try:
        g = Graph.from_spec(effective)
    except GraphSyntaxError as exc:
        return refuse(AbstentionReason.INVALID_INPUT, f"invalid graph: {exc}", q=query)
    prov["graph_digest"] = digest(effective)
    warnings: list[str] = []
    if unmeasured:
        warnings.append(
            f"graph variables with no data column are treated as unmeasured: {unmeasured}"
        )
    unreviewed = g.unreviewed_edges()
    if unreviewed and policy.require_approved_graph:
        return refuse(
            AbstentionReason.UNAPPROVED_ASSUMPTION,
            f"the graph contains edges that have not been reviewed: {unreviewed}",
            ("approve or refute each proposed edge (data test, experiment or human sign-off)",),
            q=query,
        )
    refuted: list[str] = [g.edge_label(e) for e in spec.edges if e.status is EdgeStatus.refuted]
    if refuted:
        warnings.append(f"refuted edges were removed before analysis: {refuted}")
    de_x = g.descendants({query.treatment})
    for nco in query.negative_control_outcomes:
        if nco in de_x:
            return refuse(
                AbstentionReason.INVALID_INPUT,
                f"negative control {nco!r} is a descendant of the treatment in the graph, so it "
                "is not a negative control",
                q=query,
            )

    # ── identification ───────────────────────────────────────────────────────
    stage.name = "identify"
    try:
        cert = identify(g, query)
    except IdentificationError as exc:
        return refuse(AbstentionReason.INVALID_INPUT, str(exc), q=query)
    except InvalidAdjustmentSet as exc:
        fix = (
            (f"a valid adjustment set is {{{', '.join(exc.suggestion)}}}",)
            if exc.suggestion is not None
            else ("no valid adjustment set exists among the measured variables",)
        )
        return refuse(AbstentionReason.INVALID_ADJUSTMENT_SET, exc.detail, fix, q=query)
    except IdentificationInconsistency:
        raise
    if cert.status is IdentificationStatus.not_identified:
        return refuse(
            AbstentionReason.NON_IDENTIFIABLE,
            f"the effect of {query.treatment} on {query.outcome} is not identified from the "
            f"measured variables under this graph ({cert.general_id})",
            missing_information(g, query.treatment, query.outcome),
            q=query,
            cert=cert,
        )

    # ── design ───────────────────────────────────────────────────────────────
    stage.name = "design"
    dres = prepare(frame, query, cert, policy)
    warnings.extend(dres.warnings)
    prov["data_digest"] = data_digest(dres.frame)
    if dres.abstention is not None:
        reason, detail, design_fix = dres.abstention
        return refuse(reason, detail, design_fix, q=query, cert=cert, diag=dres.diagnostics)
    d = dres.prepared
    assert d is not None and dres.diagnostics is not None

    # ── estimation ───────────────────────────────────────────────────────────
    stage.name = "estimate"
    assert cert.strategy is not None
    ests = [
        e
        for e in est_registry.applicable(cert.strategy, d.treatment_kind, d.estimand)
        if getattr(e, "supports", lambda _d, _s: True)(d, cert.strategy)
    ]
    if not ests:
        return refuse(
            AbstentionReason.NO_ESTIMATOR,
            f"identified by {cert.strategy.value} ({cert.estimand}), but no registered "
            f"estimator targets the {d.estimand.upper()} under that strategy for a "
            f"{d.treatment_kind} treatment",
            ("register an estimator for this strategy (see causal_core.estimators)",),
            q=query,
            cert=cert,
            diag=dres.diagnostics,
        )
    fits: list[Fit] = []
    for i, est in enumerate(ests):
        rng = np.random.default_rng([policy.seed, i])
        try:
            fits.append(est.fit(d, policy, rng))
        except Exception as exc:
            if i == 0:
                raise
            warnings.append(f"secondary estimator {est.name} failed: {type(exc).__name__}: {exc}")
    primary = fits[0]
    rec = primary.record
    if policy.max_ci_width is not None and rec.ci is not None:
        width = rec.ci.hi - rec.ci.lo
        if width > policy.max_ci_width:
            return refuse(
                AbstentionReason.CI_TOO_WIDE,
                f"the {rec.ci.level:.0%} interval is {width:.4g} wide; policy.max_ci_width is "
                f"{policy.max_ci_width}",
                ("collect more data or reduce outcome noise",),
                q=query,
                cert=cert,
                diag=dres.diagnostics,
            )

    stage.name = "triangulate"
    tri = _triangulate(fits, policy.triangulation_alpha)

    stage.name = "refute"
    ref_ctx = RefutationContext(
        prepared=d,
        primary=ests[0],
        policy=policy,
        query=query,
        cert=cert,
        graph=g,
        frame=dres.frame,
        data=frame,
        seed=policy.seed,
    )
    refutations = refuters.run(ref_ctx)
    warnings.extend(ref_ctx.notes)

    stage.name = "sensitivity"
    sens = analyze_sensitivity(d, primary, cert.strategy, policy) if policy.sensitivity else None

    stage.name = "assemble"
    failed = [r.name for r in refutations if not r.passed]
    if failed or (tri is not None and not tri.agree):
        status = ResultStatus.contested
        if failed:
            warnings.append(f"falsification tests failed: {failed}")
    elif unreviewed:
        status = ResultStatus.provisional
        warnings.append(f"graph contains unreviewed edges: {unreviewed}")
    else:
        status = ResultStatus.supported

    claim_fields: dict[str, tuple[float | None, str, str | None]] = {
        "effect": (rec.point, "/primary/point", None),
        "effect.se": (rec.se, "/primary/se", None),
        "effect.ci.lo": (rec.ci.lo if rec.ci else None, "/primary/ci/lo", None),
        "effect.ci.hi": (rec.ci.hi if rec.ci else None, "/primary/ci/hi", None),
        "ci.level": (rec.ci.level * 100 if rec.ci else None, "/primary/ci/level", "%"),
        "n": (float(d.n), "/diagnostics/n_used", None),
    }
    if sens is not None:
        claim_fields["e_value"] = (sens.e_value, "/sensitivity/e_value", None)
        claim_fields["e_value.ci"] = (sens.e_value_ci, "/sensitivity/e_value_ci", None)
        claim_fields["robustness_value"] = (
            sens.robustness_value,
            "/sensitivity/robustness_value",
            None,
        )
    result = CausalResult(
        status=status,
        query=query,
        certificate=cert,
        primary=rec,
        estimates=tuple(f.record for f in fits),
        diagnostics=dres.diagnostics,
        refutations=tuple(refutations),
        sensitivity=sens,
        triangulation=tri,
        claims=build_claims(claim_fields),
        warnings=tuple(warnings),
        provenance=Provenance(**prov),
    )
    return result.model_copy(
        update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))}
    )

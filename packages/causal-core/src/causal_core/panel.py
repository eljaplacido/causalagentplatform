"""
Difference-in-differences for staggered adoption, with inference from influence functions.

Operations roll changes out in waves: some services this week, others next month.
The question afterwards -- what did the change do? -- is a panel question, and the
estimator most people reach for, a two-way fixed-effects regression of the outcome
on unit and period dummies plus a treatment indicator, is wrong there. When effects
differ across cohorts or grow over time it uses already-treated units as controls
for later adopters and can report the wrong sign (Goodman-Bacon 2021; de Chaisemartin
& D'Haultfœuille 2020).

This module estimates **group-time average effects** (Callaway & Sant'Anna 2021):
for each adoption cohort ``g`` (the first period its units are treated) and each
period ``t``, the change in the outcome since period ``g − 1`` among cohort ``g``,
minus the same change among units that are **never treated**::

    ATT(g, t) = E[Y_t − Y_{g−1} | G = g] − E[Y_t − Y_{g−1} | never treated]

Those are aggregated into an overall ATT (post-adoption cells weighted by cohort
size) and an event study (by periods since adoption; pre-adoption event times are
placebos that should be zero under parallel trends).

**Inference.** Every estimate here is a smooth function of per-unit means, so each
has an influence function with one value per *unit*. Standard errors come from
those, which clusters by unit by construction -- a unit's periods are not
independent observations -- and the aggregates' influence functions include the
uncertainty of the cohort-share weights.

**What identifies it.** Parallel trends (absent treatment, each cohort's outcome
would have moved like the never-treated units') and no anticipation (a unit's
outcome does not respond before it is treated). Neither can be drawn in a causal
graph, so the certificate is tier C. The pre-trends test is a falsification test:
a failure makes the result ``contested``; a pass is weak evidence (Roth 2022) and
does not make the assumption true.

Refused, not approximated: unbalanced panels, treatments that switch off, no
never-treated units, no cohort with a pre-period, cohorts too small to estimate.
Not offered in this version: covariates, not-yet-treated controls, anticipation
windows, continuous treatments, repeated cross-sections, segments.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import stats

from causal_core.canonical import data_digest, digest
from causal_core.claims import build_claims
from causal_core.contract import (
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    CausalQuery,
    CausalResult,
    DesignCheck,
    Diagnostics,
    EstimateRecord,
    EventTimeEffect,
    IdentificationCertificate,
    IdentificationStatus,
    Interval,
    ParallelTrendsSensitivity,
    Provenance,
    RefutationResult,
    ResultStatus,
    Sensitivity,
    Strategy,
    Tier,
    TrendSensitivityRow,
)

F = NDArray[np.float64]

ESTIMATOR = "did_group_time"
FAMILY = "difference_in_differences"

ASSUMPTIONS = (
    "parallel trends: absent treatment, each adoption cohort's mean outcome would have changed "
    "like the never-treated units'",
    "no anticipation: outcomes do not respond before a unit is first treated",
    "absorbing treatment: once treated, a unit stays treated",
    "no interference: one unit's treatment does not change another's outcome",
)

INTERVAL_METHOD = "normal; influence function per unit (clustered by unit)"


def certificate(graph_digest: str, engine: str) -> IdentificationCertificate:
    """The identification certificate a panel design earns: tier C, whatever the graph says."""
    return IdentificationCertificate(
        status=IdentificationStatus.identified,
        strategy=Strategy.difference_in_differences,
        tier=Tier.C,
        estimand=(
            "ATT = Σ_{g, t≥g} w_gt · ( E[Y_t − Y_{g−1} | G = g] − E[Y_t − Y_{g−1} | never "
            "treated] ),  w_gt ∝ P(G = g)"
        ),
        assumptions=ASSUMPTIONS,
        engine=engine,
        graph_digest=graph_digest,
    )


# ── the panel ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Panel:
    """A balanced panel as arrays: units x periods."""

    y: F  # N x T outcomes
    cohort: NDArray[np.int64]  # first treated period index per unit; -1 = never treated
    periods: tuple[Any, ...]
    n_rows: int

    @property
    def n(self) -> int:
        return int(self.y.shape[0])

    @property
    def t(self) -> int:
        return int(self.y.shape[1])

    def cohorts(self) -> list[int]:
        return sorted(int(g) for g in np.unique(self.cohort) if g >= 0)


class PanelRefusal(Exception):
    def __init__(self, reason: AbstentionReason, detail: str, missing: tuple[str, ...] = ()):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.missing = missing


def build_panel(
    frame: pd.DataFrame, query: CausalQuery, policy: AnalysisPolicy
) -> tuple[Panel, list[DesignCheck], list[str]]:
    """Validate and pivot the long frame; raise PanelRefusal for what cannot be analysed."""
    unit, time, d_col, y_col = query.unit, query.time, query.treatment, query.outcome
    assert unit is not None and time is not None
    cols = [unit, time, d_col, y_col]
    sub = frame[cols]
    if sub.isna().any().any():
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            f"missing values in {[c for c in cols if sub[c].isna().any()]}; a panel design "
            "cannot silently drop unit-periods",
        )
    dvals = set(pd.unique(sub[d_col]))
    try:
        binary = {float(v) for v in dvals} <= {0.0, 1.0}
    except (TypeError, ValueError):
        binary = False
    if not binary:
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            f"treatment {d_col!r} must be binary (0/1) for difference-in-differences",
        )
    if sub.duplicated([unit, time]).any():
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            f"({unit}, {time}) pairs repeat: one row per unit-period",
        )
    periods = tuple(sorted(pd.unique(sub[time])))
    y = sub.pivot(index=unit, columns=time, values=y_col).reindex(columns=list(periods))
    d = sub.pivot(index=unit, columns=time, values=d_col).reindex(columns=list(periods))
    checks: list[DesignCheck] = []
    warnings: list[str] = []

    balanced = not y.isna().any().any()
    checks.append(DesignCheck(
        name="panel is balanced",
        passed=balanced,
        detail="" if balanced else f"{int(y.isna().sum().sum())} unit-periods are missing",
    ))
    if not balanced:
        raise PanelRefusal(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"the panel is unbalanced: {int(y.isna().sum().sum())} of {y.size} unit-periods "
            "are missing. This version needs every unit observed in every period.",
            ("a balanced panel, or restrict to units observed throughout",),
        )
    dm = d.to_numpy(dtype=float)
    reversals = int((np.diff(dm, axis=1) < 0).any(axis=1).sum())
    checks.append(DesignCheck(
        name="treatment is absorbing",
        passed=reversals == 0,
        detail="" if reversals == 0 else f"{reversals} units stop being treated",
    ))
    if reversals:
        raise PanelRefusal(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"{reversals} units stop being treated after starting; this design needs a "
            "treatment that, once on, stays on",
        )

    treated_any = dm.max(axis=1) > 0
    cohort = np.where(treated_any, dm.argmax(axis=1), -1).astype(np.int64)
    ym = y.to_numpy(dtype=float)
    from_start = cohort == 0
    if from_start.any():
        warnings.append(
            f"{int(from_start.sum())} units are treated from the first period, so they have no "
            "pre-period to difference from; they are excluded"
        )
        ym, cohort = ym[~from_start], cohort[~from_start]
    n_never = int((cohort < 0).sum())
    treated_cohorts = sorted(int(g) for g in np.unique(cohort) if g >= 0)
    checks.append(DesignCheck(
        name="some treated cohort has a pre-period",
        passed=bool(treated_cohorts),
    ))
    if not treated_cohorts:
        raise PanelRefusal(
            AbstentionReason.DESIGN_CHECK_FAILED,
            "no treated unit has a period before its treatment began: there is no change to "
            "compare",
        )
    checks.append(DesignCheck(
        name="never-treated units exist",
        passed=n_never > 0,
        detail=f"{n_never} never-treated units",
    ))
    if n_never == 0:
        raise PanelRefusal(
            AbstentionReason.DESIGN_CHECK_FAILED,
            "every unit is eventually treated, so there is no never-treated comparison group "
            "(not-yet-treated comparisons are not offered in this version)",
            ("a group of units that never receives the treatment",),
        )
    sizes = {g: int((cohort == g).sum()) for g in treated_cohorts}
    small = {periods[g]: k for g, k in sizes.items() if k < policy.min_per_arm}
    if small or n_never < policy.min_per_arm:
        detail = []
        if small:
            detail.append(f"adoption cohorts with fewer than {policy.min_per_arm} units: {small}")
        if n_never < policy.min_per_arm:
            detail.append(f"only {n_never} never-treated units")
        raise PanelRefusal(AbstentionReason.LOW_N, "; ".join(detail))
    if ym.shape[0] < policy.min_n:
        raise PanelRefusal(
            AbstentionReason.LOW_N, f"{ym.shape[0]} units, fewer than min_n={policy.min_n}"
        )
    checks.append(DesignCheck(
        name="every cohort is large enough",
        passed=True,
        detail=", ".join(f"{periods[g]}: {k}" for g, k in sizes.items()) + f", never: {n_never}",
    ))
    return Panel(y=ym, cohort=cohort, periods=periods, n_rows=len(sub)), checks, warnings


# ── group-time effects and their influence functions ──────────────────────────


@dataclass(frozen=True)
class Cell:
    g: int  # cohort (first treated period index)
    t: int  # period index
    att: float
    psi: F  # influence function, one value per unit (length N)

    @property
    def event_time(self) -> int:
        return self.t - self.g


def controls(panel: Panel, g: int, t: int) -> NDArray[np.bool_]:
    """The comparison units for cohort g at period t: the never-treated.

    A seam for the seeded defects: using units already treated by period t as controls
    is the "forbidden comparison" that biases two-way fixed effects.
    """
    return np.asarray(panel.cohort < 0)


def group_time(panel: Panel) -> list[Cell]:
    """ATT(g, t) for every cohort and every period except the base period g − 1."""
    cells: list[Cell] = []
    n = panel.n
    for g in panel.cohorts():
        base = g - 1
        treated = panel.cohort == g
        p_g = treated.mean()
        for t in range(panel.t):
            if t == base:
                continue
            ctrl = controls(panel, g, t)
            p_c = ctrl.mean()
            dy = panel.y[:, t] - panel.y[:, base]
            mu_g = dy[treated].mean()
            mu_c = dy[ctrl].mean()
            psi = np.zeros(n)
            psi[treated] += (dy[treated] - mu_g) / p_g
            psi[ctrl] -= (dy[ctrl] - mu_c) / p_c
            cells.append(Cell(g=g, t=t, att=float(mu_g - mu_c), psi=psi))
    return cells


@dataclass(frozen=True)
class Aggregate:
    point: float
    psi: F

    def se(self) -> float:
        return float(np.sqrt(np.mean(self.psi**2) / self.psi.shape[0]))


def aggregate(panel: Panel, cells: list[Cell]) -> Aggregate:
    """Cohort-size weighted mean of cells, with the weights' own estimation in the IF.

    θ = Σ_k π_{g_k} a_k / Σ_k π_{g_k}. Its influence function is Σ_k w_k ψ_k (the cells'
    uncertainty) plus Σ_k (a_k − θ)(1[G = g_k] − π_{g_k}) / Σ_k π_{g_k} (the shares').
    """
    pi = {g: float((panel.cohort == g).mean()) for g in {c.g for c in cells}}
    s = sum(pi[c.g] for c in cells)
    theta = sum(pi[c.g] * c.att for c in cells) / s
    psi = np.zeros(panel.n)
    for c in cells:
        psi += (pi[c.g] / s) * c.psi
        psi += (c.att - theta) * ((panel.cohort == c.g).astype(float) - pi[c.g]) / s
    return Aggregate(point=float(theta), psi=psi)


def overall_effect(panel: Panel, post: list[Cell]) -> Aggregate:
    """The overall ATT: every post-adoption cell, weighted by cohort size.

    A seam for the seeded defects: the `twfe_static` defect replaces this with the
    two-way fixed-effects coefficient, the estimator this module exists to avoid.
    """
    return aggregate(panel, post)


def aggregate_se(agg: Aggregate, panel: Panel, cells: list[Cell]) -> float:
    """The aggregate's standard error. A seam: the `cells_independent` defect lives here."""
    return agg.se()


def pre_trends(panel: Panel, pre: dict[int, Aggregate], alpha: float) -> RefutationResult | None:
    """Joint Wald test that every pre-adoption event-time effect is zero."""
    if not pre:
        return None
    theta = np.array([pre[e].point for e in sorted(pre)])
    psi = np.column_stack([pre[e].psi for e in sorted(pre)])
    cov = psi.T @ psi / panel.n**2
    stat = float(theta @ np.linalg.pinv(cov) @ theta)
    df = len(theta)
    p = float(stats.chi2.sf(stat, df))
    return RefutationResult(
        name="pre_trends",
        passed=p > alpha,
        statistic=stat,
        p_value=p,
        threshold=alpha,
        detail=f"joint Wald test of {df} pre-adoption event-time effects; a pass is weak "
        "evidence for parallel trends, not proof of them",
    )


# ── sensitivity to parallel-trends violations ─────────────────────────────────

SENSITIVITY_METHOD = (
    "relative magnitudes on event-time deviations (after Rambachan & Roth 2023): post-adoption "
    "bias <= M x the largest pre-adoption deviation; union bound over an upper confidence bound "
    "on the pre-period deviations and the estimate's interval"
)


def pre_bound(panel: Panel, pre: dict[int, Aggregate], alpha: float) -> float:
    """An upper confidence bound on max_e |pre-adoption effect|, Bonferroni across the periods.

    A seam for the seeded defects: `last_pre_period_only` bounds it from e = -2 alone.
    """
    z = float(stats.norm.ppf(1 - alpha / (2 * len(pre))))
    return max(abs(a.point) + z * a.se() for a in pre.values())


def trend_sensitivity(
    point: float, se: float, panel: Panel, pre: dict[int, Aggregate], policy: AnalysisPolicy
) -> ParallelTrendsSensitivity | None:
    if not pre:
        return None
    u = pre_bound(panel, pre, policy.pt_alpha_pre)
    z = float(stats.norm.ppf(1 - policy.pt_alpha_effect / 2))
    rows = tuple(
        TrendSensitivityRow(m=m, lo=point - (m * u + z * se), hi=point + (m * u + z * se))
        for m in policy.pt_sensitivity_grid
    )
    margin = abs(point) - z * se
    return ParallelTrendsSensitivity(
        method=SENSITIVITY_METHOD,
        alpha_pre=policy.pt_alpha_pre,
        alpha_effect=policy.pt_alpha_effect,
        pre_bound=u,
        rows=rows,
        breakdown=margin / u if margin > 0 and u > 0 else None,
    )


# ── the analysis ──────────────────────────────────────────────────────────────


def _record(point: float, se: float, n: int, level: float, estimand: str) -> EstimateRecord:
    z = float(stats.norm.ppf(0.5 + level / 2))
    return EstimateRecord(
        estimator=ESTIMATOR,
        family=FAMILY,
        estimand=estimand,
        point=point,
        se=se,
        ci=Interval(level=level, lo=point - z * se, hi=point + z * se, method=INTERVAL_METHOD),
        n_used=n,
        assumptions=ASSUMPTIONS,
    )


def analyze_panel(
    frame: pd.DataFrame,
    query: CausalQuery,
    policy: AnalysisPolicy,
    *,
    cert: IdentificationCertificate,
    unreviewed: list[str],
    warnings: list[str],
    prov: dict[str, Any],
    refuse: Callable[..., Abstention],
) -> CausalResult | Abstention:
    unsupported = {
        "adjust_for": query.adjust_for,
        "instruments": query.instruments,
        "treatment_proxies": query.treatment_proxies,
        "outcome_proxies": query.outcome_proxies,
        "negative_control_outcomes": query.negative_control_outcomes,
        "segment_by": query.segment_by,
        "treatment_levels": query.treatment_levels,
    }
    given = sorted(k for k, v in unsupported.items() if v)
    if given:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            f"not supported with difference_in_differences in this version: {given}",
            q=query,
        )
    missing_cols = [c for c in (query.unit, query.time) if c is None or c not in frame.columns]
    if query.unit is None or query.time is None or missing_cols:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            "difference_in_differences needs `unit` and `time`, naming columns of the data"
            + (f"; not found: {missing_cols}" if missing_cols else ""),
            ("the unit identifier and period columns",),
            q=query,
        )
    if query.estimand != "att":
        return refuse(
            AbstentionReason.INVALID_INPUT,
            "difference-in-differences identifies the effect on the treated; ask for "
            "estimand='att' (the average effect over everyone is not identified by this design)",
            q=query,
        )

    prov["data_digest"] = data_digest(frame[[query.unit, query.time, query.treatment,
                                             query.outcome]])
    try:
        panel, checks, notes = build_panel(frame, query, policy)
    except PanelRefusal as r:
        return refuse(r.reason, r.detail, r.missing, q=query, cert=cert)
    warnings = [*warnings, *notes]

    cells = group_time(panel)
    post = [c for c in cells if c.event_time >= 0]
    overall = overall_effect(panel, post)
    se = aggregate_se(overall, panel, post)
    level = policy.ci_level
    rec = _record(overall.point, se, panel.n, level, "att")

    events: list[EventTimeEffect] = []
    pre: dict[int, Aggregate] = {}
    for e in sorted({c.event_time for c in cells}):
        group = [c for c in cells if c.event_time == e]
        agg = aggregate(panel, group)
        if e < 0:
            pre[e] = agg
        events.append(EventTimeEffect(
            event_time=e,
            estimate=_record(agg.point, aggregate_se(agg, panel, group), panel.n, level,
                             f"att_event_{e}"),
            cohorts=len(group),
        ))
    refutations: list[RefutationResult] = []
    test = pre_trends(panel, pre, policy.refutation_alpha)
    if test is None:
        warnings.append("no pre-adoption period beyond the base: parallel trends cannot be tested")
    else:
        refutations.append(test)

    if refutations and not refutations[0].passed:
        status = ResultStatus.contested
        warnings.append("falsification tests failed: ['pre_trends']")
    elif unreviewed:
        status = ResultStatus.provisional
        warnings.append(f"graph contains unreviewed edges: {unreviewed}")
    else:
        status = ResultStatus.supported

    trends = trend_sensitivity(overall.point, se, panel, pre, policy)
    if trends is None:
        warnings.append("no pre-adoption period beyond the base: sensitivity to parallel-trends "
                        "violations cannot be bounded")
    treated_units = int((panel.cohort >= 0).sum())
    diagnostics = Diagnostics(
        n_rows=panel.n_rows,
        n_used=panel.n * panel.t,
        treatment_kind="binary",
        n_treated=treated_units,
        n_control=panel.n - treated_units,
        outcome_sd=float(np.std(panel.y, ddof=1)),
        checks=tuple(checks),
    )
    result = CausalResult(
        status=status,
        query=query,
        certificate=cert,
        primary=rec,
        estimates=(rec,),
        diagnostics=diagnostics,
        refutations=tuple(refutations),
        event_study=tuple(events),
        sensitivity=None if trends is None else Sensitivity(parallel_trends=trends),
        claims=build_claims({
            "effect": (rec.point, "/primary/point", None),
            "effect.se": (rec.se, "/primary/se", None),
            "effect.ci.lo": (rec.ci.lo if rec.ci else None, "/primary/ci/lo", None),
            "effect.ci.hi": (rec.ci.hi if rec.ci else None, "/primary/ci/hi", None),
            "ci.level": (level * 100, "/primary/ci/level", "%"),
            "n": (float(panel.n), "/primary/n_used", None),
            "pt.breakdown": (
                trends.breakdown if trends is not None else None,
                "/sensitivity/parallel_trends/breakdown",
                None,
            ),
        }),
        warnings=tuple(warnings),
        provenance=Provenance(**prov),
    )
    return result.model_copy(
        update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))}
    )

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
minus the same change among the comparison units ``C``::

    ATT(g, t) = E[Y_t − Y_{g−1} | G = g] − E[Y_t − Y_{g−1} | C]

The comparison is the units **never treated** (``control_group="never_treated"``,
the default) or those plus every other cohort **not yet treated** at ``t`` and at
the base period (``"not_yet_treated"``), which is what a rollout that eventually
reaches everyone leaves. A cell with too few comparison units is not estimated.

With **covariates** -- time-invariant, pre-treatment columns under which parallel
trends is assumed to hold -- each cell is instead the doubly robust DiD of
Sant'Anna & Zhao (2020): a logistic propensity model and a linear outcome model
for the comparison units' change, combined so that the cell is consistent if
either model is right::

    ATT(g, t) = E[w₁ (ΔY − μ₀(X))] − E[w₀ (ΔY − μ₀(X))],
    w₁ ∝ 1[G = g],  w₀ ∝ p(X)·1[C] / (1 − p(X))

Those are aggregated into an overall ATT (post-adoption cells weighted by cohort
size) and an event study (by periods since adoption; pre-adoption event times are
placebos that should be zero under parallel trends).

**Inference.** Every estimate here is a smooth function of per-unit means, so each
has an influence function with one value per *unit*. Standard errors come from
those, which clusters by unit by construction -- a unit's periods are not
independent observations -- and the aggregates' influence functions include the
uncertainty of the cohort-share weights. The doubly robust cells' influence
functions include the estimation effects of both nuisance models.

**What identifies it.** Parallel trends (absent treatment, each cohort's outcome
would have moved like the never-treated units') and no anticipation (a unit's
outcome does not respond before it is treated). Neither can be drawn in a causal
graph, so the certificate is tier C. The pre-trends test is a falsification test:
a failure makes the result ``contested``; a pass is weak evidence (Roth 2022) and
does not make the assumption true.

Refused, not approximated: unbalanced panels, treatments that switch off, no
comparison units, no cohort with a pre-period, cohorts too small to estimate,
covariates that vary within a unit or are not numeric, and poor overlap (a cohort
whose fitted propensity is near one for more than a small share of its units).
Not offered in this version: time-varying covariates, anticipation windows,
continuous treatments, repeated cross-sections, segments.
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
    PropensityDiagnostics,
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
ESTIMATOR_DR = "did_group_time_dr"
FAMILY = "difference_in_differences"

ASSUMPTIONS = (
    "parallel trends: absent treatment, each adoption cohort's mean outcome would have changed "
    "like the never-treated units'",
    "no anticipation: outcomes do not respond before a unit is first treated",
    "absorbing treatment: once treated, a unit stays treated",
    "no interference: one unit's treatment does not change another's outcome",
)

INTERVAL_METHOD = "normal; influence function per unit (clustered by unit)"

NOT_YET_TREATED = "not_yet_treated"


def assumptions(query: CausalQuery | None = None) -> tuple[str, ...]:
    """What the estimate rests on, for the options the query chose."""
    if query is None or (not query.covariates and query.control_group != NOT_YET_TREATED):
        return ASSUMPTIONS
    comparison = ("the never-treated units'" if query.control_group != NOT_YET_TREATED
                  else "the units not yet treated at that period's")
    pt = ("conditional parallel trends: absent treatment, among units with the same "
          f"covariates ({', '.join(query.covariates)}), each adoption cohort's mean outcome would "
          f"have changed like {comparison}" if query.covariates else
          f"parallel trends: absent treatment, each adoption cohort's mean outcome would have "
          f"changed like {comparison}")
    extra = ((
        "overlap: for every covariate value found in a cohort, comparison units with that value "
        "exist (the propensity of adoption stays below one)",
        "the covariates are fixed characteristics, not affected by the treatment",
    ) if query.covariates else ())
    anticipation = ("no anticipation: outcomes do not respond before a unit is first treated, "
                    "including units serving as comparisons before their own adoption"
                    if query.control_group == NOT_YET_TREATED else ASSUMPTIONS[1])
    return (pt, anticipation, *extra, *ASSUMPTIONS[2:])


def certificate(
    graph_digest: str,
    engine: str,
    query: CausalQuery | None = None,
    unreviewed: tuple[str, ...] = (),
    unreviewed_llm: tuple[str, ...] = (),
) -> IdentificationCertificate:
    """The identification certificate a panel design earns: tier C, whatever the graph says.

    The graph does not identify a panel effect, but its review state still travels with
    the certificate: a decision reads which edges nobody has reviewed from here.
    """
    nyt = query is not None and query.control_group == NOT_YET_TREATED
    comparison = "C_gt" if nyt else "never treated"
    if query is not None and query.covariates:
        cell = (f"E[Y_t − Y_{{g−1}} | G = g] − E[ E[Y_t − Y_{{g−1}} | X, {comparison}] | G = g ]"
                f",  X = ({', '.join(query.covariates)})")
    else:
        cell = f"E[Y_t − Y_{{g−1}} | G = g] − E[Y_t − Y_{{g−1}} | {comparison}]"
    where = ",  C_gt = never treated or first treated after max(t, g−1)" if nyt else ""
    return IdentificationCertificate(
        status=IdentificationStatus.identified,
        strategy=Strategy.difference_in_differences,
        tier=Tier.C,
        estimand=f"ATT = Σ_{{g, t≥g}} w_gt · ( {cell} ),  w_gt ∝ P(G = g){where}",
        assumptions=assumptions(query),
        unreviewed_edges=unreviewed,
        unreviewed_llm_edges=unreviewed_llm,
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
    x: F | None = None  # N x k time-invariant covariates, or None
    control_group: str = "never_treated"

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
    covs = list(query.covariates)
    clash = sorted(set(covs) & {unit, time, d_col, y_col})
    absent = [c for c in covs if c not in frame.columns]
    if clash or absent or len(set(covs)) < len(covs):
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            (f"covariates not found in the data: {absent}" if absent else
             f"covariates repeat, or name the unit, time, treatment or outcome column: "
             f"{clash or covs}"),
        )
    cols = [unit, time, d_col, y_col, *covs]
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
    not_numeric = [c for c in covs if not pd.api.types.is_numeric_dtype(sub[c])]
    if not_numeric:
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            f"covariates must be numeric: {not_numeric}; encode a category as indicator columns",
            ("numeric covariates (0/1 indicators for categories)",),
        )
    varying = [c for c in covs if (sub.groupby(unit)[c].nunique() > 1).any()]
    if varying:
        raise PanelRefusal(
            AbstentionReason.INVALID_INPUT,
            f"covariates vary within a unit over time: {varying}. This version supports "
            "time-invariant, pre-treatment characteristics only; a covariate that changes after "
            "adoption may itself be affected by the treatment",
            ("each unit's covariate value from before any unit was treated, repeated on every "
             "row",),
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
    xm: F | None = None
    if covs:
        xm = sub.groupby(unit)[covs].first().reindex(y.index).to_numpy(dtype=float)
    from_start = cohort == 0
    if from_start.any():
        warnings.append(
            f"{int(from_start.sum())} units are treated from the first period, so they have no "
            "pre-period to difference from; they are excluded"
        )
        ym, cohort = ym[~from_start], cohort[~from_start]
        xm = None if xm is None else xm[~from_start]
    if xm is not None:
        design_x = np.column_stack([np.ones(xm.shape[0]), xm])
        if np.linalg.matrix_rank(design_x) < design_x.shape[1]:
            raise PanelRefusal(
                AbstentionReason.INVALID_INPUT,
                f"the covariates {covs} are constant or collinear across units",
            )
    nyt = query.control_group == NOT_YET_TREATED
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
    if not nyt:
        checks.append(DesignCheck(
            name="never-treated units exist",
            passed=n_never > 0,
            detail=f"{n_never} never-treated units",
        ))
        if n_never == 0:
            raise PanelRefusal(
                AbstentionReason.DESIGN_CHECK_FAILED,
                "every unit is eventually treated, so there is no never-treated comparison "
                "group; control_group='not_yet_treated' compares each cohort with the units not "
                "yet treated instead",
                ("a group of units that never receives the treatment, or "
                 "control_group='not_yet_treated'",),
            )
    sizes = {g: int((cohort == g).sum()) for g in treated_cohorts}
    small = {periods[g]: k for g, k in sizes.items() if k < policy.min_per_arm}
    few_never = not nyt and n_never < policy.min_per_arm
    if small or few_never:
        detail = []
        if small:
            detail.append(f"adoption cohorts with fewer than {policy.min_per_arm} units: {small}")
        if few_never:
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
    if covs:
        checks.append(DesignCheck(name="covariates are constant within unit", passed=True,
                                  detail=", ".join(covs)))
    panel = Panel(y=ym, cohort=cohort, periods=periods, n_rows=len(sub), x=xm,
                  control_group=query.control_group)
    return panel, checks, warnings


# ── group-time effects and their influence functions ──────────────────────────


@dataclass(frozen=True)
class Cell:
    g: int  # cohort (first treated period index)
    t: int  # period index
    att: float
    psi: F  # influence function, one value per unit (length N)
    overlap: CellOverlap | None = None  # doubly robust cells only

    @property
    def event_time(self) -> int:
        return self.t - self.g


@dataclass(frozen=True)
class CellOverlap:
    """Propensity diagnostics of one doubly robust cell."""

    p_min: float
    p_max: float
    share_treated_above: float  # share of the cohort's units above the upper bound
    ess_control: float  # effective number of comparison units under the weights
    n_treated: int


def controls(panel: Panel, g: int, t: int) -> NDArray[np.bool_]:
    """The comparison units for cohort g at period t.

    Never-treated: units never treated. Not-yet-treated: those plus every other cohort
    first treated after both t and the base period g − 1, so untreated at both.

    A seam for the seeded defects: using units already treated by period t as controls
    is the "forbidden comparison" that biases two-way fixed effects, and a not-yet-treated
    set that leaks such units is the same mistake by another route.
    """
    never = panel.cohort < 0
    if panel.control_group != NOT_YET_TREATED:
        return np.asarray(never)
    return np.asarray(never | ((panel.cohort > max(t, g - 1)) & (panel.cohort != g)))


def covariate_matrix(panel: Panel) -> F | None:
    """The covariates the cells adjust for. A seam: `ignore_covariates` returns None."""
    return panel.x


def _logit(X: F, d: F, max_iter: int = 100) -> F:
    """Logistic regression by maximum likelihood (Newton, with step-halving)."""
    beta = np.zeros(X.shape[1])

    def loglik(b: F) -> float:
        eta = np.clip(X @ b, -35.0, 35.0)
        return float(np.sum(d * eta - np.logaddexp(0.0, eta)))

    ll = loglik(beta)
    for _ in range(max_iter):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ beta, -35.0, 35.0)))
        step = np.linalg.pinv(X.T @ (X * (p * (1 - p))[:, None])) @ (X.T @ (d - p))
        size = 1.0
        while size > 1e-8:
            cand = beta + size * step
            ll_new = loglik(cand)
            if ll_new >= ll - 1e-12:
                break
            size /= 2
        beta, converged = cand, abs(ll_new - ll) < 1e-10
        ll = ll_new
        if converged:
            break
    return beta


def propensity(X: F, d: F) -> tuple[F, F]:
    """Fitted P(G = g | X) among the cell's units, and its asymptotically linear representation.

    A seam for the seeded defects: `propensity_ignored` makes it a constant.
    """
    p = 1.0 / (1.0 + np.exp(-np.clip(X @ _logit(X, d), -35.0, 35.0)))
    info = X.T @ (X * (p * (1 - p))[:, None]) / X.shape[0]
    return p, ((d - p)[:, None] * X) @ np.linalg.pinv(info)


def outcome_regression(X: F, dy: F, d: F) -> tuple[F, F]:
    """E[ΔY | X] fitted on the comparison units, and its asymptotically linear representation.

    A seam for the seeded defects: `outcome_regression_ignored` makes it zero.
    """
    c = 1.0 - d
    beta = np.linalg.pinv(X.T @ (X * c[:, None])) @ (X.T @ (c * dy))
    mu = X @ beta
    info = X.T @ (X * c[:, None]) / X.shape[0]
    return mu, ((c * (dy - mu))[:, None] * X) @ np.linalg.pinv(info)


def dr_cell(X: F, d: F, dy: F, upper: float) -> tuple[float, F, CellOverlap]:
    """Doubly robust DiD on one cell's units (Sant'Anna & Zhao 2020, traditional panel form).

    Returns the estimate, its influence function on these units, and overlap diagnostics.
    """
    p, lin_ps = propensity(X, d)
    mu, lin_or = outcome_regression(X, dy, d)
    pc = np.minimum(p, 1 - 1e-6)
    w1 = d
    w0 = pc * (1 - d) / (1 - pc)
    r = dy - mu
    eta1 = float(np.mean(w1 * r) / np.mean(w1))
    eta0 = float(np.mean(w0 * r) / np.mean(w0))
    m1 = np.mean(w1[:, None] * X, axis=0)
    m2 = np.mean((w0 * (r - eta0))[:, None] * X, axis=0)
    m3 = np.mean(w0[:, None] * X, axis=0)
    inf_treat = (w1 * r - w1 * eta1 - lin_or @ m1) / np.mean(w1)
    inf_cont = (w0 * r - w0 * eta0 + lin_ps @ m2 - lin_or @ m3) / np.mean(w0)
    treated = d > 0
    ov = CellOverlap(
        p_min=float(p.min()),
        p_max=float(p.max()),
        share_treated_above=float(np.mean(p[treated] > upper)),
        ess_control=float(w0.sum() ** 2 / np.sum(w0**2)) if np.any(w0 > 0) else 0.0,
        n_treated=int(treated.sum()),
    )
    return eta1 - eta0, inf_treat - inf_cont, ov


def group_time(
    panel: Panel, min_controls: int = 1, upper: float = 0.99
) -> tuple[list[Cell], list[tuple[int, int]]]:
    """ATT(g, t) for every cohort and every period except the base period g − 1.

    Returns the cells and the (g, t) pairs skipped for want of `min_controls` comparison units.
    """
    cells: list[Cell] = []
    skipped: list[tuple[int, int]] = []
    n = panel.n
    x = covariate_matrix(panel)
    for g in panel.cohorts():
        base = g - 1
        treated = panel.cohort == g
        p_g = treated.mean()
        for t in range(panel.t):
            if t == base:
                continue
            ctrl = controls(panel, g, t)
            if int(ctrl.sum()) < min_controls:
                skipped.append((g, t))
                continue
            dy = panel.y[:, t] - panel.y[:, base]
            psi = np.zeros(n)
            if x is None:
                p_c = ctrl.mean()
                mu_g = dy[treated].mean()
                mu_c = dy[ctrl].mean()
                psi[treated] += (dy[treated] - mu_g) / p_g
                psi[ctrl] -= (dy[ctrl] - mu_c) / p_c
                cells.append(Cell(g=g, t=t, att=float(mu_g - mu_c), psi=psi))
                continue
            sub = treated | ctrl
            X = np.column_stack([np.ones(int(sub.sum())), x[sub]])
            att, psi_sub, ov = dr_cell(X, treated[sub].astype(float), dy[sub], upper)
            psi[sub] = psi_sub * (n / sub.sum())
            cells.append(Cell(g=g, t=t, att=float(att), psi=psi, overlap=ov))
    return cells, skipped


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


def _record(point: float, se: float, n: int, level: float, estimand: str,
            query: CausalQuery | None = None) -> EstimateRecord:
    z = float(stats.norm.ppf(0.5 + level / 2))
    details: dict[str, float | str] = {}
    if query is not None:
        details["control_group"] = query.control_group
        if query.covariates:
            details["covariates"] = ", ".join(query.covariates)
    return EstimateRecord(
        estimator=ESTIMATOR_DR if query is not None and query.covariates else ESTIMATOR,
        family=FAMILY,
        estimand=estimand,
        point=point,
        se=se,
        ci=Interval(level=level, lo=point - z * se, hi=point + z * se, method=INTERVAL_METHOD),
        n_used=n,
        assumptions=assumptions(query),
        details=details,
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

    covs = [c for c in query.covariates if c in frame.columns]
    prov["data_digest"] = data_digest(frame[[query.unit, query.time, query.treatment,
                                             query.outcome, *covs]])
    try:
        panel, checks, notes = build_panel(frame, query, policy)
    except PanelRefusal as r:
        return refuse(r.reason, r.detail, r.missing, q=query, cert=cert)
    warnings = [*warnings, *notes]

    cells, skipped = group_time(panel, policy.min_per_arm, policy.propensity_bounds[1])
    post = [c for c in cells if c.event_time >= 0]
    checks.append(DesignCheck(
        name="comparison units exist for some post-adoption cell",
        passed=bool(post),
        detail=f"{len(post)} post-adoption cells estimated",
    ))
    if not post:
        return refuse(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"no post-adoption cohort-period has {policy.min_per_arm} or more comparison units "
            f"({query.control_group.replace('_', '-')}): there is nothing to compare a treated "
            "cohort with after it adopts",
            ("units that are never treated, or that adopt later than the others",),
            q=query,
            cert=cert,
        )
    if skipped:
        by_g: dict[int, list[Any]] = {}
        for g, t in skipped:
            by_g.setdefault(g, []).append(panel.periods[t])
        warnings.append(
            f"cohort-periods with fewer than {policy.min_per_arm} comparison units are not "
            "estimated, and the overall effect covers the rest: "
            + "; ".join(f"cohort {panel.periods[g]} at {ts}" for g, ts in by_g.items())
        )
        idle = sorted(set(panel.cohorts()) - {c.g for c in post})
        if idle:
            warnings.append(
                f"cohorts {[panel.periods[g] for g in idle]} have no estimable post-adoption "
                "period; they serve only as comparisons"
            )
    dr = [c.overlap for c in cells if c.overlap is not None]
    propensity_diag: PropensityDiagnostics | None = None
    if dr:
        worst = max(cells, key=lambda c: c.overlap.share_treated_above if c.overlap else -1.0)
        assert worst.overlap is not None
        share = worst.overlap.share_treated_above
        ok = share <= policy.max_extreme_propensity_share
        checks.append(DesignCheck(
            name="overlap: fitted propensities of adoption stay below the upper bound",
            passed=ok,
            detail=f"largest share of a cohort above {policy.propensity_bounds[1]}: {share:.3f} "
            f"(cohort {panel.periods[worst.g]}, period {panel.periods[worst.t]})",
        ))
        propensity_diag = PropensityDiagnostics(
            min=min(o.p_min for o in dr),
            max=max(o.p_max for o in dr),
            share_outside_bounds=share,
            ess_treated=float(min(o.n_treated for o in dr)),
            ess_control=min(o.ess_control for o in dr),
        )
        if not ok:
            return refuse(
                AbstentionReason.POSITIVITY,
                f"poor overlap: {share:.1%} of cohort {panel.periods[worst.g]}'s units have a "
                f"fitted propensity of adoption above {policy.propensity_bounds[1]} (period "
                f"{panel.periods[worst.t]}), more than max_extreme_propensity_share="
                f"{policy.max_extreme_propensity_share}. For them no comparable untreated unit "
                "exists, and the weights would rest on a handful of comparisons",
                ("comparison units that resemble the adopters on the covariates, or fewer "
                 "covariates",),
                q=query,
                cert=cert,
            )
    overall = overall_effect(panel, post)
    se = aggregate_se(overall, panel, post)
    level = policy.ci_level
    rec = _record(overall.point, se, panel.n, level, "att", query)

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
                             f"att_event_{e}", query),
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
        propensity=propensity_diag,
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

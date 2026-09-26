"""
Sensitivity to unmeasured confounding: how strong would a hidden confounder
have to be to explain the estimate away?

* E-value (VanderWeele & Ding 2017): the minimum risk-ratio association an
  unmeasured confounder would need with both treatment and outcome. For a binary
  outcome it is computed from the estimated risk ratio; otherwise from the
  standardized effect via RR ≈ exp(0.91 × d), an approximation the method's
  authors propose for continuous outcomes, and labelled as such.
* Robustness value (Cinelli & Hazlett 2020): the share of residual variance of
  both treatment and outcome a confounder would need to explain to reduce the
  OLS benchmark estimate to zero (RV) or to statistical insignificance (RV_α).
* Rosenbaum bound (Rosenbaum 2002, §4.3.3): for a back-door design with a binary
  treatment, a 1:1 matched design on the propensity-score logit, and the largest
  Γ -- the factor by which two units with the same covariates may differ in their
  odds of treatment -- at which Wilcoxon's signed-rank test of no effect still
  rejects. Pre-registered as T9 (benchmarks/t9-rosenbaum/).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from causal_core.contract import (
    AnalysisPolicy,
    EstimateRecord,
    RosenbaumSensitivity,
    Sensitivity,
    Strategy,
)
from causal_core.estimators import F, Fit, Prepared
from causal_core.estimators._linalg import add_intercept, logistic

#: The Γ values at which the upper-bound p-value is tabulated beside Γ*.
ROSENBAUM_GRID: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0)


def e_value_rr(rr: float) -> float:
    rr = rr if rr >= 1 else 1 / rr
    return rr + math.sqrt(rr * (rr - 1))


def _e_value_interval(point_rr: float, lo_rr: float, hi_rr: float) -> tuple[float, float]:
    ev = e_value_rr(point_rr)
    if lo_rr <= 1 <= hi_rr:
        return ev, 1.0
    bound = lo_rr if point_rr > 1 else hi_rr
    return ev, e_value_rr(bound)


def e_values(d: Prepared, primary: Fit, level: float) -> tuple[float, float, str] | None:
    rec: EstimateRecord = primary.record
    ex = primary.extras
    binary_outcome = bool(np.isin(np.unique(d.y), [0.0, 1.0]).all())
    if (
        binary_outcome
        and d.treatment_kind == "binary"
        and {"mu1", "mu0", "if_mu1", "if_mu0"} <= ex.keys()
    ):
        mu1, mu0 = float(ex["mu1"]), float(ex["mu0"])  # type: ignore[arg-type]
        if mu1 > 0 and mu0 > 0:
            inf = np.asarray(ex["if_mu1"]) / mu1 - np.asarray(ex["if_mu0"]) / mu0
            se_log = float(np.sqrt(np.sum(inf**2)) / len(inf))
            z = float(stats.norm.ppf(0.5 + level / 2))
            rr = mu1 / mu0
            lo, hi = rr * math.exp(-z * se_log), rr * math.exp(z * se_log)
            ev, ev_ci = _e_value_interval(rr, lo, hi)
            return ev, ev_ci, "risk ratio from the primary estimator's arm means"
    sd_y = float(np.std(d.y, ddof=1))
    if sd_y <= 0 or rec.ci is None:
        return None

    def to_rr(effect: float) -> float:
        return math.exp(0.91 * effect / sd_y)

    ev, ev_ci = _e_value_interval(to_rr(rec.point), to_rr(rec.ci.lo), to_rr(rec.ci.hi))
    return ev, ev_ci, "approximate: RR = exp(0.91 × effect / sd(outcome)) (VanderWeele & Ding 2017)"


def robustness_value(d: Prepared, level: float) -> tuple[float, float, float] | None:
    """(partial R² of treatment, RV, RV_α) from the OLS of Y on T and covariates."""
    D = add_intercept(np.column_stack([d.t, d.X]) if d.X.size else d.t[:, None])
    n, k = D.shape
    dof = n - k
    if dof <= 1:
        return None
    beta, *_ = np.linalg.lstsq(D, d.y, rcond=None)
    resid = d.y - D @ beta
    sigma2 = float(resid @ resid / dof)
    try:
        cov = sigma2 * np.linalg.inv(D.T @ D)
    except np.linalg.LinAlgError:
        return None
    se = float(np.sqrt(cov[1, 1]))
    if se <= 0:
        return None
    t_stat = float(beta[1] / se)
    r2 = t_stat**2 / (t_stat**2 + dof)
    f = abs(t_stat) / math.sqrt(dof)

    def rv(fq: float) -> float:
        if fq <= 0:
            return 0.0
        return 0.5 * (math.sqrt(fq**4 + 4 * fq**2) - fq**2)

    t_crit = float(stats.t.ppf(0.5 + level / 2, dof - 1))
    f_alpha = f - t_crit / math.sqrt(dof - 1)
    return r2, rv(f), rv(f_alpha)


# ── Rosenbaum bounds ─────────────────────────────────────────────────────────


def bound_probability(gamma: float) -> float:
    """π(Γ): the largest chance, under Rosenbaum's model with bias Γ, that the unit with the
    higher response in a pair is the treated one -- the worst case the upper bound assumes."""
    return gamma / (1.0 + gamma)


def one_sided_alpha(ci_level: float) -> float:
    """The level Γ* is computed at: the one-sided half of the primary interval's level."""
    return (1.0 - ci_level) / 2.0


def _signed_ranks(d: F) -> tuple[float, float, float]:
    """(T, Σ r, Σ r²) of Wilcoxon's signed-rank statistic, ties averaged.

    A zero difference is ranked with the others and then scores zero (Pratt's treatment, the
    one Rosenbaum's tables and DOS2::senWilcox use), so it raises the ranks above it.
    """
    d = np.asarray(d, dtype=float)
    if d.size == 0:
        return 0.0, 0.0, 0.0
    r = stats.rankdata(np.abs(d)) * (d != 0)
    return float(r[d > 0].sum()), float(r.sum()), float((r**2).sum())


def wilcoxon_upper_p(d: F, gamma: float) -> float:
    """Upper bound on the one-sided (greater) p-value of Wilcoxon's signed-rank test of no
    effect when the hidden bias is at most Γ; large-sample normal approximation."""
    t_stat, s1, s2 = _signed_ranks(d)
    if s1 == 0:
        return 1.0
    pi = bound_probability(gamma)
    return float(stats.norm.sf((t_stat - pi * s1) / math.sqrt(pi * (1 - pi) * s2)))


#: Γ* is reported up to this value; beyond it the statement is "at least" this.
GAMMA_CAP = 1e6


def sensitivity_value(d: F, alpha: float) -> float | None:
    """Γ*: the largest Γ ≥ 1 at which :func:`wilcoxon_upper_p` ≤ α, or None when the test
    does not reject even at Γ = 1.

    The bound is increasing in Γ (its standardized deviate decreases in π), so Γ* is found by
    bisection on log Γ, to a relative precision of 1e-10.
    """
    if wilcoxon_upper_p(d, 1.0) > alpha:
        return None
    lo, hi = 0.0, 1.0  # log Γ; p(lo) ≤ alpha
    while wilcoxon_upper_p(d, math.exp(hi)) <= alpha:
        lo, hi = hi, 2 * hi
        if hi > math.log(GAMMA_CAP):
            return GAMMA_CAP
    while hi - lo > 1e-10:
        mid = (lo + hi) / 2
        if wilcoxon_upper_p(d, math.exp(mid)) <= alpha:
            lo = mid
        else:
            hi = mid
    return math.exp(lo)


def rosenbaum_pairs(
    d: F, alpha: float, direction: float = 1.0, grid: tuple[float, ...] = ROSENBAUM_GRID
) -> tuple[float | None, dict[str, float]]:
    """Γ* and the upper-bound p-values on ``grid`` (keyed by Γ as text) for
    treated-minus-control pair differences ``d``, testing no effect against an effect of the
    sign of ``direction``."""
    signed = np.asarray(d, dtype=float) * (1.0 if direction >= 0 else -1.0)
    table = {f"{g:g}": wilcoxon_upper_p(signed, g) for g in grid}
    return sensitivity_value(signed, alpha), table


def match_pairs(t: F, score: F, caliper: float) -> tuple[np.ndarray, np.ndarray]:
    """Greedy 1:1 nearest-neighbour matching on ``score``, without replacement.

    Each unit of the smaller arm, hardest first (the treated from the highest score down, or
    the controls from the lowest up), takes the nearest unmatched unit of the larger arm whose
    score is within ``caliper``; ties go to the partner with the higher score, then the lower
    row. Returns (treated rows, control rows), aligned.
    """
    treated = np.flatnonzero(t == 1)
    control = np.flatnonzero(t == 0)
    focal_is_treated = treated.size <= control.size
    focal, pool = (treated, control) if focal_is_treated else (control, treated)
    order = np.argsort(score[pool], kind="stable")
    sorted_scores = score[pool][order]
    m = sorted_scores.size
    # Union-find "next available" pointers to the right (index m = none) and left (-1 = none).
    right = np.arange(m + 1)
    left = np.arange(m + 1)  # left[k] for position k-1, shifted by one; 0 = none

    def find(parent: np.ndarray, k: int) -> int:
        root = k
        while parent[root] != root:
            root = parent[root]
        while parent[k] != root:
            parent[k], k = root, parent[k]
        return root

    fs = score[focal]
    focal_order = np.argsort(-fs if focal_is_treated else fs, kind="stable")
    out_f: list[int] = []
    out_p: list[int] = []
    for i in focal_order:
        s = fs[i]
        pos = int(np.searchsorted(sorted_scores, s, side="left"))
        r = find(right, pos)
        lft = find(left, pos) - 1  # nearest available at or below pos-1
        best = -1
        if r < m and (lft < 0 or sorted_scores[r] - s <= s - sorted_scores[lft]):
            best = r
        elif lft >= 0:  # the lowest available row among the scores equal to lft's
            best = find(right, int(np.searchsorted(sorted_scores, sorted_scores[lft], "left")))
        if best < 0 or abs(sorted_scores[best] - s) > caliper:
            continue
        right[best] = best + 1
        left[best + 1] = best
        out_f.append(int(focal[i]))
        out_p.append(int(pool[order[best]]))
    f_idx, p_idx = np.asarray(out_f, dtype=int), np.asarray(out_p, dtype=int)
    return (f_idx, p_idx) if focal_is_treated else (p_idx, f_idx)


def propensity_logit(d: Prepared, policy: AnalysisPolicy) -> F:
    """Logit of a logistic propensity on the adjustment set, clipped to the policy's bounds."""
    if d.X.shape[1] == 0:
        return np.zeros(d.n)
    lo, hi = policy.propensity_bounds
    p = np.clip(logistic(d.t, add_intercept(d.X)).p, lo, hi)
    return np.asarray(np.log(p / (1 - p)), dtype=float)


def rosenbaum_sensitivity(
    d: Prepared, primary: Fit, strategy: Strategy | None, policy: AnalysisPolicy
) -> tuple[RosenbaumSensitivity | None, str | None]:
    """The matched design and its Rosenbaum bound, or None with the reason it does not apply."""
    if strategy is Strategy.randomized:
        return None, (
            "treatment was randomized: assignment is known, so hidden bias in it is excluded "
            "by design"
        )
    if strategy is not Strategy.backdoor:
        name = strategy.value if strategy is not None else "no strategy"
        return None, (
            f"identified by {name}, not by adjustment: a matched comparison is not the "
            "identifying design"
        )
    if d.treatment_kind != "binary":
        return None, "a matched design needs a binary treatment"
    score = propensity_logit(d, policy)
    sd = float(np.std(score))
    caliper = policy.match_caliper * sd if sd > 0 else 0.0
    ti, ci = match_pairs(d.t, score, caliper)
    n_pairs = int(ti.size)
    n_focal = int(min(d.t.sum(), d.n - d.t.sum()))
    if n_pairs < policy.min_per_arm:
        return None, (
            f"{n_pairs} matched pairs within the caliper; fewer than min_per_arm "
            f"({policy.min_per_arm})"
        )
    diffs = d.y[ti] - d.y[ci]
    direction = 1.0 if primary.record.point >= 0 else -1.0
    alpha = one_sided_alpha(policy.ci_level)
    gamma_star, table = rosenbaum_pairs(diffs, alpha, direction)
    smd: float | None = None
    if d.X.shape[1]:
        pooled = np.sqrt((d.X[d.t == 1].var(axis=0) + d.X[d.t == 0].var(axis=0)) / 2)
        keep = pooled > 0
        if keep.any():
            gap = d.X[ti].mean(axis=0) - d.X[ci].mean(axis=0)
            smd = float(np.max(np.abs(gap[keep] / pooled[keep])))
    return (
        RosenbaumSensitivity(
            method=f"1:1 greedy nearest-neighbour matching on the propensity-score logit, "
            f"without replacement, caliper {policy.match_caliper:g} SD; Wilcoxon signed-rank "
            "test, Rosenbaum upper bound, normal approximation (Rosenbaum 2002, §4.3.3)",
            n_pairs=n_pairs,
            n_unmatched=n_focal - n_pairs,
            max_abs_smd=smd,
            matched_difference=float(diffs.mean()),
            gamma_star=gamma_star,
            p_upper=table,
        ),
        None,
    )


def analyze_sensitivity(
    d: Prepared, primary: Fit, strategy: Strategy | None, policy: AnalysisPolicy
) -> Sensitivity:
    ev = e_values(d, primary, policy.ci_level)
    rv = (
        robustness_value(d, policy.ci_level)
        if strategy in (Strategy.backdoor, Strategy.randomized)
        else None
    )
    rb, rb_reason = rosenbaum_sensitivity(d, primary, strategy, policy)
    return Sensitivity(
        rosenbaum=rb,
        rosenbaum_null_reason=rb_reason,
        e_value=ev[0] if ev else None,
        e_value_ci=ev[1] if ev else None,
        e_value_method=ev[2] if ev else None,
        partial_r2_treatment=rv[0] if rv else None,
        robustness_value=rv[1] if rv else None,
        robustness_value_alpha=rv[2] if rv else None,
    )

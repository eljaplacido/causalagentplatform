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
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from causal_core.contract import AnalysisPolicy, EstimateRecord, Sensitivity, Strategy
from causal_core.estimators import Fit, Prepared
from causal_core.estimators._linalg import add_intercept


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


def analyze_sensitivity(
    d: Prepared, primary: Fit, strategy: Strategy | None, policy: AnalysisPolicy
) -> Sensitivity:
    ev = e_values(d, primary, policy.ci_level)
    rv = (
        robustness_value(d, policy.ci_level)
        if strategy in (Strategy.backdoor, Strategy.randomized)
        else None
    )
    return Sensitivity(
        e_value=ev[0] if ev else None,
        e_value_ci=ev[1] if ev else None,
        e_value_method=ev[2] if ev else None,
        partial_r2_treatment=rv[0] if rv else None,
        robustness_value=rv[1] if rv else None,
        robustness_value_alpha=rv[2] if rv else None,
    )

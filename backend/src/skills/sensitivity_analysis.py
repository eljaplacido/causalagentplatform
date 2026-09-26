"""
Sensitivity to unmeasured confounding, via causal-core's E-value.

The E-value (VanderWeele & Ding 2017) is defined on the risk-ratio scale. So it
is computed only when the scale is known:

* ``effect_scale="risk_ratio"``: ``ate`` and its interval are risk ratios;
* ``effect_scale="difference"`` with the outcome's standard deviation
  (``outcome_sd``, or ``data`` plus ``outcome_column``): the approximation
  RR = exp(0.91 * effect / sd) that the method's authors give for continuous
  outcomes.

Otherwise the E-value is ``None``, with the reason. The previous version read
any effect as a log-odds ratio (so the same effect in cents or in dollars gave
different "robustness"), and returned "Rosenbaum bounds" computed by multiplying
the estimate by an invented factor. Both are removed.

Rosenbaum bounds need matched data. Given treated-minus-control matched pair
differences (``pair_differences``), ``rosenbaum_gamma`` is causal-core's
sensitivity value: the largest Γ at which Wilcoxon's signed-rank test of no
effect still rejects at ``(1 - ci_level)/2`` one-sided, in the direction of
``ate`` (Rosenbaum 2002, §4.3.3; pre-registered as T9). Without pairs it is
``None``; ``causal_core.analyze`` builds the matched design itself from data.

EpistemicState constraints enforced:
- audit_log_required: sensitivity results logged
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import structlog
from causal_core.sensitivity import e_value_rr, one_sided_alpha, rosenbaum_pairs

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger(__name__)


class SensitivityAnalysisError(Exception):
    """Raised when sensitivity analysis fails."""


def _rosenbaum(
    pair_differences: Sequence[float] | None, direction: float, ci_level: float
) -> dict[str, Any]:
    if pair_differences is None:
        return {"rosenbaum_gamma": None, "rosenbaum": None}
    alpha = one_sided_alpha(ci_level)
    gamma, table = rosenbaum_pairs(np.asarray(pair_differences, dtype=float), alpha, direction)
    return {
        "rosenbaum_gamma": None if gamma is None else round(gamma, 4),
        "rosenbaum": {
            "alpha": alpha,
            "alternative": "greater" if direction >= 0 else "less",
            "n_pairs": len(pair_differences),
            "p_upper": table,
            "method": "Wilcoxon signed-rank, Rosenbaum upper bound, normal approximation",
        },
    }


def _e_values(
    point_rr: float, lo_rr: float | None, hi_rr: float | None
) -> tuple[float, float | None]:
    ev = e_value_rr(point_rr)
    if lo_rr is None or hi_rr is None:
        return ev, None
    if lo_rr <= 1 <= hi_rr:
        return ev, 1.0
    return ev, e_value_rr(lo_rr if point_rr > 1 else hi_rr)


async def sensitivity_analysis(
    ate: float | None,
    confidence_interval: list[float | None],
    assumptions: list[dict[str, Any]],
    data: Any | None = None,
    *,
    effect_scale: Literal["difference", "risk_ratio"] = "difference",
    outcome_sd: float | None = None,
    outcome_column: str | None = None,
    robust_e_value: float = 2.0,
    pair_differences: Sequence[float] | None = None,
    ci_level: float = 0.95,
) -> dict[str, Any]:
    """How strong would an unmeasured confounder need to be to explain the effect away?

    Returns ``e_value`` (point), ``e_value_ci`` (for the interval bound nearest the
    null; 1.0 when the interval includes the null), ``robust_to_unmeasured_confounding``
    (``e_value_ci >= robust_e_value``, a convention, not a law; ``None`` when not
    computable), ``method`` and ``reason``; ``rosenbaum_gamma`` and ``rosenbaum`` (the
    upper-bound p-values by Γ) when ``pair_differences`` are given, else ``None``.
    """
    if pair_differences is not None and len(pair_differences) < 2:
        raise SensitivityAnalysisError("a Rosenbaum bound needs at least two matched pairs")
    direction = -1.0 if ate is not None and ate < 0 else 1.0
    base: dict[str, Any] = {**_rosenbaum(pair_differences, direction, ci_level),
                            "robust_threshold": robust_e_value}
    if ate is None:
        return {**base, "e_value": None, "e_value_ci": None,
                "robust_to_unmeasured_confounding": None, "method": "none",
                "reason": "no_ate_estimate"}
    lo, hi = (list(confidence_interval) + [None, None])[:2]

    if effect_scale == "risk_ratio":
        if ate <= 0 or (lo is not None and lo <= 0):
            return {**base, "e_value": None, "e_value_ci": None,
                    "robust_to_unmeasured_confounding": None, "method": "none",
                    "reason": "risk ratios must be positive"}
        ev, ev_ci = _e_values(float(ate), lo, hi)
        method = "e_value (risk ratio)"
    else:
        sd = outcome_sd
        if sd is None and data is not None and outcome_column is not None:
            sd = float(data[outcome_column].std(ddof=1))
        if sd is None or not sd > 0:
            return {**base, "e_value": None, "e_value_ci": None,
                    "robust_to_unmeasured_confounding": None, "method": "none",
                    "reason": "an E-value for a difference-scale effect needs the outcome's "
                    "standard deviation (outcome_sd, or data with outcome_column), or pass "
                    "effect_scale='risk_ratio'"}

        def rr(effect: float) -> float:
            return math.exp(0.91 * effect / sd)

        ev, ev_ci = _e_values(
            rr(float(ate)),
            rr(float(lo)) if lo is not None else None,
            rr(float(hi)) if hi is not None else None,
        )
        method = "e_value (approximate: RR = exp(0.91 x effect / sd(outcome)))"

    robust = None if ev_ci is None else bool(ev_ci >= robust_e_value)
    logger.info("sensitivity_analysis.complete", e_value=ev, e_value_ci=ev_ci, method=method)
    return {**base, "e_value": round(ev, 4), "e_value_ci": None if ev_ci is None else round(ev_ci, 4),
            "robust_to_unmeasured_confounding": robust, "method": method, "reason": None}

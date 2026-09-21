"""
Effects within declared subgroups, and the discipline that makes them claimable.

"Does this work?" is rarely the operational question. "For whom does it work?" is:
a remediation playbook that shortens P1 incidents and lengthens P3 ones has an
average effect near zero and two true answers.

Subgroups are also where careful people ruin their own evidence. Estimate an effect
in ten segments, act on the biggest, and the biggest is mostly noise -- with ten
null segments at alpha 0.05, something looks significant about 40% of the time. So
three rules hold here:

  1. **Declared, never discovered.** The caller names the segments in
     ``query.segment_by`` before seeing any answer. Searching for the subgroup with
     the largest effect is a different activity with different statistics, and this
     kernel does not do it.
  2. **Two intervals, and the honest one is the wider.** Each segment gets its
     unadjusted interval ("what is the effect here") and a family-wise interval
     ("what may I claim, having looked at k segments"). Anything downstream that
     makes a claim reads the second.
  3. **Ask whether they differ before saying they do.** One Cochran's Q across the
     segments, reported with the answer. When it does not reject, the segments are
     reported with that fact attached, because the differences are not
     distinguishable from noise.

Each segment is a full analysis of its own stratum -- same graph, same
identification, same design checks -- so a stratum with too few units or without
overlap produces its own typed refusal rather than a number. Strata are disjoint,
which is what makes the tests here simple enough to trust.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
import pandas as pd
from scipy import stats

from causal_core.contract import (
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    CausalQuery,
    CausalResult,
    Heterogeneity,
    Interval,
    SegmentEffect,
)
from causal_core.refute import holm

if TYPE_CHECKING:
    from collections.abc import Sequence

#: A stratum smaller than this is not worth an estimate even if the design checks
#: would pass it; the interval would be too wide to mean anything and the segment
#: would still consume a share of the family-wise budget.
MIN_SEGMENT_ROWS = 20

#: More segments than this and the correction dominates: every interval is too wide
#: to act on, which is a sign the question should be asked differently rather than
#: a result to report.
MAX_SEGMENTS = 20


class Analyzer(Protocol):
    """The recursive call back into the pipeline, injected to avoid a cycle."""

    def __call__(
        self,
        data: pd.DataFrame,
        graph: Any,
        *,
        query: CausalQuery,
        policy: AnalysisPolicy,
    ) -> CausalResult | Abstention | Any: ...


def _levels(series: pd.Series) -> list[Any]:
    return sorted(series.dropna().unique().tolist(), key=str)


def stratum_policy(policy: AnalysisPolicy) -> AnalysisPolicy:
    """Policy for a single stratum's analysis.

    Falsification tests and sensitivity run once, on the whole design, where they
    have the power to say something; repeating them per stratum multiplies the
    tests without multiplying the evidence. The stratum keeps every check that
    decides whether *this* estimate is trustworthy: identification, overlap,
    balance, sample size.
    """
    return policy.model_copy(update={"refuters": (), "sensitivity": False})


def _wald_p(point: float, se: float | None) -> float | None:
    if se is None or not math.isfinite(se) or se <= 0:
        return None
    return float(2 * stats.norm.sf(abs(point / se)))


def _family_wise_interval(point: float, se: float | None, level: float, k: int) -> Interval | None:
    """Bonferroni: the interval that survives having looked at k segments."""
    if se is None or not math.isfinite(se) or se <= 0 or k < 1:
        return None
    alpha = (1.0 - level) / k
    z = float(stats.norm.ppf(1 - alpha / 2))
    return Interval(
        level=1.0 - (1.0 - level) / k,
        lo=point - z * se,
        hi=point + z * se,
        method=f"bonferroni_{k}",
    )


def cochran_q(points: Sequence[float], ses: Sequence[float], alpha: float) -> Heterogeneity | None:
    """Do these segments differ by more than sampling noise?

    Strata are disjoint, so the estimates are independent and the classical
    fixed-effect heterogeneity statistic applies: Q = sum wᵢ(θᵢ - θ̄)², weights
    1/SEᵢ², compared with χ² on k-1 degrees of freedom. Fewer than two estimable
    segments means there is nothing to compare, and None says so rather than
    returning a test of nothing.
    """
    usable = [(p, s) for p, s in zip(points, ses, strict=True) if s and math.isfinite(s) and s > 0]
    if len(usable) < 2:
        return None
    pts = np.array([p for p, _ in usable], dtype=float)
    weights = np.array([1.0 / s**2 for _, s in usable], dtype=float)
    pooled = float(np.sum(weights * pts) / np.sum(weights))
    q = float(np.sum(weights * (pts - pooled) ** 2))
    df = len(usable) - 1
    p_value = float(stats.chi2.sf(q, df))
    return Heterogeneity(
        statistic=q,
        df=df,
        p_value=p_value,
        alpha=alpha,
        differs=p_value < alpha,
        segments_compared=len(usable),
    )


def estimate_segments(
    frame: pd.DataFrame,
    graph: Any,
    query: CausalQuery,
    policy: AnalysisPolicy,
    analyzer: Analyzer,
) -> tuple[tuple[SegmentEffect, ...], Heterogeneity | None, list[str]]:
    """Estimate the effect within each declared segment.

    Returns the per-segment effects, the heterogeneity test, and any warnings.
    Never raises: a segment that cannot be estimated becomes a refusal inside the
    result, because a partial answer with the gaps named is more useful than an
    exception, and far more useful than a silently shorter list.
    """
    warnings: list[str] = []
    effects: list[SegmentEffect] = []

    for variable in query.segment_by:
        if variable not in frame.columns:
            warnings.append(f"segment variable {variable!r} is not a column of the data")
            continue
        if variable in (query.treatment, query.outcome):
            warnings.append(
                f"segment variable {variable!r} is the "
                f"{'treatment' if variable == query.treatment else 'outcome'}; "
                "segmenting by it is not a subgroup analysis"
            )
            continue

        levels = _levels(frame[variable])
        if len(levels) > MAX_SEGMENTS:
            warnings.append(
                f"{variable!r} has {len(levels)} levels, more than the {MAX_SEGMENTS} this "
                "kernel will correct for: with that many, every corrected interval is too "
                "wide to act on. Group the levels first."
            )
            continue
        if len(levels) < 2:
            warnings.append(f"{variable!r} takes one value in this data: nothing to compare")
            continue

        stratum_query = query.model_copy(update={"segment_by": ()})
        for level in levels:
            subset = frame[frame[variable] == level]
            effects.append(
                _one_segment(subset, graph, stratum_query, policy, analyzer, variable, level)
            )

    if not effects:
        return (), None, warnings

    # Multiplicity is over every segment examined in this analysis, across every
    # segmenting variable: the family is what was looked at, not what was reported.
    estimable = [e for e in effects if e.estimate is not None]
    k = len(effects)
    adjusted: list[SegmentEffect] = []
    rejected = _holm_decisions(estimable, policy)

    for effect in effects:
        if effect.estimate is None:
            adjusted.append(effect)
            continue
        record = effect.estimate
        adjusted.append(
            effect.model_copy(
                update={
                    "family_wise_ci": _family_wise_interval(
                        record.point, record.se, policy.ci_level, k
                    ),
                    "p_value": _wald_p(record.point, record.se),
                    "significant_after_correction": rejected.get(
                        (effect.variable, effect.level), False
                    ),
                }
            )
        )

    het = cochran_q(
        [e.estimate.point for e in estimable if e.estimate],
        [e.estimate.se or 0.0 for e in estimable if e.estimate],
        alpha=policy.segment_alpha,
    )
    if het is not None and not het.differs:
        warnings.append(
            f"the {het.segments_compared} segments are not distinguishable from one another "
            f"(Cochran's Q p = {het.p_value:.3g}): differences between them are within noise"
        )
    return tuple(adjusted), het, warnings


def _holm_decisions(
    estimable: list[SegmentEffect], policy: AnalysisPolicy
) -> dict[tuple[str, str], bool]:
    """Holm's step-down over the family, keyed by segment."""
    pairs = [
        ((e.variable, e.level), _wald_p(e.estimate.point, e.estimate.se))
        for e in estimable
        if e.estimate is not None
    ]
    usable = [(key, p) for key, p in pairs if p is not None]
    if not usable:
        return {}
    flags = holm([p for _, p in usable], 1.0 - policy.ci_level)
    return {key: flag for (key, _), flag in zip(usable, flags, strict=True)}


def _one_segment(
    subset: pd.DataFrame,
    graph: Any,
    stratum_query: CausalQuery,
    policy: AnalysisPolicy,
    analyzer: Analyzer,
    variable: str,
    level: Any,
) -> SegmentEffect:
    label = str(level)
    if len(subset) < MIN_SEGMENT_ROWS:
        return SegmentEffect(
            variable=variable,
            level=label,
            n_used=0,
            refusal=AbstentionReason.LOW_N,
            refusal_detail=(
                f"{len(subset)} rows in this segment, below the {MIN_SEGMENT_ROWS} needed for "
                "an interval worth reading"
            ),
        )

    try:
        out = analyzer(subset, graph, query=stratum_query, policy=stratum_policy(policy))
    except Exception as exc:  # one bad stratum must not lose the others
        return SegmentEffect(
            variable=variable,
            level=label,
            n_used=0,
            refusal=AbstentionReason.INVALID_INPUT,
            refusal_detail=f"{type(exc).__name__}: {exc}"[:500],
        )

    if isinstance(out, CausalResult):
        return SegmentEffect(
            variable=variable,
            level=label,
            n_used=out.diagnostics.n_used,
            estimate=out.primary,
        )
    if isinstance(out, Abstention):
        return SegmentEffect(
            variable=variable,
            level=label,
            n_used=out.diagnostics.n_used if out.diagnostics else 0,
            refusal=out.reason,
            refusal_detail=out.detail[:500],
        )
    return SegmentEffect(
        variable=variable,
        level=label,
        n_used=0,
        refusal=AbstentionReason.INVALID_INPUT,
        refusal_detail="the analysis of this segment failed",
    )

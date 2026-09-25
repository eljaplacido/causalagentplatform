"""
Anytime-valid confidence sequences for randomized streams that are watched while they run.

A canary is looked at after every batch and stopped the moment its interval clears
zero. A fixed-sample 95% interval read that way excludes a true null at *some* look
far more often than 5% of the time. A **confidence sequence** is a sequence of
intervals that contains the effect at every look simultaneously with probability
1 − α, so it may be read -- and acted on -- as often as wanted.

**The score.** For unit i, assigned to treatment with known probability p::

    ψ_i = m̂1 − m̂0 + T_i (Y_i − m̂1) / p − (1 − T_i)(Y_i − m̂0) / (1 − p)

where m̂1, m̂0 are the arm means of the units that arrived *before* i. Because the
baseline is predictable -- it uses only the past -- each ψ_i has conditional mean
equal to the effect under randomization, whatever the baseline is; a good baseline
only makes it less variable. The level may drift during the run (traffic by hour of
day) without biasing it: the drift is common to both arms and is absorbed by the
running means.

**The boundary.** The asymptotic confidence sequence of Waudby-Smith, Arbour, Sinha,
Kennedy & Ramdas (2023, "Time-uniform central limit theory and asymptotic confidence
sequences")::

    ψ̄_t ± σ̂_t · sqrt( 2(tρ² + 1) / (t²ρ²) · log( sqrt(tρ² + 1) / α ) )

with ρ tuned so the sequence is tightest at ``cs_target_n`` units. Asymptotic: it is
not reported before ``cs_burn_in`` units. Wider than a fixed-n interval by design --
that is the price of being allowed to look.
"""

from __future__ import annotations

import math
from collections.abc import Callable
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
    IdentificationCertificate,
    Interval,
    Provenance,
    ResultStatus,
    SequentialLook,
    SequentialSummary,
    Strategy,
)

F = NDArray[np.float64]

ESTIMATOR = "sequential_aipw"
METHOD = "asymptotic confidence sequence (anytime-valid; Waudby-Smith et al. 2023)"
ASSUMPTIONS = (
    "treatment assigned independently of potential outcomes, with the declared probability",
    "units arrive independently; outcomes are observed before the unit is analysed",
    "no interference between units",
)

#: Allocation mismatch that refuses the stream, as the fixed-sample path does (SRM).
SRM_ALPHA = 0.001


def rho_for(target_n: int, alpha: float) -> float:
    """Mixture tuning that minimises the boundary's width at ``target_n`` units."""
    la = -2 * math.log(alpha)
    return math.sqrt((la + math.log(la + 1)) / target_n)


def half_width(sigma: float, t: int, rho: float, alpha: float) -> float:
    """The confidence sequence's half-width at t units. A seam for the seeded defects."""
    r2 = rho * rho
    mixture = math.log(math.sqrt(t * r2 + 1) / alpha)
    return sigma * math.sqrt(2 * (t * r2 + 1) / (t * t * r2) * mixture)


def allocation(query: CausalQuery) -> float:
    """The known probability of treatment. A seam: `ignore_allocation` replaces it."""
    assert query.expected_treated_share is not None
    return float(query.expected_treated_share)


def scores(y: F, t: F, p: float) -> F:
    """ψ_i with predictable per-arm baselines (means of the units before i)."""
    n = y.shape[0]

    def past(values: F, mask: F) -> tuple[F, F]:
        s = np.concatenate([[0.0], np.cumsum(values * mask)[:-1]])
        c = np.concatenate([[0.0], np.cumsum(mask)[:-1]])
        return s, c

    s_all, c_all = past(y, np.ones(n))
    s1, c1 = past(y, t)
    s0, c0 = past(y, 1 - t)
    overall = np.divide(s_all, c_all, out=np.zeros(n), where=c_all > 0)
    m1 = np.divide(s1, c1, out=overall.copy(), where=c1 > 0)
    m0 = np.divide(s0, c0, out=overall.copy(), where=c0 > 0)
    return m1 - m0 + t * (y - m1) / p - (1 - t) * (y - m0) / (1 - p)


def analyze_sequential(
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
    if cert.strategy is not Strategy.randomized:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            "anytime-valid monitoring needs a randomized design (design='randomized', and a "
            "graph with no causes of the treatment); the score is unbiased only because "
            "assignment is random with a known probability",
            q=query,
            cert=cert,
        )
    if query.expected_treated_share is None:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            "sequential monitoring needs `expected_treated_share`, the known probability of "
            "assignment to treatment",
            ("the allocation probability the randomizer uses",),
            q=query,
            cert=cert,
        )
    unsupported = {
        "adjust_for": query.adjust_for,
        "segment_by": query.segment_by,
        "negative_control_outcomes": query.negative_control_outcomes,
        "treatment_levels": query.treatment_levels,
        "unit/time": query.unit or query.time,
    }
    given = sorted(k for k, v in unsupported.items() if v)
    if given:
        return refuse(
            AbstentionReason.INVALID_INPUT,
            f"not supported with sequential monitoring in this version: {given}",
            q=query,
            cert=cert,
        )
    seq, d_col, y_col = query.sequence, query.treatment, query.outcome
    assert seq is not None
    if seq not in frame.columns:
        return refuse(AbstentionReason.INVALID_INPUT, f"sequence {seq!r} is not a column",
                      q=query, cert=cert)
    sub = frame[[seq, d_col, y_col]]
    if sub.isna().any().any():
        return refuse(AbstentionReason.INVALID_INPUT, "missing values in the stream", q=query,
                      cert=cert)
    if sub[seq].duplicated().any():
        return refuse(
            AbstentionReason.INVALID_INPUT,
            f"arrival positions in {seq!r} repeat: the order must be total", q=query, cert=cert,
        )
    try:
        binary = {float(v) for v in pd.unique(sub[d_col])} <= {0.0, 1.0}
    except (TypeError, ValueError):
        binary = False
    if not binary:
        return refuse(AbstentionReason.INVALID_INPUT,
                      f"treatment {d_col!r} must be binary (0/1)", q=query, cert=cert)
    ordered = sub.sort_values(seq, kind="mergesort")
    y = ordered[y_col].to_numpy(dtype=float)
    t = ordered[d_col].to_numpy(dtype=float)
    n = y.shape[0]
    if n < policy.cs_burn_in:
        return refuse(
            AbstentionReason.LOW_N,
            f"{n} units, fewer than the burn-in of {policy.cs_burn_in}: the sequence is "
            "asymptotic and is not reported this early",
            q=query,
            cert=cert,
        )
    prov["data_digest"] = data_digest(ordered)

    p = allocation(query)
    n_treated = int(t.sum())
    srm = float(stats.chisquare([n_treated, n - n_treated], [p * n, (1 - p) * n]).pvalue)
    checks = [DesignCheck(
        name="allocation matches the declared probability",
        passed=srm >= SRM_ALPHA,
        detail=f"{n_treated}/{n} treated against p={p}; chi-square p={srm:.2g}",
    )]
    if srm < SRM_ALPHA:
        return refuse(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"sample ratio mismatch: {n_treated} of {n} treated where p={p} was declared "
            f"(chi-square p={srm:.2g} < {SRM_ALPHA}); the assignment is not what the analysis "
            "assumes",
            q=query,
            cert=cert,
        )

    psi = scores(y, t, p)
    alpha = 1 - policy.ci_level
    rho = rho_for(policy.cs_target_n, alpha)
    cs1, cs2 = np.cumsum(psi), np.cumsum(psi * psi)
    marks = list(range(policy.cs_burn_in, n + 1, policy.cs_look_every))
    if marks[-1] != n:
        marks.append(n)
    looks: list[SequentialLook] = []
    above = below = None
    lo_run, hi_run = -math.inf, math.inf  # the running intersection: still valid, never wider
    for k in marks:
        mean = cs1[k - 1] / k
        var = max((cs2[k - 1] - k * mean * mean) / (k - 1), 0.0)
        hw = half_width(math.sqrt(var), k, rho, alpha)
        lo_run, hi_run = max(lo_run, mean - hw), min(hi_run, mean + hw)
        lo, hi = (lo_run, hi_run) if lo_run <= hi_run else (mean - hw, mean + hw)
        looks.append(SequentialLook(
            n=k, n_treated=int(t[:k].sum()), estimate=float(mean), lo=float(lo), hi=float(hi)
        ))
        if above is None and lo > 0:
            above = k
        if below is None and hi < 0:
            below = k

    last = looks[-1]
    sd_psi = float(np.std(psi, ddof=1))
    rec = EstimateRecord(
        estimator=ESTIMATOR,
        family="confidence_sequence",
        estimand="ate",
        point=last.estimate,
        se=sd_psi / math.sqrt(n),
        ci=Interval(level=policy.ci_level, lo=last.lo, hi=last.hi, method=METHOD),
        n_used=n,
        assumptions=ASSUMPTIONS,
    )
    status = ResultStatus.provisional if unreviewed else ResultStatus.supported
    if unreviewed:
        warnings = [*warnings, f"graph contains unreviewed edges: {unreviewed}"]
    result = CausalResult(
        status=status,
        query=query,
        certificate=cert,
        primary=rec,
        estimates=(rec,),
        diagnostics=Diagnostics(
            n_rows=len(frame),
            n_used=n,
            treatment_kind="binary",
            n_treated=n_treated,
            n_control=n - n_treated,
            outcome_sd=float(np.std(y, ddof=1)),
            srm_p_value=srm,
            checks=tuple(checks),
        ),
        sequential=SequentialSummary(
            method=METHOD,
            alpha=alpha,
            rho=rho,
            target_n=policy.cs_target_n,
            looks=tuple(looks),
            first_above_zero=above,
            first_below_zero=below,
        ),
        claims=build_claims({
            "effect": (rec.point, "/primary/point", None),
            "effect.ci.lo": (last.lo, "/primary/ci/lo", None),
            "effect.ci.hi": (last.hi, "/primary/ci/hi", None),
            "ci.level": (policy.ci_level * 100, "/primary/ci/level", "%"),
            "n": (float(n), "/primary/n_used", None),
        }),
        warnings=tuple(warnings),
        provenance=Provenance(**prov),
    )
    return result.model_copy(
        update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))}
    )

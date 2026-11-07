"""
Blocked and paired randomized designs: the same task run under both arms.

When every task is run once with the change and once without, the comparison that matters is
inside the task. An analysis that ignores the pairing compares two arm means whose noise
includes every difference between tasks, and its interval is wide for that reason. This module
compares within blocks -- a matched pair, or a stratum inside which treatment was randomized --
and lets the blocks, not the units, be the independent observations.

The estimate is the size-weighted average of within-block differences,

    tau = sum_b (n_b / N) * (mean_b(Y | T=1) - mean_b(Y | T=0)),

the per-unit average effect: the block-fixed-effects estimator with a treatment-by-block
interaction, unbiased whatever share of each block was treated. (A fixed-effects regression
without the interaction weights block b by n_b p_b (1 - p_b) and estimates a different
average when that share varies -- the seeded defect `fixed_effects_weights`.) Its variance is
the between-block variance of the weighted block effects,

    V = K / (K - 1) * sum_b (n_b / N)^2 (tau_b - tau)^2,

with critical values from t(K - 1); for pairs this is the paired t interval. It is honest about
the average effect in the population the blocks are drawn from.

**A binary outcome in pairs** gets a different interval. With few discordant pairs the paired t
interval is confidently wrong -- zero width whenever no pair disagrees, coverage 0.45 at 30
pairs in the worst case of an exact enumeration (benchmarks/t11-blocked) -- so the interval is
the score interval for a paired difference of proportions (Tango 1998) with a continuity
correction of 1 on the scale of b - c. At a difference of zero its statistic is McNemar's with
Edwards' correction, so the interval excludes zero only when McNemar's test rejects; over every
split of up to 120 discordant pairs it never did so where the exact test did not. The exact
two-sided McNemar p-value is reported beside it. The price is coverage above nominal.

Refused: a design that is not randomized (``INVALID_INPUT``); a treatment that never varies
(``NO_VARIATION``); blocks in which only one arm was observed, unless ``policy.missing='drop'``
(then they are left out, counted in ``BlockSummary`` and warned about); fewer than
``policy.min_per_arm`` complete blocks or ``policy.min_n`` units (``LOW_N``); and the options this
version does not combine with blocks (clusters, sequences, segments, adjustment sets, the ATT).
Gated by T11.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import optimize, stats

from causal_core.canonical import data_digest, digest
from causal_core.claims import build_claims
from causal_core.contract import (
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    BlockSummary,
    CausalQuery,
    CausalResult,
    DesignCheck,
    Diagnostics,
    EstimateRecord,
    IdentificationCertificate,
    Interval,
    Provenance,
    RefutationResult,
    ResultStatus,
    Sensitivity,
    Strategy,
)
from causal_core.design import no_variation_diagnostics, no_variation_refusal
from causal_core.sensitivity import _e_value_interval

F = NDArray[np.float64]

ESTIMATOR = "blocked_difference_in_means"
PAIRED_BINARY_ESTIMATOR = "paired_risk_difference"
METHOD_T = "t(K-1); between-block variance, blocks as the independent observations"
METHOD_SCORE = (
    "score interval for a paired difference of proportions with a continuity correction "
    "(Tango 1998); excludes 0 only when McNemar's test rejects"
)
ASSUMPTIONS = (
    "treatment assigned at random within each block, independently of potential outcomes",
    "blocks independent of one another; units within a block may be arbitrarily correlated",
    "no interference within a block: one unit's arm does not change another's outcome",
)
#: Numerical margin kept from the ends of [-1, 1] when inverting the score test.
_EDGE = 1e-9


# ── seams (each one a seeded defect's target: causal_core.bench.defects_t11) ──────────


def block_weights(eff: pd.DataFrame) -> F:
    """Per-unit estimand: block b weighted by n_b / N. A seam: `fixed_effects_weights`."""
    n: F = eff["n"].to_numpy(dtype=float)
    out: F = n / n.sum()
    return out


def block_variance(y: F, t: F, diff: F, w: F, point: float) -> float:
    """Between-block variance of the weighted block effects, blocks as the independent
    observations. A seam: `unpaired_se` treats every unit as independent instead."""
    k = diff.shape[0]
    return float(k / (k - 1) * np.sum(w**2 * (diff - point) ** 2))


def score_z(b: int, c: int, k: int, d0: float, correction: float = 1.0) -> float:
    """Tango's (1998) score statistic for H0: p10 - p01 = d0, with a continuity correction.

    ``b`` pairs in which only the treated unit has the outcome, ``c`` only the control, ``k``
    pairs. The nuisance (p01) is its maximum-likelihood value under the constraint, in closed
    form. At d0 = 0 this is McNemar's statistic, (|b - c| - correction) / sqrt(b + c).
    """
    big_b = (b + c) - (2 * k - b + c) * d0
    disc = big_b * big_b + 8.0 * k * c * d0 * (1.0 - d0)
    p01 = (big_b + math.sqrt(max(disc, 0.0))) / (4.0 * k)
    var = k * (2.0 * p01 + d0 * (1.0 - d0))
    num = b - c - k * d0
    num = math.copysign(max(abs(num) - correction, 0.0), num)
    if var <= 0:
        return 0.0 if num == 0 else math.copysign(math.inf, num)
    return num / math.sqrt(var)


def paired_binary_interval(b: int, c: int, k: int, level: float) -> tuple[float, float]:
    """The continuity-corrected score interval for p10 - p01: every d0 the test does not reject.

    A seam: `wald_for_binary_pairs` returns the paired t interval instead.
    """
    z = float(stats.norm.ppf(0.5 + level / 2))
    est = (b - c) / k

    def lower(d: float) -> float:
        return score_z(b, c, k, d) - z

    def upper(d: float) -> float:
        return score_z(b, c, k, d) + z

    lo = -1.0
    if lower(-1.0 + _EDGE) > 0 and est > -1.0 + _EDGE:
        lo = float(optimize.brentq(lower, -1.0 + _EDGE, est, xtol=1e-12))
    hi = 1.0
    if upper(1.0 - _EDGE) < 0 and est < 1.0 - _EDGE:
        hi = float(optimize.brentq(upper, est, 1.0 - _EDGE, xtol=1e-12))
    return lo, hi


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar: the conditional binomial test on the discordant pairs."""
    d = b + c
    if d == 0:
        return 1.0
    return min(1.0, 2.0 * float(stats.binom.cdf(min(b, c), d, 0.5)))


def paired_binary_power(k: int, p_benefit: float, p_harm: float, alpha: float) -> float:
    """Exact probability that this module's interval for ``k`` pairs lies wholly on the benefit
    side of zero, when a pair is treated-only with probability ``p_benefit`` and control-only
    with ``p_harm``.

    The interval excludes zero exactly when McNemar's test with Edwards' correction rejects,
    |b - c| - 1 > z sqrt(b + c) (its statistic at d0 = 0), so the power is a sum over the
    number of discordant pairs, each a binomial tail: no simulation, no normal approximation.
    """
    disc = p_benefit + p_harm
    if k < 1 or disc <= 0:
        return 0.0
    z = float(stats.norm.ppf(1 - alpha / 2))
    nd = np.arange(k + 1)
    # benefit side: b - c - 1 > z sqrt(nd), with b - c = 2b - nd
    threshold = np.floor((nd + 1 + z * np.sqrt(nd)) / 2)
    tail = stats.binom.sf(threshold, nd, min(p_benefit / disc, 1.0))
    return float(np.sum(stats.binom.pmf(nd, k, disc) * tail))


def paired_binary_pairs(discordance: float, effect: float, alpha: float, power: float,
                        max_pairs: int = 1_000_000) -> int | None:
    """The fewest pairs at which `paired_binary_power` reaches ``power`` for a difference of
    ``effect`` (> 0) when a share ``discordance`` (>= effect) of pairs disagree. None when it
    would take more than ``max_pairs``."""
    if effect <= 0 or discordance < effect:
        return None
    p_benefit, p_harm = (discordance + effect) / 2, (discordance - effect) / 2

    def ok(k: int) -> bool:
        return paired_binary_power(k, p_benefit, p_harm, alpha) >= power

    hi = 2
    while not ok(hi):
        hi *= 2
        if hi > max_pairs:
            return None
    lo = hi // 2
    while hi - lo > 1:  # power rises with k up to a sawtooth of discreteness; the first k is
        mid = (lo + hi) // 2  # found up to that sawtooth, then walked down while it holds
        lo, hi = (lo, mid) if ok(mid) else (mid, hi)
    while hi > 2 and ok(hi - 1):
        hi -= 1
    return hi


def neyman_se(y: F, t: F) -> float:
    """The standard error an analysis ignoring the blocks reports (Neyman, unequal variances)."""
    y1, y0 = y[t == 1], y[t == 0]
    return math.sqrt(float(np.var(y1, ddof=1)) / y1.shape[0]
                     + float(np.var(y0, ddof=1)) / y0.shape[0])


# ── the analysis ──────────────────────────────────────────────────────────────────────


def block_effects(sub: pd.DataFrame, block: str, d_col: str, y_col: str) -> pd.DataFrame:
    """Per complete block: the arm means, sizes and their difference."""
    g = sub.groupby([block, d_col])[y_col].agg(["mean", "size"]).unstack(d_col)
    eff = pd.DataFrame({
        "m1": g[("mean", 1.0)], "m0": g[("mean", 0.0)],
        "n1": g[("size", 1.0)], "n0": g[("size", 0.0)],
    })
    eff["diff"] = eff["m1"] - eff["m0"]
    eff["n"] = eff["n1"] + eff["n0"]
    return eff


def _p_value_t(point: float, se: float, df: int) -> float | None:
    if se <= 0 or not math.isfinite(se):
        return None
    return float(2 * stats.t.sf(abs(point / se), df))


def _estimate(sub: pd.DataFrame, block: str, d_col: str, y_col: str,
              level: float) -> tuple[float, float, float, float, dict[str, Any]]:
    """(point, se, lo, hi, facts) for one outcome column over the complete blocks."""
    y = sub[y_col].to_numpy(dtype=float)
    t = sub[d_col].to_numpy(dtype=float)
    eff = block_effects(sub, block, d_col, y_col)
    k = int(eff.shape[0])
    diff = eff["diff"].to_numpy(dtype=float)
    pairs = bool(((eff["n1"] == 1) & (eff["n0"] == 1)).all())
    binary = bool(np.isin(np.unique(y), [0.0, 1.0]).all())
    facts: dict[str, Any] = {"k": k, "pairs": pairs, "binary": binary, "eff": eff,
                             "sd_difference": float(np.std(diff, ddof=1)) if k > 1 else None}
    if pairs and binary:
        b = int(((eff["m1"] == 1) & (eff["m0"] == 0)).sum())
        c = int(((eff["m1"] == 0) & (eff["m0"] == 1)).sum())
        point = (b - c) / k
        se = float(np.std(diff, ddof=1)) / math.sqrt(k)
        lo, hi = paired_binary_interval(b, c, k, level)
        facts.update(b=b, c=c, mcnemar=mcnemar_exact_p(b, c), method=METHOD_SCORE,
                     estimator=PAIRED_BINARY_ESTIMATOR, p_value=mcnemar_exact_p(b, c))
        return point, se, lo, hi, facts
    w = block_weights(eff)
    point = float(np.sum(w * diff))
    se = math.sqrt(block_variance(y, t, diff, w, point))
    crit = float(stats.t.ppf(0.5 + level / 2, k - 1))
    facts.update(method=METHOD_T, estimator=ESTIMATOR, p_value=_p_value_t(point, se, k - 1))
    return point, se, point - crit * se, point + crit * se, facts


def _e_values(sub: pd.DataFrame, d_col: str, y_col: str, block: str, point: float,
              lo: float, hi: float, binary: bool) -> tuple[float, float, str] | None:
    """E-values for the blocked estimate, the main pipeline's two methods."""
    if binary:
        eff = block_effects(sub, block, d_col, y_col)
        w = block_weights(eff)
        p0 = float(np.sum(w * eff["m0"].to_numpy(dtype=float)))
        p1 = p0 + point
        if p0 <= 0 or p1 <= 0:
            return None
        # A lower bound below -p0 maps to a risk ratio <= 0; it only ever matters through
        # "does the interval contain 1", which the clamp leaves unchanged.
        rr_lo, rr_hi = max((p0 + lo) / p0, 1e-12), (p0 + hi) / p0
        ev, ev_ci = _e_value_interval(p1 / p0, rr_lo, rr_hi)
        return ev, ev_ci, ("risk ratio of the block-weighted arm proportions; the interval bound "
                           "mapped from the paired difference as (p0 + bound) / p0")
    sd_y = float(np.std(sub[y_col].to_numpy(dtype=float), ddof=1))
    if sd_y <= 0:
        return None

    def to_rr(effect: float) -> float:
        return math.exp(0.91 * effect / sd_y)

    ev, ev_ci = _e_value_interval(to_rr(point), to_rr(lo), to_rr(hi))
    return ev, ev_ci, "approximate: RR = exp(0.91 × effect / sd(outcome)) (VanderWeele & Ding 2017)"


def analyze_blocked(
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
            "a blocked analysis here is for designs randomized within blocks (design="
            "'randomized', a graph with no causes of the treatment)",
            q=query,
            cert=cert,
        )
    unsupported = {
        "adjust_for": query.adjust_for,
        "segment_by": query.segment_by,
        "cluster": query.cluster,
        "sequence": query.sequence,
        "treatment_levels": query.treatment_levels,
        "expected_treated_share": query.expected_treated_share,
        "unit": query.unit,
        "time": query.time,
        "washout": query.washout,
        "estimand='att'": query.estimand == "att",
    }
    given = sorted(k for k, v in unsupported.items() if v)
    if given:
        return refuse(AbstentionReason.INVALID_INPUT,
                      f"not supported with block in this version: {given}", q=query, cert=cert)
    b_col, d_col, y_col = query.block, query.treatment, query.outcome
    assert b_col is not None
    ncos = list(query.negative_control_outcomes)
    absent = [c for c in (b_col, *ncos) if c not in frame.columns]
    if absent:
        return refuse(AbstentionReason.INVALID_INPUT, f"not columns of the data: {absent}",
                      q=query, cert=cert)
    cols = list(dict.fromkeys([b_col, d_col, y_col, *ncos]))
    sub = frame[cols]
    n_rows = len(sub)
    na_rows = int(sub.isna().any(axis=1).sum())
    if na_rows:
        if policy.missing == "error":
            return refuse(
                AbstentionReason.DESIGN_CHECK_FAILED,
                f"{na_rows} of {n_rows} rows have missing values in "
                f"{[c for c in cols if sub[c].isna().any()]}",
                ("impute upstream with a documented method, or set policy.missing='drop' to use "
                 "complete cases (valid only if values are missing completely at random)",),
                q=query, cert=cert,
            )
        sub = sub.dropna()
        warnings = [*warnings, f"dropped {na_rows} rows with missing values (assumes missing "
                    "completely at random)"]
    numeric = [c for c in (d_col, y_col, *ncos)
               if not (pd.api.types.is_numeric_dtype(sub[c])
                       or pd.api.types.is_bool_dtype(sub[c]))]
    if numeric:
        return refuse(AbstentionReason.INVALID_INPUT, f"columns must be numeric or boolean: "
                      f"{numeric}", q=query, cert=cert)
    sub = sub.astype(dict.fromkeys((d_col, y_col, *ncos), float))
    tv = sub[d_col].to_numpy(dtype=float)
    yv = sub[y_col].to_numpy(dtype=float)
    if np.unique(tv).shape[0] < 2:
        diag, detail = no_variation_diagnostics(query, tv, yv, n_rows)
        text, fix = no_variation_refusal(query, detail, float(tv[0]) if tv.shape[0] else 0.0)
        return refuse(AbstentionReason.NO_VARIATION, text, fix, q=query, cert=cert, diag=diag)
    if not set(np.unique(tv).tolist()) <= {0.0, 1.0}:
        return refuse(AbstentionReason.INVALID_INPUT,
                      f"treatment {d_col!r} must be binary (0/1) in a blocked design",
                      q=query, cert=cert)
    if np.ptp(yv) == 0:
        return refuse(AbstentionReason.DESIGN_CHECK_FAILED, f"outcome {y_col!r} does not vary",
                      q=query, cert=cert)

    arms = sub.groupby(b_col)[d_col].nunique()
    incomplete = arms.index[arms < 2]
    n_incomplete = int(incomplete.shape[0])
    left_out = int(sub[b_col].isin(incomplete).sum())
    checks = [DesignCheck(
        name="every block has both arms",
        passed=n_incomplete == 0,
        detail="" if n_incomplete == 0 else
        f"{n_incomplete} of {arms.shape[0]} blocks have one arm only ({left_out} units)",
    )]
    if n_incomplete:
        if policy.missing == "error":
            return refuse(
                AbstentionReason.DESIGN_CHECK_FAILED,
                f"{n_incomplete} of {arms.shape[0]} blocks have only one arm ({left_out} units): "
                "a block without a comparison cannot contribute a within-block difference",
                (f"leave those blocks out upstream, or set policy.missing='drop' to analyse the "
                 f"{arms.shape[0] - n_incomplete} complete blocks (valid only if which arm went "
                 "unobserved is unrelated to the outcomes)",),
                q=query, cert=cert,
            )
        sub = sub.loc[~sub[b_col].isin(incomplete)]
        warnings = [*warnings, f"blocks with only one arm were left out ({n_incomplete} blocks, "
                    f"{left_out} units): the estimate is about the blocks in which both arms "
                    "were observed"]
    k = int(sub[b_col].nunique())
    n = len(sub)
    checks.append(DesignCheck(name="complete blocks", passed=k >= policy.min_per_arm,
                              detail=f"{k} blocks, {n} units"))
    if n < policy.min_n:
        return refuse(AbstentionReason.LOW_N, f"n={n} is below policy.min_n={policy.min_n}",
                      q=query, cert=cert)
    if k < policy.min_per_arm:
        return refuse(
            AbstentionReason.LOW_N,
            f"{k} complete blocks; at least {policy.min_per_arm} (policy.min_per_arm) are needed "
            "to estimate a between-block variance",
            ("more blocks, or analyse without `block` (the unblocked interval is valid, and "
             "wider)",),
            q=query, cert=cert,
        )

    prov["data_digest"] = data_digest(sub)
    level = policy.ci_level
    point, se, lo, hi, facts = _estimate(sub, b_col, d_col, y_col, level)
    y = sub[y_col].to_numpy(dtype=float)
    t = sub[d_col].to_numpy(dtype=float)
    rec = EstimateRecord(
        estimator=facts["estimator"],
        family="difference_in_means",
        estimand="ATE: E[Y(T=1) - Y(T=0)], per unit, compared within blocks",
        point=point,
        se=se,
        ci=Interval(level=level, lo=lo, hi=hi, method=facts["method"]),
        n_used=n,
        assumptions=ASSUMPTIONS,
        details={"blocks": float(k), "df": float(k - 1)},
    )
    refutations: list[RefutationResult] = []
    for nco in ncos:
        alpha = policy.refutation_alpha / len(ncos)
        if np.ptp(sub[nco].to_numpy(dtype=float)) == 0:
            continue
        npoint, _, _, _, nfacts = _estimate(sub, b_col, d_col, nco, level)
        p = nfacts["p_value"]
        refutations.append(RefutationResult(
            name=f"negative_control_outcome:{nco}",
            passed=p is None or p >= alpha,
            statistic=npoint,
            p_value=p,
            threshold=alpha,
            detail=f"blocked estimate of the 'effect' of {d_col} on {nco}, which the analyst "
            "declared it cannot affect (Bonferroni over negative controls)",
        ))
    sens = None
    if policy.sensitivity:
        ev = _e_values(sub, d_col, y_col, b_col, point, lo, hi, facts["binary"])
        if ev is not None:
            sens = Sensitivity(e_value=ev[0], e_value_ci=ev[1], e_value_method=ev[2])
    summary = BlockSummary(
        column=b_col,
        n_blocks=k,
        pairs=facts["pairs"],
        incomplete_blocks=n_incomplete,
        units_left_out=left_out,
        sd_difference=facts["sd_difference"],
        discordant_treated=facts.get("b"),
        discordant_control=facts.get("c"),
        mcnemar_exact_p=facts.get("mcnemar"),
        unblocked_se=neyman_se(y, t),
    )
    failed = [r.name for r in refutations if not r.passed]
    status = (ResultStatus.contested if failed
              else ResultStatus.provisional if unreviewed else ResultStatus.supported)
    if failed:
        warnings = [*warnings, f"falsification tests failed: {failed}"]
    elif unreviewed:
        warnings = [*warnings, f"graph contains unreviewed edges: {unreviewed}"]
    claim_fields: dict[str, tuple[float | None, str, str | None]] = {
        "effect": (rec.point, "/primary/point", None),
        "effect.se": (rec.se, "/primary/se", None),
        "effect.ci.lo": (lo, "/primary/ci/lo", None),
        "effect.ci.hi": (hi, "/primary/ci/hi", None),
        "ci.level": (level * 100, "/primary/ci/level", "%"),
        "n": (float(n), "/diagnostics/n_used", None),
        "blocks": (float(k), "/blocks/n_blocks", None),
    }
    if facts.get("b") is not None:
        claim_fields["discordant.treated"] = (float(facts["b"]), "/blocks/discordant_treated",
                                              None)
        claim_fields["discordant.control"] = (float(facts["c"]), "/blocks/discordant_control",
                                              None)
        claim_fields["mcnemar.p"] = (facts["mcnemar"], "/blocks/mcnemar_exact_p", None)
    if sens is not None:
        claim_fields["e_value"] = (sens.e_value, "/sensitivity/e_value", None)
        claim_fields["e_value.ci"] = (sens.e_value_ci, "/sensitivity/e_value_ci", None)
    result = CausalResult(
        status=status,
        query=query,
        certificate=cert,
        primary=rec,
        estimates=(rec,),
        diagnostics=Diagnostics(
            n_rows=len(frame),
            n_used=n,
            n_dropped_missing=na_rows,
            treatment_kind="binary",
            n_treated=int(t.sum()),
            n_control=int((1 - t).sum()),
            outcome_sd=float(np.std(y, ddof=1)),
            checks=tuple(checks),
        ),
        refutations=tuple(refutations),
        sensitivity=sens,
        blocks=summary,
        claims=build_claims(claim_fields),
        warnings=tuple(warnings),
        provenance=Provenance(**prov),
    )
    return result.model_copy(
        update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))}
    )

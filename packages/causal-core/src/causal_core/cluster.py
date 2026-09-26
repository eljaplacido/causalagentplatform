"""
Cluster-randomized designs: randomize services, measure requests.

In operations the thing randomized is rarely the thing measured. A change ships to some
*services*; latency is measured per *request*. Requests within one service share its
load and its bad afternoon, so they are not independent, and an interval computed as if
they were is too narrow by roughly ``sqrt(1 + (m − 1)·ICC)`` -- about four times for
clusters of fifty at an intra-cluster correlation of 0.3.

The estimate is the unit-weighted difference in means (the effect on a request,
averaged over requests). Its standard error sums each unit's influence-function value
within its cluster, so the clusters -- not the units -- are the independent
observations; a CR2 (Bell–McCaffrey) adjustment, which inflates each cluster's residual
sum by ``1/sqrt(1 − m_j/n_arm)`` so that large, high-leverage clusters are not
under-counted, and a t distribution on ``K − 2`` degrees of freedom account for having
few of them.

**Switchback designs** use the time block as the cluster: one system, one arm per block,
blocks randomized. A declared ``washout`` drops the first minutes of *every* block -- the
carryover while the system settles after a switch -- and each block then counts once: the
effect is the difference of the arms' block means, with a Welch variance across blocks on
t(K − 2). Trimming only the blocks after a switch, weighted by what remains, made each
block depend on its neighbour's arm and under-covered (T8, development seeds: 0.89–0.91).
A carryover check asks whether the effect is still moving after the washout: in each switched
block, the least-squares slope of the outcome over the retained minutes; the mean slope of
blocks switched into treatment against that of blocks switched out (a carryover still settling
makes the effect rise through one and fall through the other), Welch, t(K_s − 2) over the K_s
switched blocks, at ``policy.carryover_alpha``. A failure makes the result ``contested``: the
estimate and interval are reported, the causal reading is withheld. T8 v2 gates it -- power
0.8 against a carryover the washout does not cover and large enough to matter, false alarms
at most 0.10 where the washout covers it -- and records what it cannot see: a carryover whose
bias is a fraction of the estimate's standard error (T8's long_carryover, power about 0.1). No
check on the retained minutes can: the residual there is small against minute-level noise.

Refused: treatment that varies within a cluster (then the design is not what this
assumes), fewer than ``min_clusters_per_arm`` clusters in either arm, and cluster sizes
so variable (coefficient of variation above ``max_cluster_size_cv``) that a few clusters
dominate: T7 v2 measured coverage 0.879 at a CV of 0.71, which no variance correction
repaired.
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
    Strategy,
)

F = NDArray[np.float64]

ESTIMATOR = "difference_in_means_cluster_robust"
METHOD = "t(K-2); CR2 (Bell-McCaffrey) cluster-robust, residuals summed within clusters"
ASSUMPTIONS = (
    "clusters assigned to treatment independently of potential outcomes (randomization)",
    "no interference between clusters; units within a cluster may be arbitrarily correlated",
    "treatment is constant within a cluster",
)


def point_estimate(y: F, t: F, cluster: NDArray[Any]) -> float:
    """The unit-weighted difference in means. A seam: `cluster_weighted_estimate` swaps it."""
    return float(y[t == 1].mean() - y[t == 0].mean())


def cluster_variance(y: F, t: F, cluster: NDArray[Any], k: int) -> float:
    """CR2 variance of the difference in means, clusters as the independent observations.

    For y ~ 1 + t the CR2 adjustment has a closed form: within arm a, cluster j's residual
    sum S_j is scaled by (1 - m_j/n_a)^(-1/2). A seam: `unit_level_se` treats every unit
    as independent instead.
    """
    var = 0.0
    for arm in (1.0, 0.0):
        mask = t == arm
        ya, ca = y[mask], cluster[mask]
        n_a = ya.shape[0]
        resid = pd.Series(ya - ya.mean())
        sums = resid.groupby(ca).sum().to_numpy()
        sizes = resid.groupby(ca).size().to_numpy()
        var += float(np.sum(sums**2 / (1 - sizes / n_a))) / n_a**2
    return var


def _switched(sub: pd.DataFrame, block: str, d_col: str, time: str) -> pd.Series:
    """Per block: whether its arm differs from the previous block's (in time order)."""
    starts = sub.groupby(block)[time].min().sort_values()
    arm = sub.groupby(block)[d_col].first().reindex(starts.index)
    return (arm != arm.shift()).fillna(False).astype(bool) & arm.shift().notna()


def apply_washout(sub: pd.DataFrame, block: str, d_col: str, time: str,
                  washout: float) -> pd.DataFrame:
    """Drop the first `washout` time units of every block. A seam: `no_washout` skips it.

    Every block, not only those after a switch: otherwise a block's retained window depends
    on its neighbour's arm, and the blocks are no longer independent observations.
    """
    since = sub[time] - sub.groupby(block)[time].transform("min")
    return sub.loc[since >= washout]


def block_means(sub: pd.DataFrame, block: str, d_col: str, y_col: str) -> pd.DataFrame:
    return sub.groupby(block).agg(y=(y_col, "mean"), t=(d_col, "first"))


def block_variance(means: pd.DataFrame) -> float:
    """Welch variance of the difference of block means. A seam: `minute_level_se`."""
    a, b = means.loc[means["t"] == 1, "y"], means.loc[means["t"] == 0, "y"]
    return float(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def block_slopes(frame: pd.DataFrame, block: str, since: str, y_col: str,
                 start: float) -> pd.Series:
    """Per block: least-squares slope of the outcome on time since the block began, over
    ``since >= start``. A seam: `check_includes_washout` fits over the whole block."""
    win = frame.loc[frame[since] >= start, [block, since, y_col]]
    g = win.groupby(block)
    sc = win[since] - g[since].transform("mean")
    yc = win[y_col] - g[y_col].transform("mean")
    sxx = (sc * sc).groupby(win[block]).sum()
    sxy = (sc * yc).groupby(win[block]).sum()
    return (sxy / sxx.where(sxx > 0)).dropna()


def carryover_contrast(into: pd.Series, out: pd.Series) -> tuple[float, float, int]:
    """Into- minus out-of-treatment mean slope: (difference, Welch t, df = K_s - 2).
    A seam: `carryover_check_blind` pools the two directions."""
    diff = float(into.mean() - out.mean())
    se = math.sqrt(float(into.var(ddof=1)) / len(into) + float(out.var(ddof=1)) / len(out))
    return diff, (diff / se if se > 0 else 0.0), len(into) + len(out) - 2


def carryover_check(sub: pd.DataFrame, block: str, d_col: str, y_col: str, time: str,
                    washout: float, alpha: float) -> DesignCheck:
    """Is the effect still moving after the washout? Switched blocks only.

    With the system settled by the end of the washout, the outcome's slope over the rest
    of a block has the same expectation in blocks switched into treatment as in blocks
    switched out (the daily cycle is common to both; which blocks switch is randomized).
    A carryover still settling makes it rise through one and fall through the other.
    The difference of the two mean slopes is tested with a Welch variance across blocks on
    t(K_s - 2) -- the block, as for the estimate, the independent observation.
    """
    switched = _switched(sub, block, d_col, time)
    since = sub[time] - sub.groupby(block)[time].transform("min")
    frame = sub.assign(_since=since)
    frame = frame[frame[block].map(switched).astype(bool)]
    slopes = block_slopes(frame, block, "_since", y_col, washout)
    arm = frame.groupby(block)[d_col].first().reindex(slopes.index)
    into, out = slopes[arm == 1], slopes[arm == 0]
    name = "no carryover beyond the washout"
    if len(into) < 2 or len(out) < 2:
        return DesignCheck(name=name, passed=True,
                           detail="not run: fewer than two switched blocks in each direction")
    diff, tstat, df = carryover_contrast(into, out)
    p = float(2 * stats.t.sf(abs(tstat), df)) if df > 0 else 1.0
    return DesignCheck(
        name=name,
        passed=p > alpha,
        detail=f"slope of the outcome after the washout, blocks switched into minus out of "
        f"treatment: {diff:.4g} per unit of {time} (t={tstat:.2f} on {df} df, p={p:.2g}, "
        f"level {alpha}; {len(into)} + {len(out)} switched blocks)",
    )


def carryover_status(status: ResultStatus, check: DesignCheck | None) -> ResultStatus:
    """A failed carryover check makes the result contested. A seam: `flag_not_gating`."""
    if check is not None and not check.passed:
        return ResultStatus.contested
    return status


def icc(y: F, t: F, cluster: NDArray[Any]) -> float | None:
    """One-way ANOVA estimate of the intra-cluster correlation, pooled within arms."""
    frame = pd.DataFrame({"y": y, "t": t, "c": cluster})
    msb_num = msw_num = 0.0
    df_b = df_w = 0
    sizes: list[int] = []
    for _, arm in frame.groupby("t"):
        groups = arm.groupby("c")["y"]
        means, counts = groups.mean(), groups.size()
        grand = arm["y"].mean()
        msb_num += float((counts * (means - grand) ** 2).sum())
        msw_num += float(((arm["y"] - arm["c"].map(means)) ** 2).sum())
        df_b += len(counts) - 1
        df_w += len(arm) - len(counts)
        sizes.extend(counts.tolist())
    if df_b <= 0 or df_w <= 0:
        return None
    msb, msw = msb_num / df_b, msw_num / df_w
    n_total, k = sum(sizes), len(sizes)
    m0 = (n_total - sum(s * s for s in sizes) / n_total) / (k - 1)
    var_b = max((msb - msw) / m0, 0.0)
    return float(var_b / (var_b + msw)) if var_b + msw > 0 else None


def analyze_cluster(
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
            "cluster-robust analysis here is for cluster-RANDOMIZED designs (design='randomized', "
            "a graph with no causes of the treatment)",
            q=query,
            cert=cert,
        )
    unsupported = {
        "adjust_for": query.adjust_for,
        "segment_by": query.segment_by,
        "sequence": query.sequence,
        "negative_control_outcomes": query.negative_control_outcomes,
        "treatment_levels": query.treatment_levels,
        "unit": query.unit,
        "time (without washout)": query.time if query.washout is None else None,
    }
    given = sorted(k for k, v in unsupported.items() if v)
    if given:
        return refuse(AbstentionReason.INVALID_INPUT,
                      f"not supported with cluster randomization in this version: {given}",
                      q=query, cert=cert)
    if query.washout is not None and (query.time is None or query.time not in frame.columns):
        return refuse(AbstentionReason.INVALID_INPUT,
                      "a washout needs `time`, the column giving time within the stream",
                      q=query, cert=cert)
    c_col, d_col, y_col = query.cluster, query.treatment, query.outcome
    assert c_col is not None
    if c_col not in frame.columns:
        return refuse(AbstentionReason.INVALID_INPUT, f"cluster {c_col!r} is not a column",
                      q=query, cert=cert)
    cols = [c_col, d_col, y_col] + ([query.time] if query.washout is not None and query.time
                                     else [])
    sub = frame[cols]
    if sub.isna().any().any():
        return refuse(AbstentionReason.INVALID_INPUT, "missing values in the analysis columns",
                      q=query, cert=cert)
    try:
        binary = {float(v) for v in pd.unique(sub[d_col])} <= {0.0, 1.0}
    except (TypeError, ValueError):
        binary = False
    if not binary:
        return refuse(AbstentionReason.INVALID_INPUT, f"treatment {d_col!r} must be binary (0/1)",
                      q=query, cert=cert)
    varying = int((sub.groupby(c_col)[d_col].nunique() > 1).sum())
    checks = [DesignCheck(
        name="treatment is constant within each cluster",
        passed=varying == 0,
        detail="" if varying == 0 else f"{varying} clusters mix treated and untreated units",
    )]
    if varying:
        return refuse(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"treatment varies within {varying} clusters: this is not a cluster-randomized "
            "design, and cluster-robust errors would not make it one",
            q=query,
            cert=cert,
        )
    arm = sub.groupby(c_col)[d_col].first()
    k1, k0 = int((arm == 1).sum()), int((arm == 0).sum())
    k = k1 + k0
    if min(k1, k0) < policy.min_clusters_per_arm:
        return refuse(
            AbstentionReason.LOW_N,
            f"{k1} treated and {k0} control clusters; at least {policy.min_clusters_per_arm} per "
            "arm are needed to estimate a cluster-level variance",
            ("more clusters in the smaller arm",),
            q=query,
            cert=cert,
        )
    carryover: DesignCheck | None = None
    if query.washout is not None:
        assert query.time is not None
        kept = apply_washout(sub, c_col, d_col, query.time, query.washout)
        emptied = sorted(set(sub[c_col]) - set(kept[c_col]))
        if emptied:
            return refuse(
                AbstentionReason.INVALID_INPUT,
                f"a washout of {query.washout} removes every observation of {len(emptied)} "
                "switched blocks: blocks must be longer than the washout",
                q=query,
                cert=cert,
            )
        carryover = carryover_check(sub, c_col, d_col, y_col, query.time, query.washout,
                                    policy.carryover_alpha)
        sub = kept
    sizes = sub.groupby(c_col).size().to_numpy(dtype=float)
    cv = float(sizes.std() / sizes.mean())
    checks.append(DesignCheck(
        name="no few clusters dominate",
        passed=cv <= policy.max_cluster_size_cv,
        detail=f"cluster-size coefficient of variation {cv:.2f} (limit "
        f"{policy.max_cluster_size_cv})",
    ))
    if cv > policy.max_cluster_size_cv:
        return refuse(
            AbstentionReason.DESIGN_CHECK_FAILED,
            f"cluster sizes vary too much (coefficient of variation {cv:.2f} > "
            f"{policy.max_cluster_size_cv}): a few large clusters dominate a difference in means "
            f"over {k} clusters, and its interval would not cover",
            ("more clusters, randomization stratified by cluster size, or an analysis of cluster "
             "totals",),
            q=query,
            cert=cert,
        )
    if carryover is not None:
        checks.append(carryover)
        if not carryover.passed:
            warnings = [*warnings, "carryover beyond the washout, so the result is contested: "
                        f"{carryover.detail}"]
    prov["data_digest"] = data_digest(sub)
    y = sub[y_col].to_numpy(dtype=float)
    t = sub[d_col].to_numpy(dtype=float)
    cl = sub[c_col].to_numpy()
    if query.washout is not None:
        means = block_means(sub, c_col, d_col, y_col)
        treated_means = means.loc[means["t"] == 1, "y"]
        control_means = means.loc[means["t"] == 0, "y"]
        point = float(treated_means.mean() - control_means.mean())
        se = math.sqrt(block_variance(means))
    else:
        point = point_estimate(y, t, cl)
        se = math.sqrt(cluster_variance(y, t, cl, k))
    df = k - 2
    crit = float(stats.t.ppf(0.5 + policy.ci_level / 2, df))
    rho = icc(y, t, cl)
    rec = EstimateRecord(
        estimator=ESTIMATOR,
        family="difference_in_means",
        estimand="ate",
        point=point,
        se=se,
        ci=Interval(level=policy.ci_level, lo=point - crit * se, hi=point + crit * se,
                    method=METHOD),
        n_used=int(y.shape[0]),
        assumptions=ASSUMPTIONS,
        details={"clusters": float(k), "df": float(df)},
    )
    status = ResultStatus.provisional if unreviewed else ResultStatus.supported
    if unreviewed:
        warnings = [*warnings, f"graph contains unreviewed edges: {unreviewed}"]
    status = carryover_status(status, carryover)
    result = CausalResult(
        status=status,
        query=query,
        certificate=cert,
        primary=rec,
        estimates=(rec,),
        diagnostics=Diagnostics(
            n_rows=len(frame),
            n_used=int(y.shape[0]),
            treatment_kind="binary",
            n_treated=int(t.sum()),
            n_control=int((1 - t).sum()),
            outcome_sd=float(np.std(y, ddof=1)),
            n_clusters=k,
            icc=rho,
            checks=tuple(checks),
        ),
        claims=build_claims({
            "effect": (rec.point, "/primary/point", None),
            "effect.se": (rec.se, "/primary/se", None),
            "effect.ci.lo": (rec.ci.lo if rec.ci else None, "/primary/ci/lo", None),
            "effect.ci.hi": (rec.ci.hi if rec.ci else None, "/primary/ci/hi", None),
            "ci.level": (policy.ci_level * 100, "/primary/ci/level", "%"),
            "n": (float(y.shape[0]), "/primary/n_used", None),
            "clusters": (float(k), "/diagnostics/n_clusters", None),
        }),
        warnings=tuple(warnings),
        provenance=Provenance(**prov),
    )
    return result.model_copy(
        update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))}
    )

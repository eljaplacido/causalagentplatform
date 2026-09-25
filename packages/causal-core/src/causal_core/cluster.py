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
observations; a CR1 correction ``K/(K − 1)`` and a t distribution on ``K − 2`` degrees
of freedom account for having few of them.

Refused: treatment that varies within a cluster (then the design is not what this
assumes), and fewer than ``min_clusters_per_arm`` clusters in either arm.
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
METHOD = "t(K-2); CR1 cluster-robust, influence function summed within clusters"
ASSUMPTIONS = (
    "clusters assigned to treatment independently of potential outcomes (randomization)",
    "no interference between clusters; units within a cluster may be arbitrarily correlated",
    "treatment is constant within a cluster",
)


def point_estimate(y: F, t: F, cluster: NDArray[Any]) -> float:
    """The unit-weighted difference in means. A seam: `cluster_weighted_estimate` swaps it."""
    return float(y[t == 1].mean() - y[t == 0].mean())


def influence(y: F, t: F) -> F:
    n1, n = t.sum(), t.shape[0]
    p1, p0 = n1 / n, 1 - n1 / n
    m1, m0 = y[t == 1].mean(), y[t == 0].mean()
    psi: F = t * (y - m1) / p1 - (1 - t) * (y - m0) / p0
    return psi


def cluster_variance(psi: F, cluster: NDArray[Any], k: int) -> float:
    """CR1 variance of the mean of ψ, clusters as the independent observations.

    A seam: `unit_level_se` treats every unit as independent instead.
    """
    sums = pd.Series(psi).groupby(cluster).sum().to_numpy()
    n = psi.shape[0]
    return float((k / (k - 1)) * np.sum(sums**2) / n**2)


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
        "unit/time": query.unit or query.time,
    }
    given = sorted(k for k, v in unsupported.items() if v)
    if given:
        return refuse(AbstentionReason.INVALID_INPUT,
                      f"not supported with cluster randomization in this version: {given}",
                      q=query, cert=cert)
    c_col, d_col, y_col = query.cluster, query.treatment, query.outcome
    assert c_col is not None
    if c_col not in frame.columns:
        return refuse(AbstentionReason.INVALID_INPUT, f"cluster {c_col!r} is not a column",
                      q=query, cert=cert)
    sub = frame[[c_col, d_col, y_col]]
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
    prov["data_digest"] = data_digest(sub)
    y = sub[y_col].to_numpy(dtype=float)
    t = sub[d_col].to_numpy(dtype=float)
    cl = sub[c_col].to_numpy()
    point = point_estimate(y, t, cl)
    se = math.sqrt(cluster_variance(influence(y, t), cl, k))
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

"""
Overlap: where in the covariates does one arm never occur? (Pre-registered as T10.)

The logistic check in :mod:`causal_core.design` reads positivity off one main-effects
propensity model, which cannot express a stratum defined by several covariates jointly or
a threshold on a continuous one. This module looks for such regions directly, honestly:

* the units are split in two; on one half a shallow classification tree for the treatment
  is grown, and its leaves -- conjunctions of conditions on the covariates -- are candidate
  regions;
* the other half is routed into those leaves, and a leaf is a *violation region* for an
  arm when the one-sided Clopper-Pearson upper bound on that arm's probability, from the
  held-out units alone, is below ``policy.overlap_region_bound``; then the halves swap.

The data that chose a region never judge it. A leaf needs ``min_region_units`` held-out
units before an absence can be told from chance at all, so smaller ones are not judged.

Nothing here trims. What the regions mean for an answer -- a refusal, or a warning -- is
decided in :mod:`causal_core.design`, and the regions are reported so the caller can ask
the narrower question explicitly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy import stats

from causal_core.contract import AnalysisPolicy, OverlapRegion

F = NDArray[np.float64]
Idx = NDArray[np.intp]

MAX_REGIONS_REPORTED = 10


def min_region_units(policy: AnalysisPolicy) -> int:
    """The fewest held-out units in which the absence of an arm can reach the bound."""
    bound, alpha = policy.overlap_region_bound, policy.overlap_region_alpha
    if bound <= 0:
        return 0
    return math.ceil(math.log(alpha) / math.log(1 - bound))


# ── The tree ──────────────────────────────────────────────────────────────────


@dataclass
class Node:
    feature: int = -1
    threshold: float = 0.0
    left: Node | None = None
    right: Node | None = None

    @property
    def is_leaf(self) -> bool:
        return self.left is None


def _best_split(x: F, t: F, min_leaf: int) -> tuple[float, float] | None:
    """(impurity after, threshold) of the best Gini split on one feature, or None."""
    order = np.argsort(x, kind="stable")
    xs, ts = x[order], t[order]
    n = len(ts)
    c1 = np.cumsum(ts)[:-1]
    nl = np.arange(1, n, dtype=float)
    nr = n - nl
    tot = float(ts.sum())
    pl, pr = c1 / nl, (tot - c1) / nr
    imp = nl * pl * (1 - pl) + nr * pr * (1 - pr)
    ok = (xs[:-1] < xs[1:]) & (nl >= min_leaf) & (nr >= min_leaf)
    if not ok.any():
        return None
    imp = np.where(ok, imp, np.inf)
    i = int(np.argmin(imp))
    return float(imp[i]), float((xs[i] + xs[i + 1]) / 2)


def grow(X: F, t: F, idx: Idx, depth: int, min_leaf: int) -> Node:
    """A classification tree for ``t`` on ``X[idx]``: Gini splits, a node split only if it helps."""
    tt = t[idx]
    n = len(idx)
    p = float(tt.mean()) if n else 0.0
    parent = n * p * (1 - p)
    if depth <= 0 or parent == 0 or n < 2 * min_leaf:
        return Node()
    best: tuple[float, int, float] | None = None
    for j in range(X.shape[1]):
        r = _best_split(X[idx, j], tt, min_leaf)
        if r is not None and r[0] < parent - 1e-9 and (best is None or r[0] < best[0]):
            best = (r[0], j, r[1])
    if best is None:
        return Node()
    _, j, thr = best
    go_left = X[idx, j] <= thr
    return Node(
        feature=j,
        threshold=thr,
        left=grow(X, t, idx[go_left], depth - 1, min_leaf),
        right=grow(X, t, idx[~go_left], depth - 1, min_leaf),
    )


Condition = tuple[int, bool, float]  # (feature, goes left i.e. <=, threshold)


def route(node: Node, X: F, idx: Idx, path: tuple[Condition, ...] = ()) -> list[
    tuple[tuple[Condition, ...], Idx]
]:
    """The leaves of ``node`` with the units of ``idx`` that fall into each."""
    if node.is_leaf:
        return [(path, idx)]
    assert node.left is not None and node.right is not None
    go_left = X[idx, node.feature] <= node.threshold
    return route(node.left, X, idx[go_left], (*path, (node.feature, True, node.threshold))) + (
        route(node.right, X, idx[~go_left], (*path, (node.feature, False, node.threshold)))
    )


def rule(path: Sequence[Condition], names: Sequence[str], binary: Sequence[bool]) -> str:
    """A leaf as a readable conjunction, one clause per covariate; 0/1 covariates as equalities."""
    if not path:
        return "all units"
    lower: dict[int, float] = {}
    upper: dict[int, float] = {}
    for j, left, thr in path:
        if left:
            upper[j] = min(upper.get(j, np.inf), thr)
        else:
            lower[j] = max(lower.get(j, -np.inf), thr)
    parts = []
    for j in sorted(set(lower) | set(upper)):
        lo, hi = lower.get(j), upper.get(j)
        if binary[j]:
            parts.append(f"{names[j]} = {1 if lo is not None else 0}")
        elif lo is not None and hi is not None:
            parts.append(f"{lo:.4g} < {names[j]} <= {hi:.4g}")
        elif lo is not None:
            parts.append(f"{names[j]} > {lo:.4g}")
        else:
            parts.append(f"{names[j]} <= {hi:.4g}")
    return " and ".join(parts)


# ── Seams (each one a place a plausible wrong implementation would differ) ─────


def honest_pairs(n: int, rng: np.random.Generator) -> list[tuple[Idx, Idx]]:
    """(grow on, judge on) index pairs: two halves, each judged by the other's tree."""
    perm = rng.permutation(n)
    a, b = perm[: n // 2], perm[n // 2 :]
    return [(a, b), (b, a)]


def candidate_leaves(
    X: F, t: F, grow_on: Idx, judge_on: Idx, policy: AnalysisPolicy, min_leaf: int
) -> list[tuple[tuple[Condition, ...], Idx]]:
    """Regions grown on ``grow_on``, with the ``judge_on`` units that fall into each."""
    tree = grow(X, t, grow_on, policy.overlap_region_depth, min_leaf)
    return route(tree, X, judge_on)


def arms_to_check(estimand: str) -> tuple[int, ...]:
    """The ATE needs both arms everywhere; the ATT only needs controls where there are treated."""
    return (0,) if estimand == "att" else (0, 1)


def upper_bound(k: int, m: int, alpha: float) -> float:
    """One-sided (1 - alpha) Clopper-Pearson upper bound on a probability, k of m."""
    if k >= m:
        return 1.0
    return float(stats.beta.ppf(1 - alpha, k + 1, m - k))


def region_refuses(share: float, policy: AnalysisPolicy) -> bool:
    """Above the tolerance the logistic check already applies, a region share refuses."""
    return share > policy.max_extreme_propensity_share


# ── The check ─────────────────────────────────────────────────────────────────


@dataclass
class RegionResult:
    share: float  # ATE: of all units; ATT: of treated units
    regions: list[OverlapRegion] = field(default_factory=list)
    flagged: NDArray[np.bool_] | None = None  # units in a violation region (row positions)
    min_units: int = 0
    ran: bool = True


def overlap_regions(
    X: F,
    t: F,
    names: Sequence[str],
    policy: AnalysisPolicy,
    estimand: str,
) -> RegionResult:
    """Honest overlap regions for a binary treatment ``t`` (0/1) on covariates ``X``."""
    n = len(t)
    n_min = min_region_units(policy)
    flagged = np.zeros(n, dtype=bool)
    if n_min <= 0 or X.shape[1] == 0 or n < 2 * n_min:
        return RegionResult(0.0, [], flagged, n_min, ran=False)
    binary = [bool(np.isin(np.unique(X[:, j]), (0.0, 1.0)).all()) for j in range(X.shape[1])]
    rng = np.random.default_rng([policy.seed, 0x0E71A9])
    arms = arms_to_check(estimand)
    found: list[OverlapRegion] = []
    for grow_on, judge_on in honest_pairs(n, rng):
        for path, idx in candidate_leaves(X, t, grow_on, judge_on, policy, n_min):
            m = len(idx)
            if m == 0:
                continue
            k1 = int(t[idx].sum())
            for arm in arms:
                k = k1 if arm == 1 else m - k1
                ub = upper_bound(k, m, policy.overlap_region_alpha)
                if ub < policy.overlap_region_bound:
                    flagged[idx] = True
                    found.append(
                        OverlapRegion(
                            rule=rule(path, names, binary),
                            n_units=m,
                            n_treated=k1,
                            missing_arm="treated" if arm == 1 else "control",
                            upper_bound=ub,
                        )
                    )
    if estimand == "att":
        treated = t == 1
        share = float(flagged[treated].mean()) if treated.any() else 0.0
    else:
        share = float(flagged.mean())
    found.sort(key=lambda r: -r.n_units)
    return RegionResult(share, found[:MAX_REGIONS_REPORTED], flagged, n_min)


def describe(regions: Sequence[OverlapRegion], k: int = 3) -> str:
    """The first ``k`` distinct regions, for a message."""
    seen: dict[str, str] = {}
    for r in regions:
        seen.setdefault(r.rule, r.missing_arm)
    return "; ".join(f"{rule} (no {arm} units)" for rule, arm in list(seen.items())[:k])


def limited_overlap_share(e: F, t: F, bounds: tuple[float, float], estimand: str) -> float:
    """Share of units whose propensity is outside ``bounds`` (ATT: treated units above the top)."""
    lo, hi = bounds
    if estimand == "att":
        treated = t == 1
        return float(np.mean(e[treated] > hi)) if treated.any() else 0.0
    return float(np.mean((e < lo) | (e > hi)))

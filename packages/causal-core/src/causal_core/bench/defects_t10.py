"""
Seeded defects for T10: plausible ways to check positivity wrongly.

Each patches one seam of ``causal_core.overlap`` (called through its own namespace), or,
for silent trimming, the pipeline's call into the design stage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd

import causal_core.design as design_mod
import causal_core.overlap as overlap_mod
import causal_core.pipeline as pipeline_mod
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalQuery
from causal_core.contract import IdentificationCertificate as Cert


@contextmanager
def no_region_check() -> Iterator[None]:
    """The kernel as it was: positivity read off the logistic propensity alone."""

    def fake(X: Any, t: Any, names: Any, policy: AnalysisPolicy,
             estimand: str) -> overlap_mod.RegionResult:
        return overlap_mod.RegionResult(0.0, [], np.zeros(len(t), dtype=bool),
                                        overlap_mod.min_region_units(policy), ran=False)

    with mock.patch.object(overlap_mod, "overlap_regions", fake):
        yield


@contextmanager
def marginal_only() -> Iterator[None]:
    """Regions searched one covariate at a time -- a depth-1 split per covariate, judged the
    same honest way. A stratum defined by several covariates jointly never appears."""

    def fake(X: Any, t: Any, grow_on: Any, judge_on: Any, policy: AnalysisPolicy,
             min_leaf: int) -> list[Any]:
        leaves = []
        for j in range(X.shape[1]):
            col = X[:, [j]]
            tree = overlap_mod.grow(col, t, grow_on, 1, min_leaf)
            for path, idx in overlap_mod.route(tree, col, judge_on):
                leaves.append((tuple((j, left, thr) for _, left, thr in path), idx))
        return leaves

    with mock.patch.object(overlap_mod, "candidate_leaves", fake):
        yield


@contextmanager
def in_sample_regions() -> Iterator[None]:
    """Each tree's leaves judged on the half that grew them: the search's selection read as
    evidence."""

    def fake(n: int, rng: np.random.Generator) -> list[Any]:
        perm = rng.permutation(n)
        a, b = perm[: n // 2], perm[n // 2 :]
        return [(a, a), (b, b)]

    with mock.patch.object(overlap_mod, "honest_pairs", fake):
        yield


@contextmanager
def share_threshold_ignored() -> Iterator[None]:
    """Any violation region refuses, whatever share of the units it holds."""

    def fake(share: float, policy: AnalysisPolicy) -> bool:
        return share > 0

    with mock.patch.object(overlap_mod, "region_refuses", fake):
        yield


@contextmanager
def att_checks_both_arms() -> Iterator[None]:
    """The ATT held to the ATE's two-way requirement: the region check run as for the ATE, so a
    never-treated region -- which carries no weight in the ATT -- refuses it too."""
    real = overlap_mod.overlap_regions

    def fake(X: Any, t: Any, names: Any, policy: AnalysisPolicy,
             estimand: str) -> overlap_mod.RegionResult:
        return real(X, t, names, policy, "ate")

    with mock.patch.object(overlap_mod, "overlap_regions", fake):
        yield


@contextmanager
def silent_trimming() -> Iterator[None]:
    """Units in violation regions dropped and the rest analysed, still called the ATE."""
    real = design_mod.prepare  # what the pipeline calls

    def fake(data: pd.DataFrame, query: CausalQuery, cert: Cert,
             policy: AnalysisPolicy) -> design_mod.DesignResult:
        res = real(data, query, cert, policy)
        flagged = res.overlap_flagged
        if (res.abstention is None or res.abstention[0] is not AbstentionReason.POSITIVITY
                or flagged is None or not flagged.any()):
            return res
        kept = data.drop(index=res.frame.index[flagged])
        return real(kept, query, cert, policy.model_copy(update={"overlap_region_bound": 0.0}))

    with mock.patch.object(pipeline_mod, "prepare", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "no_region_check": no_region_check,
    "marginal_only": marginal_only,
    "in_sample_regions": in_sample_regions,
    "share_threshold_ignored": share_threshold_ignored,
    "att_checks_both_arms": att_checks_both_arms,
    "silent_trimming": silent_trimming,
}

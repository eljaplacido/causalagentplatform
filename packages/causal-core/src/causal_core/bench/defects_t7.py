"""
Seeded defects for T7: plausible ways to analyse a cluster-randomized experiment wrongly.

Each patches one seam of ``causal_core.cluster``, which calls its seams through its own
module namespace, so the patch reaches the analysis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd

import causal_core.cluster as cluster_mod


@contextmanager
def unit_level_se() -> Iterator[None]:
    """Every unit counted as an independent observation -- the usual mistake."""

    def fake(y: np.ndarray, t: np.ndarray, cluster: np.ndarray, k: int) -> float:
        y1, y0 = y[t == 1], y[t == 0]
        return float(np.var(y1, ddof=1) / y1.shape[0] + np.var(y0, ddof=1) / y0.shape[0])

    with mock.patch.object(cluster_mod, "cluster_variance", fake):
        yield


@contextmanager
def cluster_weighted_estimate() -> Iterator[None]:
    """The average of cluster means: a cluster-weighted estimand, not the per-unit one."""

    def fake(y: np.ndarray, t: np.ndarray, cluster: np.ndarray) -> float:
        means = pd.DataFrame({"y": y, "t": t, "c": cluster}).groupby("c").mean()
        treated, control = means.loc[means["t"] == 1, "y"], means.loc[means["t"] == 0, "y"]
        return float(treated.mean() - control.mean())

    with mock.patch.object(cluster_mod, "point_estimate", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "unit_level_se": unit_level_se,
    "cluster_weighted_estimate": cluster_weighted_estimate,
}

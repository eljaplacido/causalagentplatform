"""
Seeded defects for T8: plausible ways to analyse a switchback experiment wrongly.

Each patches one seam of ``causal_core.cluster``, called through its own namespace.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import pandas as pd

import causal_core.cluster as cluster_mod


@contextmanager
def no_washout() -> Iterator[None]:
    """The settling-in after each switch is kept, as if the system responded instantly."""

    def fake(sub: pd.DataFrame, block: str, d_col: str, time: str,
             washout: float) -> pd.DataFrame:
        return sub

    with mock.patch.object(cluster_mod, "apply_washout", fake):
        yield


@contextmanager
def minute_level_se() -> Iterator[None]:
    """Every minute an independent observation -- the dashboard's t-test."""

    captured: dict[str, pd.DataFrame] = {}
    real_means = cluster_mod.block_means

    def spy(sub: pd.DataFrame, block: str, d_col: str, y_col: str) -> pd.DataFrame:
        captured["minutes"] = sub[[d_col, y_col]].rename(columns={d_col: "t", y_col: "y"})
        return real_means(sub, block, d_col, y_col)

    def fake(means: pd.DataFrame) -> float:
        m = captured["minutes"]
        y1, y0 = m.loc[m["t"] == 1, "y"], m.loc[m["t"] == 0, "y"]
        return float(y1.var(ddof=1) / len(y1) + y0.var(ddof=1) / len(y0))

    with mock.patch.object(cluster_mod, "block_means", spy), \
            mock.patch.object(cluster_mod, "block_variance", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "no_washout": no_washout,
    "minute_level_se": minute_level_se,
}

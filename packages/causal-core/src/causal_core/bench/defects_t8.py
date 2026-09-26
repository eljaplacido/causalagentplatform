"""
Seeded defects for T8: plausible ways to analyse a switchback experiment wrongly.

Each patches one seam of ``causal_core.cluster``, called through its own namespace.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import pandas as pd

import causal_core.cluster as cluster_mod
from causal_core.contract import DesignCheck, ResultStatus


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


@contextmanager
def carryover_check_blind() -> Iterator[None]:
    """"Is the effect still moving after a switch?" asked of all switched blocks at once:
    the mean slope tested against zero, not signed by the direction of the switch, so
    the effect arriving in blocks switched in cancels the effect leaving blocks switched out."""

    def fake(into: pd.Series, out: pd.Series) -> tuple[float, float, int]:
        pooled = pd.concat([into, out])
        mean = float(pooled.mean())
        se = float(pooled.std(ddof=1)) / math.sqrt(len(pooled))
        return mean, (mean / se if se > 0 else 0.0), len(pooled) - 1

    with mock.patch.object(cluster_mod, "carryover_contrast", fake):
        yield


@contextmanager
def check_includes_washout() -> Iterator[None]:
    """The slopes are fitted over the whole block, washout included -- where the carryover
    the washout was declared to absorb is."""
    real = cluster_mod.block_slopes

    def fake(frame: pd.DataFrame, block: str, since: str, y_col: str,
             start: float) -> pd.Series:
        return real(frame, block, since, y_col, 0.0)

    with mock.patch.object(cluster_mod, "block_slopes", fake):
        yield


@contextmanager
def flag_not_gating() -> Iterator[None]:
    """The carryover check is computed and reported, and the status ignores it (T8 v1)."""

    def fake(status: ResultStatus, check: DesignCheck | None) -> ResultStatus:
        return status

    with mock.patch.object(cluster_mod, "carryover_status", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "no_washout": no_washout,
    "minute_level_se": minute_level_se,
    "carryover_check_blind": carryover_check_blind,
    "check_includes_washout": check_includes_washout,
    "flag_not_gating": flag_not_gating,
}

"""
Seeded defects for T11: plausible ways to analyse a blocked or paired experiment wrongly.

Each patches one seam of ``causal_core.blocked``, which calls its seams through its own
module namespace, so the patch reaches the analysis.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
from scipy import stats

import causal_core.blocked as blocked_mod


@contextmanager
def unpaired_se() -> Iterator[None]:
    """The variance ignores the blocks: every unit an independent observation (Neyman)."""

    def fake(y: np.ndarray, t: np.ndarray, diff: np.ndarray, w: np.ndarray,
             point: float) -> float:
        y1, y0 = y[t == 1], y[t == 0]
        return float(np.var(y1, ddof=1) / y1.shape[0] + np.var(y0, ddof=1) / y0.shape[0])

    with mock.patch.object(blocked_mod, "block_variance", fake):
        yield


@contextmanager
def wald_for_binary_pairs() -> Iterator[None]:
    """Binary pairs get the paired t interval, which has zero width when no pair disagrees."""

    def fake(b: int, c: int, k: int, level: float) -> tuple[float, float]:
        d = np.r_[np.ones(b), -np.ones(c), np.zeros(k - b - c)]
        crit = float(stats.t.ppf(0.5 + level / 2, k - 1))
        half = crit * float(np.std(d, ddof=1)) / math.sqrt(k)
        return float(d.mean()) - half, float(d.mean()) + half

    with mock.patch.object(blocked_mod, "paired_binary_interval", fake):
        yield


@contextmanager
def fixed_effects_weights() -> Iterator[None]:
    """Blocks weighted by n_b p_b (1 - p_b): the fixed-effects regression's estimand, which is
    not the per-unit average when the treated share varies across blocks."""

    def fake(eff: pd.DataFrame) -> np.ndarray:
        w: np.ndarray = (eff["n1"] * eff["n0"] / eff["n"]).to_numpy(dtype=float)
        out: np.ndarray = w / w.sum()
        return out

    with mock.patch.object(blocked_mod, "block_weights", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "unpaired_se": unpaired_se,
    "wald_for_binary_pairs": wald_for_binary_pairs,
    "fixed_effects_weights": fixed_effects_weights,
}

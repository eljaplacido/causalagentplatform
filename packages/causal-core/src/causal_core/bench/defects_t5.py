"""
Seeded defects for T5: plausible ways to get staggered difference-in-differences wrong.

Each patches one seam of ``causal_core.panel``, which calls every seam through its own
module namespace, so the patch is the function the analysis actually calls (the
lesson of T4's defects, which were silently absent when patched on the wrong module).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import numpy as np

import causal_core.panel as panel_mod
from causal_core.contract import RefutationResult


@contextmanager
def twfe_static() -> Iterator[None]:
    """The overall effect is the two-way fixed-effects coefficient.

    Y_it on unit and period effects plus the treatment indicator, by the within
    transformation. It is the estimator most analyses would use, and under
    heterogeneous, growing effects it weights some cells negatively.
    """
    real = panel_mod.overall_effect

    def fake(panel: panel_mod.Panel, post: list[panel_mod.Cell]) -> panel_mod.Aggregate:
        t_idx = np.arange(panel.t)
        d = ((panel.cohort[:, None] >= 0) & (t_idx[None, :] >= panel.cohort[:, None])).astype(
            float
        )

        def within(a: np.ndarray) -> np.ndarray:
            return a - a.mean(axis=1, keepdims=True) - a.mean(axis=0, keepdims=True) + a.mean()

        dd, yy = within(d), within(panel.y)
        beta = float((dd * yy).sum() / (dd * dd).sum())
        honest = real(panel, post)
        return panel_mod.Aggregate(point=beta, psi=honest.psi)

    with mock.patch.object(panel_mod, "overall_effect", fake):
        yield


@contextmanager
def forbidden_comparisons() -> Iterator[None]:
    """Controls include units already treated by period t -- TWFE's forbidden comparison."""

    def fake(panel: panel_mod.Panel, g: int, t: int) -> np.ndarray:
        return np.asarray((panel.cohort < 0) | ((panel.cohort != g) & (panel.cohort <= t)))

    with mock.patch.object(panel_mod, "controls", fake):
        yield


@contextmanager
def cells_independent() -> Iterator[None]:
    """The aggregate's variance is the weighted sum of the cells' variances.

    Cells of one cohort share a base period and the same units, so they are strongly
    correlated; treating them as independent understates the aggregate's uncertainty.
    """

    def fake(agg: panel_mod.Aggregate, panel: panel_mod.Panel,
             cells: list[panel_mod.Cell]) -> float:
        pi = {g: float((panel.cohort == g).mean()) for g in {c.g for c in cells}}
        s = sum(pi[c.g] for c in cells)
        var = sum((pi[c.g] / s) ** 2 * np.mean(c.psi**2) / panel.n for c in cells)
        return float(np.sqrt(var))

    with mock.patch.object(panel_mod, "aggregate_se", fake):
        yield


@contextmanager
def pretrend_blind() -> Iterator[None]:
    """The pre-trends test always passes."""
    real = panel_mod.pre_trends

    def fake(panel: panel_mod.Panel, pre: dict[int, panel_mod.Aggregate],
             alpha: float) -> RefutationResult | None:
        out = real(panel, pre, alpha)
        return None if out is None else out.model_copy(update={"passed": True, "p_value": 1.0})

    with mock.patch.object(panel_mod, "pre_trends", fake):
        yield


@contextmanager
def last_pre_period_only() -> Iterator[None]:
    """The bound on pre-adoption deviations looks only at the period next to the base.

    That is the period with the smallest deviation under a trend, so the bound on how
    far trends can diverge afterwards is the least informative one there is.
    """
    real = panel_mod.pre_bound

    def fake(panel: panel_mod.Panel, pre: dict[int, panel_mod.Aggregate], alpha: float) -> float:
        return real(panel, {e: a for e, a in pre.items() if e == -2} or pre, alpha)

    with mock.patch.object(panel_mod, "pre_bound", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "twfe_static": twfe_static,
    "forbidden_comparisons": forbidden_comparisons,
    "cells_independent": cells_independent,
    "pretrend_blind": pretrend_blind,
    "last_pre_period_only": last_pre_period_only,
}

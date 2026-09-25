"""
Seeded defects for T6: plausible ways to get anytime-valid monitoring wrong.

Each patches one seam of ``causal_core.sequential``, which calls its seams through its
own module namespace, so the patch reaches the analysis (T4's lesson).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from scipy import stats

import causal_core.sequential as seq_mod
from causal_core.contract import CausalQuery


@contextmanager
def fixed_n_interval() -> Iterator[None]:
    """Every look reports the ordinary fixed-sample interval -- the mistake T6 exists for."""

    def fake(sigma: float, t: int, rho: float, alpha: float) -> float:
        return float(stats.norm.ppf(1 - alpha / 2)) * sigma / math.sqrt(t)

    with mock.patch.object(seq_mod, "half_width", fake):
        yield


@contextmanager
def boundary_without_mixture_term() -> Iterator[None]:
    """The boundary's logarithm loses its sqrt(tρ² + 1) factor: log(1/α) instead."""

    def fake(sigma: float, t: int, rho: float, alpha: float) -> float:
        r2 = rho * rho
        return sigma * math.sqrt(2 * (t * r2 + 1) / (t * t * r2) * math.log(1 / alpha))

    with mock.patch.object(seq_mod, "half_width", fake):
        yield


@contextmanager
def ignore_allocation() -> Iterator[None]:
    """The score assumes an even split, whatever the randomizer actually does."""

    def fake(query: CausalQuery) -> float:
        return 0.5

    with mock.patch.object(seq_mod, "allocation", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "fixed_n_interval": fixed_n_interval,
    "boundary_without_mixture_term": boundary_without_mixture_term,
    "ignore_allocation": ignore_allocation,
}

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
def log10_boundary() -> Iterator[None]:
    """The boundary's logarithm is base 10 -- math.log10 typed for math.log.

    (v1 registered boundary_without_mixture_term here. At this setting the sequence
    over-covers, so that narrower boundary was still valid and no coverage bar could
    catch it: it was not a defect. This one is.)
    """

    def fake(sigma: float, t: int, rho: float, alpha: float) -> float:
        r2 = rho * rho
        mixture = math.log10(math.sqrt(t * r2 + 1) / alpha)
        return sigma * math.sqrt(2 * (t * r2 + 1) / (t * t * r2) * mixture)

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
    "log10_boundary": log10_boundary,
    "ignore_allocation": ignore_allocation,
}

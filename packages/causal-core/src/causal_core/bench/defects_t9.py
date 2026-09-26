"""
Seeded defects for T9: plausible ways to get a Rosenbaum bound wrong.

Each patches one seam of ``causal_core.sensitivity``, called through its own namespace.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import numpy as np

import causal_core.sensitivity as sens_mod


@contextmanager
def random_pairs() -> Iterator[None]:
    """The matched structure is ignored: each treated unit gets a control drawn at random."""

    def fake(t: Any, score: Any, caliper: float) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(len(t))
        treated, control = np.flatnonzero(t == 1), np.flatnonzero(t == 0)
        k = min(treated.size, control.size)
        return rng.permutation(treated)[:k], rng.permutation(control)[:k]

    with mock.patch.object(sens_mod, "match_pairs", fake):
        yield


@contextmanager
def lower_bound() -> Iterator[None]:
    """The favourable bound: the least biased assignment, π = 1/(1+Γ), instead of the worst."""

    with mock.patch.object(sens_mod, "bound_probability", lambda gamma: 1.0 / (1.0 + gamma)):
        yield


@contextmanager
def alpha_not_halved() -> Iterator[None]:
    """Γ* one-sided at 1 − ci_level, as if the interval's level were a one-sided one."""

    with mock.patch.object(sens_mod, "one_sided_alpha", lambda ci_level: 1.0 - ci_level):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "random_pairs": random_pairs,
    "lower_bound": lower_bound,
    "alpha_not_halved": alpha_not_halved,
}

"""
Seeded defects: known-bad variants of the kernel that the T0 gate must catch.

"A suite that cannot catch these publishes nothing." Each defect is a context
manager that patches one mechanism in-process, so the checks it is run
against must be called with ``parallel=False`` (a process pool would not see
the patch). ``tests/t0/test_seeded_defects.py`` runs every defect against the
check named in the pre-registration and asserts that the check fails.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest import mock

import numpy as np

import causal_core.estimators as est_mod
import causal_core.estimators.builtin as builtin
import causal_core.identification as identify_mod
import causal_core.pipeline as analyze_mod
import causal_core.refute as refute_mod
from causal_core.contract import AnalysisPolicy, EstimateRecord, Interval, Strategy
from causal_core.estimators import Fit, Prepared
from causal_core.identification.id_algorithm import IDResult


@contextmanager
def constant_ci() -> Iterator[None]:
    """Every interval is (-0.5, 0.5): the platform's behaviour before causal-core."""

    def fake(point: float, se: float | None, level: float, method: str) -> Interval:
        return Interval(level=level, lo=-0.5, hi=0.5, method="constant")

    with mock.patch.object(builtin, "wald_interval", fake):
        yield


@contextmanager
def halved_se() -> Iterator[None]:
    real = builtin.wald_interval

    def fake(point: float, se: float | None, level: float, method: str) -> Interval | None:
        return real(point, None if se is None else se / 2, level, method)

    with mock.patch.object(builtin, "wald_interval", fake):
        yield


@contextmanager
def ignore_adjustment() -> Iterator[None]:
    real = analyze_mod.identify

    def fake(g: Any, query: Any) -> Any:
        cert = real(g, query)
        return cert.model_copy(update={"adjustment_set": ()})

    with mock.patch.object(analyze_mod, "identify", fake):
        yield


@contextmanager
def suppress_hedge() -> Iterator[None]:
    real = identify_mod.identify_id

    def fake(g: Any, x: Any, y: Any) -> IDResult:
        res = real(g, x, y)
        return res if res.identified else IDResult(True, "hedge suppressed")

    with mock.patch.object(identify_mod, "identify_id", fake):
        yield


@dataclass(frozen=True)
class _Leaky:
    """Builds its answer from the outcome alone -- the treatment is ignored."""

    name: str = "leaky"
    family: str = "defect"
    strategies: frozenset[Strategy] = frozenset({Strategy.backdoor, Strategy.randomized})
    treatment_kinds: frozenset[str] = frozenset({"binary", "continuous"})
    priority: int = 0

    def supports(self, d: Prepared, strategy: Strategy) -> bool:
        return True

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        hi = d.y > np.median(d.y)
        point = float(d.y[hi].mean() - d.y[~hi].mean())
        se = float(np.std(d.y) / np.sqrt(d.n))
        ci = builtin.wald_interval(point, se, policy.ci_level, "defect")
        rec = EstimateRecord(estimator=self.name, family=self.family, estimand="defect",
                             point=point, se=se, ci=ci, n_used=d.n)
        return Fit(record=rec, influence=None)


@contextmanager
def leaky_estimator() -> Iterator[None]:
    est_mod.register(_Leaky())  # type: ignore[arg-type]
    try:
        yield
    finally:
        est_mod.unregister("leaky")


@contextmanager
def refuter_always_passes() -> Iterator[None]:
    real = refute_mod.run

    def fake(ctx: Any) -> list[Any]:
        return [r.model_copy(update={"passed": True}) for r in real(ctx)]

    with mock.patch.object(refute_mod, "run", fake):
        yield


@contextmanager
def triangulation_disabled() -> Iterator[None]:
    real = analyze_mod._triangulate

    def fake(fits: Any, alpha: float) -> Any:
        tri = real(fits, alpha)
        return None if tri is None else tri.model_copy(update={"agree": True})

    with mock.patch.object(analyze_mod, "_triangulate", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "constant_ci": constant_ci,
    "halved_se": halved_se,
    "ignore_adjustment": ignore_adjustment,
    "suppress_hedge": suppress_hedge,
    "leaky_estimator": leaky_estimator,
    "refuter_always_passes": refuter_always_passes,
    "triangulation_disabled": triangulation_disabled,
}

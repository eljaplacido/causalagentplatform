"""
Estimator portfolio and plug-in registry.

An estimator declares which identification strategies and treatment kinds it
handles, and its *family* (outcome model, weighting, doubly robust, ...). The
pipeline runs every registered estimator that fits the certificate, reports the
first by priority as primary, and triangulates the rest against it -- so two
estimators resting on different modelling assumptions check each other.

Third-party packages add estimators without touching this code::

    # pyproject.toml of the plug-in
    [project.entry-points."causal_core.estimators"]
    my_forest = "my_pkg.estimators:ForestEstimator"

The object must satisfy :class:`Estimator`. Plug-ins are loaded lazily, once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from causal_core.contract import AnalysisPolicy, EstimateRecord, Strategy

F = NDArray[np.float64]
TreatmentKind = Literal["binary", "continuous"]


@dataclass(frozen=True)
class Prepared:
    """Analysis-ready arrays. Binary treatments are coded 0/1."""

    y: F
    t: F
    X: F  # adjustment covariates, one-hot encoded, n x p (p may be 0)
    covariate_names: tuple[str, ...]
    treatment_kind: TreatmentKind
    levels: tuple[float, float]
    scale: float  # multiply a per-unit effect by this (t1 - t0) for continuous treatments
    instruments: F | None = None
    mediators: F | None = None
    treatment_proxies: F | None = None
    outcome_proxies: F | None = None

    @property
    def n(self) -> int:
        return int(self.y.shape[0])


@dataclass
class Fit:
    record: EstimateRecord
    influence: F | None = None  # per-observation IF of record.point (already scaled)
    extras: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class Estimator(Protocol):
    name: str
    family: str
    strategies: frozenset[Strategy]
    treatment_kinds: frozenset[TreatmentKind]
    priority: int  # lower runs first; the first applicable estimator is primary

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit: ...


_REGISTRY: dict[str, Estimator] = {}
_PLUGINS_LOADED = False


def register(est: Estimator, *, replace: bool = False) -> Estimator:
    if not isinstance(est, Estimator):
        raise TypeError(f"{est!r} does not implement the Estimator protocol")
    if est.name in _REGISTRY and not replace:
        raise ValueError(f"estimator {est.name!r} is already registered")
    _REGISTRY[est.name] = est
    return est


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def _load_plugins() -> None:
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True
    for ep in entry_points(group="causal_core.estimators"):
        obj = ep.load()
        est = obj() if isinstance(obj, type) else obj
        if est.name not in _REGISTRY:
            register(est)


def registered() -> dict[str, Estimator]:
    _load_plugins()
    return dict(_REGISTRY)


def applicable(strategy: Strategy, kind: TreatmentKind) -> list[Estimator]:
    ests = [
        e for e in registered().values() if strategy in e.strategies and kind in e.treatment_kinds
    ]
    return sorted(ests, key=lambda e: (e.priority, e.name))


def names(ests: Iterable[Estimator]) -> list[str]:
    return [e.name for e in ests]


from causal_core.estimators import builtin as _builtin  # noqa: E402

for _est in _builtin.BUILTIN:
    register(_est)

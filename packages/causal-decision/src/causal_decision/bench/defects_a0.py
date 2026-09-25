"""
Seeded defects for A0: plausible ways to decide whether a revision breaks a verdict wrong.

Two of them are the answers a careful person reaches first -- "the kernel still
identifies it by back-door, so it holds" and "the kernel's new adjustment set is
valid, so it holds" -- which is why they are here. Each patches one seam of
``causal_decision.assumptions`` in-process; ``tests/a0/test_seeded_defects.py`` runs
each against the check the pre-registration says must catch it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from causal_core.contract import CausalQuery, ConditionCheck, IdentificationStatus, Strategy
from causal_core.graph import Graph
from causal_core.identification import adjustment_checks, identify

import causal_decision.assumptions as am

_real_checks = adjustment_checks


def _backdoor_set(g: Graph, x: str, y: str) -> tuple[str, ...] | None:
    try:
        cert = identify(g, CausalQuery(treatment=x, outcome=y))
    except ValueError:
        return None
    if cert.status is IdentificationStatus.identified and cert.strategy is Strategy.backdoor:
        return cert.adjustment_set
    return None


@contextmanager
def strategy_only() -> Iterator[None]:
    """`holds` whenever the new graph is still identified by back-door, whatever the set."""

    def fake(g: Graph, x: str, y: str, z: Iterable[str]) -> list[ConditionCheck]:
        return [] if _backdoor_set(g, x, y) is not None else _real_checks(g, x, y, z)

    with mock.patch.object(am, "adjustment_checks", fake):
        yield


@contextmanager
def new_set_valid() -> Iterator[None]:
    """Checks the kernel's NEW adjustment set instead of the one the estimate used."""

    def fake(g: Graph, x: str, y: str, z: Iterable[str]) -> list[ConditionCheck]:
        chosen = _backdoor_set(g, x, y)
        return _real_checks(g, x, y, z if chosen is None else chosen)

    with mock.patch.object(am, "adjustment_checks", fake):
        yield


@contextmanager
def ignore_bidirected() -> Iterator[None]:
    """The new graph's bidirected edges -- declared hidden confounders -- are dropped."""

    def fake(g: Graph, x: str, y: str, z: Iterable[str]) -> list[ConditionCheck]:
        spec = g.spec.model_copy(
            update={"edges": tuple(e for e in g.spec.edges if e.kind == "directed")}
        )
        return _real_checks(Graph.from_spec(spec), x, y, z)

    with mock.patch.object(am, "adjustment_checks", fake):
        yield


@contextmanager
def diff_drops_removed() -> Iterator[None]:
    """The diff forgets the edges that went away -- the strongest assumptions a graph makes."""
    real = am.diff

    def fake(old: Any, new: Any) -> am.ModelDiff:
        return real(old, new).model_copy(update={"removed_edges": ()})

    with mock.patch.object(am, "diff", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "strategy_only": strategy_only,
    "new_set_valid": new_set_valid,
    "ignore_bidirected": ignore_bidirected,
    "diff_drops_removed": diff_drops_removed,
}

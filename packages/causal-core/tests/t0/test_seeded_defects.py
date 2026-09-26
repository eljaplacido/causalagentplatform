"""
Every seeded defect must make its designated T0 check fail.

If one of these tests fails, the gate cannot see that defect -- and by the
pre-registration's rule, a suite that cannot catch its seeded defects
publishes nothing. The designated check per defect is the table in
benchmarks/t0/PRE-REGISTRATION.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from causal_core.bench import t0
from causal_core.bench.defects import DEFECTS

pytestmark = pytest.mark.t0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t0.load_prereg()[0]


def _identification(cfg: dict[str, Any]) -> bool:
    return all(c.passed for c in t0.check_identification())


def _estimation(cfg: dict[str, Any]) -> bool:
    return t0.check_estimation(cfg, reps=cfg["seeded_defects"]["reps"], parallel=False).passed


def _refuters(cfg: dict[str, Any]) -> bool:
    return t0.check_refuters(cfg, reps=cfg["seeded_defects"]["reps"], parallel=False).passed


def _triangulation(cfg: dict[str, Any]) -> bool:
    return t0.check_triangulation(cfg, reps=cfg["seeded_defects"]["reps"], parallel=False).passed


CATCHER: dict[str, Callable[[dict[str, Any]], bool]] = {
    "constant_ci": _estimation,
    "halved_se": _estimation,
    "ignore_adjustment": _estimation,
    "suppress_hedge": _identification,
    "leaky_estimator": _refuters,
    "refuter_always_passes": _refuters,
    "triangulation_disabled": _triangulation,
    "specification_test_disabled": _triangulation,
    "specification_pooled_arms": _triangulation,
    "triangulation_flag_ignored": _triangulation,
}


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught(cfg: dict[str, Any], name: str) -> None:
    with DEFECTS[name]():
        passed = CATCHER[name](cfg)
    assert not passed, f"seeded defect {name!r} went undetected"

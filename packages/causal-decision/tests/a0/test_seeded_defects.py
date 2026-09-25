"""
Every seeded defect must make its designated A0 check fail.

The designated check per defect is the table in
benchmarks/a0-assumption-impact/PRE-REGISTRATION.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision.bench import a0
from causal_decision.bench.defects_a0 import DEFECTS

pytestmark = pytest.mark.a0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return a0.load_prereg()[0]


@pytest.fixture(scope="module")
def cases(cfg: dict[str, Any]) -> list[a0.Case]:
    c = cfg["cases"]
    return a0.generate(c["seed"], c["n_pairs"])[0]


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(a0.CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught(cfg: dict[str, Any], cases: list[a0.Case], name: str) -> None:
    with DEFECTS[name]():
        caught = a0.CATCHER[name](cases, cfg)
    assert caught, f"seeded defect {name!r} went undetected"

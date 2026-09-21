"""
Every seeded defect must make its designated D0 check fail.

If one of these tests fails, the gate cannot see that defect, and a green gate that
cannot see a broken decision procedure is worse than no gate: it certifies. The
designated check per defect is the table in benchmarks/d0-decision-policy/PRE-REGISTRATION.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision.bench import d0
from causal_decision.bench.defects import DEFECTS

pytestmark = pytest.mark.d0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return d0.load_prereg()[0]


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(d0.CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught(cfg: dict[str, Any], name: str) -> None:
    with DEFECTS[name]():
        caught = d0.CATCHER[name](cfg)
    assert caught, f"seeded defect {name!r} went undetected"

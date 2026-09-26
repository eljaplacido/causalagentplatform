"""
Every seeded defect must make the checks the C0 pre-registration names for it fail.

The table is in benchmarks/c0-calibration/PRE-REGISTRATION.md; `c0.CATCHER` holds
it as data, and a defect counts as caught only when every check listed trips.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision.bench import c0
from causal_decision.bench.defects_c0 import DEFECTS

pytestmark = pytest.mark.c0


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(c0.CATCHER)


def test_the_gate_passes_without_a_defect(
    cfg: dict[str, Any], pool: dict[tuple[str, bool], list[c0.Member]]
) -> None:
    """Otherwise 'caught' below would mean nothing."""
    assert c0.all_failures(cfg, pool) == []


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught(
    cfg: dict[str, Any], pool: dict[tuple[str, bool], list[c0.Member]], name: str
) -> None:
    with DEFECTS[name]():
        tripped = {kind for kind, _ in c0.all_failures(cfg, pool)}
    missing = c0.CATCHER[name] - tripped
    assert not missing, f"seeded defect {name!r}: {sorted(missing)} did not fail (got {tripped})"

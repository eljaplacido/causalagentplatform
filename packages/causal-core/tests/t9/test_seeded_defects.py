"""Every seeded T9 defect must fail the bar the pre-registration names."""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t9
from causal_core.bench.defects_t9 import DEFECTS

pytestmark = pytest.mark.t9


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t9.load_prereg()[0]


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(t9.CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught_by_its_registered_bar(cfg: dict[str, Any], name: str) -> None:
    assert t9.defect_caught(name, cfg), f"seeded defect {name!r} went undetected"

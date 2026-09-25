"""
Every seeded defect must make the bar the pre-registration names fail -- that bar,
not merely some bar (T4's lesson: two defects were "caught" by unrelated failures
while the patch itself was not reaching the code).
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t5
from causal_core.bench.defects_t5 import DEFECTS

pytestmark = pytest.mark.t5


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t5.load_prereg()[0]


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(t5.CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught_by_its_registered_bar(cfg: dict[str, Any], name: str) -> None:
    assert t5.defect_caught(name, cfg), f"seeded defect {name!r} went undetected"

"""
The real-data benchmarks as tests: T2 IHDP and T3 LaLonde, with their pre-registered bars.

The data have no redistribution licence and are never committed. These tests use the
local cache (~/.cache/causal-core, or $CAUSAL_CORE_CACHE) and download into it only when
CAUSAL_FETCH=1, so an offline run skips instead of failing.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import lalonde
from tests.bench._data import need

pytestmark = pytest.mark.real_data


def test_lalonde_no_confident_wrong_answer() -> None:
    need([f"{n}.dta" for n in lalonde.FILES], "lalonde")
    cfg, _ = lalonde.load_prereg()
    result = lalonde.run(cfg, lalonde.load(lalonde.fetch(cfg)))
    assert result["passed"], result["failures"]
    assert not result["confident_wrong"]


def test_ihdp_att_answered_and_covered_no_confident_misses(ihdp_result: dict[str, Any]) -> None:
    assert ihdp_result["passed"], ihdp_result["failures"]

"""The T11 blocked-designs gate as tests. Bars from benchmarks/t11-blocked/PRE-REGISTRATION.md.

    pytest -m t11
"""

from __future__ import annotations

from typing import Any

import pytest
from causal_core.bench import t11

pytestmark = pytest.mark.t11


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t11.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T11"


def test_estimates_are_unbiased_intervals_cover_and_pairing_pays(cfg: dict[str, Any]) -> None:
    reports, failures = t11.check_estimation(cfg)
    assert not failures, failures[:8]
    est = cfg["estimation"]
    assert {r.id for r in reports} == {*est["continuous_designs"], *est["binary_designs"]}


def test_the_gnomon_s1_table_is_reproduced_exactly(cfg: dict[str, Any]) -> None:
    _, failures = t11.check_s1(cfg)
    assert not failures, failures


def test_designs_the_method_cannot_answer_are_refused(cfg: dict[str, Any]) -> None:
    _, failures = t11.check_refusals(cfg)
    assert not failures, failures

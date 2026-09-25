"""The T7 cluster-randomized gate as tests. Bars from benchmarks/t7-cluster/PRE-REGISTRATION.md.

    pytest -m t7
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t7

pytestmark = pytest.mark.t7


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t7.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T7"


def test_estimates_are_unbiased_and_intervals_cover(cfg: dict[str, Any]) -> None:
    reports, failures = t7.check_estimation(cfg)
    assert not failures, failures[:8]
    assert {r.id for r in reports} == set(cfg["estimation"]["designs"])


def test_designs_the_method_cannot_answer_are_refused(cfg: dict[str, Any]) -> None:
    _, failures = t7.check_refusals(cfg)
    assert not failures, failures

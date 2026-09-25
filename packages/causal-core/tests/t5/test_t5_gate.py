"""
The T5 difference-in-differences gate as tests. Bars come from
benchmarks/t5-did/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m t5
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t5

pytestmark = pytest.mark.t5


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t5.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T5"
    lo, hi = cfg["estimation"]["coverage_band"]
    assert lo < 0.95 < hi


def test_estimates_are_unbiased_intervals_cover_and_pretrends_behave(
    cfg: dict[str, Any],
) -> None:
    reports, failures = t5.check_estimation(cfg)
    assert not failures, failures[:8]
    assert {r.id for r in reports} == set(t5.DESIGNS)
    assert all(r.results == r.reps for r in reports), "a replication failed to produce a result"


def test_designs_the_estimator_cannot_answer_are_refused(cfg: dict[str, Any]) -> None:
    got, failures = t5.check_refusals(cfg)
    assert not failures, failures
    assert set(got) == set(cfg["refusals"]["must_refuse"])

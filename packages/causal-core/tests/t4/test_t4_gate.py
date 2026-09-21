"""
The T4 segment gate as tests. Thresholds come from
benchmarks/t4-segments/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m t4
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t4

pytestmark = pytest.mark.t4


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t4.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T4"
    assert cfg["estimation"]["coverage_lo"] < 0.95 < cfg["estimation"]["coverage_hi"]


def test_segment_estimates_are_unbiased_and_their_intervals_cover(cfg: dict[str, Any]) -> None:
    reports, failures = t4.check_estimation(cfg)
    assert not failures, failures[:8]
    assert len(reports) == len(t4.CASES)
    # A suite that estimated nothing would report no failures either.
    estimated = sum(len(r.per_segment) for r in reports)
    assert estimated >= 15, f"only {estimated} segments were estimated"


def test_looking_at_many_segments_does_not_manufacture_findings(cfg: dict[str, Any]) -> None:
    """With every segment null, Holm must almost never reject anything."""
    rate, ok = t4.check_false_discovery(cfg)
    assert ok, f"false discovery rate {rate:.3f} > {cfg['family_wise']['false_discovery_max']}"


def test_the_heterogeneity_test_finds_real_differences_and_invents_none(
    cfg: dict[str, Any],
) -> None:
    reports, _ = t4.check_estimation(cfg, cases=[t4.CASES[0], t4.CASES[1]])
    homogeneous, sign_flip = reports[0], reports[1]
    assert homogeneous.heterogeneity_rate <= cfg["heterogeneity"]["false_positive_max"]
    assert sign_flip.heterogeneity_rate >= cfg["heterogeneity"]["power_min"]


def test_a_segment_that_cannot_be_estimated_refuses_every_time(cfg: dict[str, Any]) -> None:
    reports, _ = t4.check_estimation(cfg, cases=[t4.CASES[5], t4.CASES[6]])
    assert reports[0].refusal_rate["tiny"] == 1.0
    assert reports[1].refusal_rate["untreated"] == 1.0
    # ...and the segments that can be estimated still are.
    assert reports[0].per_segment["big"]["n"] > 0
    assert reports[1].per_segment["mixed"]["n"] > 0

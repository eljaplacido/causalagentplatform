"""
Triangulation flags the planted outcome-model misspecification at moderate n.

Register entry ``kernel-triangulation-moderate-n``: through T0 v3, triangulation compared
estimates only, and against a confounder entering the outcome as Z**2 its power at
n = 2,000 was 0.035. T0 v4 adds the outcome-model specification test
(``causal_core.specification``) to triangulation's Holm family and gates, at the
pre-registered moderate n, that the flag fires AND makes the result contested, that the
primary estimate still covers, and that no disagreement leaves a result uncontested.
Every bar is read from benchmarks/t0/PRE-REGISTRATION.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t0

pytestmark = pytest.mark.t0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t0.load_prereg()[0]


@pytest.fixture(scope="module")
def report(cfg: dict[str, Any]) -> t0.RateReport:
    return t0.check_triangulation(cfg)


def test_the_registered_moderate_n_is_moderate(cfg: dict[str, Any]) -> None:
    c = cfg["triangulation"]
    assert c["moderate_power_n"] <= 5000, "the register entry asks for n <= 5,000"
    assert c["moderate_power_min"] >= 0.80


def test_power_at_moderate_n_counts_only_contested_flags(
    cfg: dict[str, Any], report: t0.RateReport
) -> None:
    c = cfg["triangulation"]
    key = (
        f"{t0.TRIANGULATION_POWER_CASE.id}@n={c['moderate_power_n']} "
        "(gated, disagree and contested)"
    )
    assert report.power[key] >= c["moderate_power_min"], report.failures


def test_the_flag_changes_the_status_not_the_estimate(
    cfg: dict[str, Any], report: t0.RateReport
) -> None:
    c = cfg["triangulation"]
    n = c["moderate_power_n"]
    case = t0.TRIANGULATION_POWER_CASE.id
    assert report.reported["status consistency"] >= c["status_consistency_required"]
    p = report.reported[f"{case}@n={n} primary coverage binomial p"]
    assert p >= c["moderate_power_coverage_alpha"], report.failures


def test_the_whole_triangulation_section_passes(report: t0.RateReport) -> None:
    assert report.passed, report.failures

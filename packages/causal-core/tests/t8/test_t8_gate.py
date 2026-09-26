"""The T8 switchback gate as tests. Bars from benchmarks/t8-switchback/PRE-REGISTRATION.md.

    pytest -m t8
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t8

pytestmark = pytest.mark.t8


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T8"


def test_estimates_are_unbiased_and_intervals_cover(
    cfg: dict[str, Any], estimation: tuple[list[t8.DesignReport], list[str]]
) -> None:
    reports, failures = estimation
    assert not failures, failures[:8]
    co = cfg["carryover"]
    assert {r.id for r in reports} == (set(cfg["estimation"]["designs"]) | set(co["reported"])
                                       | set(co["power_designs"]))


def test_designs_the_method_cannot_answer_are_refused(cfg: dict[str, Any]) -> None:
    _, failures = t8.check_refusals(cfg)
    assert not failures, failures

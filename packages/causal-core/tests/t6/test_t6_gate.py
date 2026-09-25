"""
The T6 anytime-valid monitoring gate as tests. Bars come from
benchmarks/t6-sequential/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m t6
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t6

pytestmark = pytest.mark.t6


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t6.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T6"
    assert 0.9 < cfg["coverage"]["uniform_min"] < 0.95


def test_coverage_holds_at_every_look_and_real_effects_are_seen(cfg: dict[str, Any]) -> None:
    reports, failures = t6.check_streams(cfg)
    assert not failures, failures[:8]
    assert {r.id for r in reports} == set(t6.STREAMS)


def test_streams_the_method_cannot_answer_are_refused(cfg: dict[str, Any]) -> None:
    got, failures = t6.check_refusals(cfg)
    assert not failures, failures
    assert set(got) == set(cfg["refusals"]["must_refuse"])

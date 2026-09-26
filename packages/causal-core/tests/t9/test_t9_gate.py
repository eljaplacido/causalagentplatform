"""The T9 Rosenbaum gate as tests. Bars from benchmarks/t9-rosenbaum/PRE-REGISTRATION.md.

    pytest -m t9
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t9

pytestmark = pytest.mark.t9


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t9.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T9"


def test_the_oracle_matches_the_registered_truths() -> None:
    assert t9.pairs_truth(t9.PAIRS["pairs_effect"]) == pytest.approx(3.171, abs=5e-4)
    assert t9.e2e_design_sensitivity(t9.E2E["e2e_hidden_bias"]) == pytest.approx(1.880, abs=5e-4)
    assert t9.e2e_design_sensitivity(t9.E2E["e2e_effect"]) == pytest.approx(2.241, abs=5e-4)
    assert t9.e2e_design_sensitivity(t9.E2E["e2e_null_measured"]) == pytest.approx(1.0)


def test_the_bound_is_valid_and_tracks_the_planted_bias(cfg: dict[str, Any]) -> None:
    reports, failures = t9.check_pairs(cfg)
    assert not failures, failures
    assert {r.id for r in reports} == set(t9.PAIRS)


def test_the_matched_design_is_valid_and_tracks_the_oracle(cfg: dict[str, Any]) -> None:
    reports, failures = t9.check_e2e(cfg)
    assert not failures, failures
    assert {r.id for r in reports} == set(t9.E2E)


def test_designs_the_bound_does_not_apply_to_report_no_gamma(cfg: dict[str, Any]) -> None:
    _, failures = t9.check_withheld(cfg)
    assert not failures, failures

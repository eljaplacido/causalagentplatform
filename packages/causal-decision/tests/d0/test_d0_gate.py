"""
The D0 decision-policy gate as tests. Bars come from
benchmarks/d0-decision-policy/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m d0
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision import decide
from causal_decision.bench import d0

pytestmark = pytest.mark.d0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return d0.load_prereg()[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "D0"
    assert cfg["table"]["scenarios_must_match"] == 1.0
    assert cfg["properties"]["violations_allowed"] == 0


def test_every_scenario_returns_the_verdict_it_names(cfg: dict[str, Any]) -> None:
    results = d0.check_table()
    assert len(results) == len(d0.SCENARIOS) >= 15
    bad = [(r.id, r.expected, r.got, r.why) for r in results if not r.passed]
    assert not bad
    matched = sum(r.passed for r in results) / len(results)
    assert matched >= cfg["table"]["scenarios_must_match"]


def test_the_table_exercises_every_verdict_and_every_kind_of_evidence() -> None:
    """A table that never reaches a verdict cannot show the procedure reaches it."""
    results = d0.check_table()
    assert {r.got for r in results} == {"act", "act_with_approval", "experiment", "refuse"}
    kinds = {decide(s.evidence(), s.request).evidence.kind for s in d0.SCENARIOS}
    assert kinds == {"result", "refusal", "apparatus_failure"}


def test_the_properties_hold_over_the_registered_sweep(cfg: dict[str, Any]) -> None:
    c = cfg["properties"]
    violations = d0.check_properties(c["sweep"], c["seed"])
    assert len(violations) <= c["violations_allowed"], violations[:10]


def test_more_data_never_costs_autonomy() -> None:
    assert not d0.check_evidence_monotonicity()

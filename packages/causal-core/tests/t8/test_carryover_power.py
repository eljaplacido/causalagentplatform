"""The switchback carryover check is gated, and a flag changes the result (T8 v2).

Register entry ``carryover-check-weak`` leaves by this file: a pre-registered T8 version
gates the check's power at >= 0.8 against a carryover the washout does not cover, with a
false-alarm bar where it does. Bars from benchmarks/t8-switchback/PRE-REGISTRATION.md.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

import causal_core as cc
from causal_core.bench import t8
from causal_core.contract import AnalysisPolicy, CausalResult, ResultStatus

pytestmark = pytest.mark.t8


def _residual_at_washout(design: str, cfg: dict[str, Any]) -> float:
    lam = t8.DESIGNS[design].lam
    return math.exp(-cfg["streams"]["washout"] / lam) if lam > 0 else 0.0


def test_the_registration_gates_power_where_the_washout_does_not_cover_the_carryover(
    cfg: dict[str, Any],
) -> None:
    co = cfg["carryover"]
    assert cfg["meta"]["version"] >= 2
    assert co["power_min"] >= 0.8
    assert co["power_designs"], "no design carries the power bar"
    for d in co["power_designs"]:
        assert _residual_at_washout(d, cfg) > 0.5, f"{d}: the washout covers its carryover"
    assert co["false_alarm_max"] <= 0.10
    assert set(co["false_alarm_designs"]) == set(cfg["estimation"]["designs"])
    for d in co["false_alarm_designs"]:
        assert _residual_at_washout(d, cfg) < 0.02, f"{d}: the washout does not cover it"
    assert AnalysisPolicy().carryover_alpha == co["alpha"]


def test_the_check_has_its_registered_power_and_false_alarm_rate(
    cfg: dict[str, Any], estimation: tuple[list[t8.DesignReport], list[str]]
) -> None:
    reports, failures = estimation
    co = cfg["carryover"]
    by_id = {r.id: r for r in reports}
    for d in co["power_designs"]:
        assert by_id[d].carryover_flag_rate >= co["power_min"], by_id[d]
    for d in co["false_alarm_designs"]:
        assert by_id[d].carryover_flag_rate <= co["false_alarm_max"], by_id[d]
    assert not [f for f in failures if "carryover" in f or ": status" in f], failures


def test_a_flag_makes_every_result_contested_and_nothing_else_does(
    estimation: tuple[list[t8.DesignReport], list[str]],
) -> None:
    for r in estimation[0]:
        assert r.status_mismatches == 0, r
        assert r.contested_rate == r.carryover_flag_rate, r


def test_a_contested_switchback_still_reports_its_estimate(cfg: dict[str, Any]) -> None:
    frame = t8.simulate(t8.DESIGNS["material_carryover"], cfg, np.random.default_rng(7))
    flagged = t8.analyse(frame, cfg)
    assert isinstance(flagged, CausalResult)
    check = next(c for c in flagged.diagnostics.checks if c.name == t8.CHECK)
    assert not check.passed and flagged.status is ResultStatus.contested
    assert any("contested" in w for w in flagged.warnings)
    lenient = cc.analyze(frame, t8.GRAPH, treatment="policy", outcome="latency",
                            design="randomized", cluster="block", time="minute",
                            washout=cfg["streams"]["washout"],
                            policy=AnalysisPolicy(carryover_alpha=1e-12))
    assert isinstance(lenient, CausalResult) and lenient.status is ResultStatus.supported
    assert lenient.primary.point == flagged.primary.point, "the flag changes the status only"
    assert lenient.primary.ci == flagged.primary.ci

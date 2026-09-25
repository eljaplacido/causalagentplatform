"""Difference-in-differences through the kernel's front door, and what it refuses."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import causal_core as cc
from causal_core.bench import t5
from causal_core.contract import AbstentionReason, CausalQuery, CausalResult, Strategy, Tier
from causal_core.graph import Graph, parse_graph
from causal_core.identification import identify


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    return t5.simulate(t5.DESIGNS["staggered_dynamic"], 8, 0.5, np.random.default_rng(3))


def did(frame: pd.DataFrame, **over: object) -> object:
    kw: dict[str, object] = {
        "treatment": "rollout", "outcome": "latency", "unit": "unit", "time": "period",
        "estimand": "att", "design": "difference_in_differences",
    }
    kw.update(over)
    return cc.analyze(frame, t5.GRAPH, **kw)


def test_a_staggered_rollout_is_estimated_with_a_tier_c_certificate(panel: pd.DataFrame) -> None:
    out = did(panel)
    assert isinstance(out, CausalResult), out
    assert out.certificate.strategy is Strategy.difference_in_differences
    assert out.certificate.tier is Tier.C, "parallel trends is not in the graph"
    assert any("parallel trends" in a for a in out.certificate.assumptions)
    assert out.primary.estimand == "att" and out.primary.ci is not None
    truth, _ = t5.truth(t5.DESIGNS["staggered_dynamic"], 8)
    assert abs(out.primary.point - truth) < 4 * (out.primary.se or 1)
    assert [r.name for r in out.refutations] == ["pre_trends"]
    assert -1 not in {e.event_time for e in out.event_study}, "the base period is not an estimate"
    assert out.result_digest


def test_identification_alone_answers_for_a_panel_design() -> None:
    q = CausalQuery(treatment="rollout", outcome="latency", design="difference_in_differences",
                    unit="unit", time="period", estimand="att")
    cert = identify(Graph.from_spec(parse_graph(t5.GRAPH)), q)
    assert cert.tier is Tier.C and cert.strategy is Strategy.difference_in_differences


def test_did_needs_the_panel_columns_and_the_att(panel: pd.DataFrame) -> None:
    no_unit = did(panel, unit=None)
    assert no_unit.kind == "refusal" and no_unit.reason is AbstentionReason.INVALID_INPUT
    ate = did(panel, estimand="ate")
    assert ate.kind == "refusal" and "effect on the treated" in ate.detail


def test_options_this_version_does_not_support_are_refused_not_ignored(
    panel: pd.DataFrame,
) -> None:
    out = did(panel, segment_by=("unit",))
    assert out.kind == "refusal" and "segment_by" in out.detail


def test_a_non_binary_treatment_is_refused(panel: pd.DataFrame) -> None:
    frame = panel.assign(rollout=panel["rollout"].map({0.0: "off", 1.0: "on"}))
    out = did(frame)
    assert out.kind == "refusal" and "binary" in out.detail


def test_trends_that_are_not_parallel_contest_the_result() -> None:
    frame = t5.simulate(t5.DESIGNS["trends_violated"], 8, 0.5, np.random.default_rng(4))
    out = did(frame)
    assert isinstance(out, CausalResult)
    assert out.status.value == "contested"


def test_the_decision_layer_reads_a_panel_result_like_any_other(panel: pd.DataFrame) -> None:
    pytest.importorskip("causal_decision")
    from causal_decision import DecisionRequest, decide

    out = did(panel)
    d = decide(out, DecisionRequest(action="keep the rollout", outcome="latency",
                                    direction="increase", minimum_worthwhile_effect=0.5))
    assert d.verdict.value != "act", "tier C caps the verdict at approval by default"


def test_duplicate_unit_periods_are_refused(panel: pd.DataFrame) -> None:
    out = did(pd.concat([panel, panel.head(3)]))
    assert out.kind == "refusal" and "repeat" in out.detail


def test_a_panel_result_says_how_far_parallel_trends_may_fail(panel: pd.DataFrame) -> None:
    out = did(panel)
    assert isinstance(out, CausalResult) and out.sensitivity is not None
    pt = out.sensitivity.parallel_trends
    assert pt is not None and [r.m for r in pt.rows] == [0.0, 0.5, 1.0, 1.5, 2.0]
    widths = [r.hi - r.lo for r in pt.rows]
    assert widths == sorted(widths), "allowing more violation can only widen the interval"
    assert pt.breakdown is not None and pt.breakdown > 0
    at = next(r for r in pt.rows if r.m >= pt.breakdown) if pt.breakdown <= 2 else None
    if at is not None:
        assert at.lo <= 0 <= at.hi, "past the breakdown the interval must reach zero"
    assert any(c.id == "pt.breakdown" for c in out.claims)

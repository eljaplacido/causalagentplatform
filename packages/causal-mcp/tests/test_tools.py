from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from causal_mcp import tools


def test_bad_input_is_a_refusal() -> None:
    resp = tools.causal_analyze(graph="X -> Y", treatment="X", outcome="Y")
    assert resp.outcome.kind == "refusal"
    assert "exactly one of data" in resp.outcome.detail


def test_data_path_outside_roots_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.csv"
    pd.DataFrame({"X": [0, 1] * 20, "Y": np.arange(40.0)}).to_csv(outside, index=False)
    monkeypatch.setenv("CAUSAL_MCP_DATA_ROOTS", str(allowed))
    resp = tools.causal_analyze(graph="X -> Y", treatment="X", outcome="Y", data_path=str(outside))
    assert resp.outcome.kind == "refusal" and "PermissionError" in resp.outcome.detail
    inside = allowed / "d.csv"
    rng = np.random.default_rng(0)
    x = rng.integers(0, 2, 400).astype(float)
    pd.DataFrame({"X": x, "Y": x + rng.normal(size=400)}).to_csv(inside, index=False)
    resp = tools.causal_analyze(graph="X -> Y", treatment="X", outcome="Y", data_path=str(inside))
    assert resp.outcome.kind == "result"


def test_identify_invalid_adjustment_set_suggests_a_valid_one() -> None:
    resp = tools.causal_identify(
        graph="A -> M; B -> M; A -> X; B -> Y; X -> Y", treatment="X", outcome="Y", adjust_for=["M"]
    )
    assert not resp.identified and resp.refusal and "open" in resp.refusal
    assert resp.missing_information == ["a valid adjustment set is {B}"]


def test_check_graph_lists_implications_and_unreviewed_edges() -> None:
    resp = tools.causal_check_graph("A -> X -> Y; B -> Y; B -> X [origin=llm_prior]")
    assert resp.valid and resp.unreviewed_edges == ["B -> X"]
    assert {(i.a, i.b, tuple(i.given)) for i in resp.testable_implications} >= {("A", "B", ())}
    assert not tools.causal_check_graph("A -> B; B -> A").valid


def test_analyze_estimates_declared_segments() -> None:
    """Segments come back with two intervals and a heterogeneity verdict."""
    rng = np.random.default_rng(0)
    n = 3000
    seg = rng.choice(["p1", "p3"], size=n)
    load = rng.normal(size=n)
    t = (0.8 * load + rng.logistic(size=n) > 0).astype(float)
    y = 60 + np.where(seg == "p1", -12.0, 6.0) * t + 10 * load + rng.normal(0, 8, n)
    data = {"seg": seg.tolist(), "load": load.tolist(), "playbook": t.tolist(), "minutes": y.tolist()}

    resp = tools.causal_analyze(
        graph="load -> playbook; load -> minutes; playbook -> minutes",
        treatment="playbook",
        outcome="minutes",
        data=data,
        segment_by=["seg"],
    )
    assert resp.outcome.kind == "result"
    levels = {s.level: s for s in resp.outcome.segments}
    assert set(levels) == {"p1", "p3"}
    assert levels["p1"].estimate is not None and levels["p1"].estimate.point < -8
    assert levels["p3"].estimate is not None and levels["p3"].estimate.point > 3
    # The family-wise interval must be the wider one: that is what it is for.
    for s in levels.values():
        assert s.family_wise_ci is not None and s.estimate is not None and s.estimate.ci is not None
        assert s.family_wise_ci.hi - s.family_wise_ci.lo > s.estimate.ci.hi - s.estimate.ci.lo
    assert resp.outcome.heterogeneity is not None
    assert resp.outcome.heterogeneity.differs is True


def test_analyze_without_segments_reports_none() -> None:
    """The default is no segmentation, and the result says so rather than guessing."""
    rng = np.random.default_rng(1)
    x = rng.integers(0, 2, 400).astype(float)
    resp = tools.causal_analyze(
        graph="X -> Y",
        treatment="X",
        outcome="Y",
        data={"X": x.tolist(), "Y": (x + rng.normal(size=400)).tolist()},
    )
    assert resp.outcome.kind == "result"
    assert resp.outcome.segments == ()
    assert resp.outcome.heterogeneity is None

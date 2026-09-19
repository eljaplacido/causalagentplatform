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

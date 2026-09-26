"""The outcome-model specification test, against designs whose outcome model is known."""

from __future__ import annotations

import numpy as np
import pandas as pd

import causal_core as cc
from causal_core.contract import AnalysisPolicy

G = "Z -> X; Z -> Y; X -> Y"
FAST = AnalysisPolicy(refuters=(), sensitivity=False)


def _frame(n: int, seed: int, curvature: float, randomized: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z = 0.5 + rng.normal(size=n)
    lin = 0.0 if randomized else z
    x = (lin + rng.logistic(size=n) > 0).astype(float)
    y = x + z + curvature * z**2 + rng.normal(size=n)
    return pd.DataFrame({"Z": z, "X": x, "Y": y})


def test_curvature_the_linear_model_misses_contests_the_result() -> None:
    out = cc.analyze(_frame(2000, 11, 1.0), G, treatment="X", outcome="Y", policy=FAST)
    assert isinstance(out, cc.CausalResult)
    assert out.triangulation is not None and not out.triangulation.agree
    assert "rejects it" in out.triangulation.detail and "Z^2" in out.triangulation.detail
    assert out.status.value == "contested"
    assert out.primary.estimator == "aipw"  # the flag changes the status, not the estimate


def test_a_linear_outcome_passes_most_of_the_time() -> None:
    flags = []
    for seed in range(40):
        out = cc.analyze(_frame(1000, 100 + seed, 0.0), G, treatment="X", outcome="Y", policy=FAST)
        assert isinstance(out, cc.CausalResult) and out.triangulation is not None
        assert "specification test" in out.triangulation.detail
        flags.append(not out.triangulation.agree)
    # nominal 0.01 per design; 4 of 40 has probability below 1e-3 at that rate
    assert sum(flags) <= 3


def test_randomized_designs_are_not_tested() -> None:
    g = "Z -> Y; X -> Y"
    out = cc.analyze(
        _frame(2000, 12, 1.0, randomized=True), g, treatment="X", outcome="Y", policy=FAST,
        design="randomized",
    )
    assert isinstance(out, cc.CausalResult)
    assert out.certificate.strategy is not None and out.certificate.strategy.value == "randomized"
    assert out.triangulation is None or "specification test" not in out.triangulation.detail


def test_the_att_tests_only_the_control_arm() -> None:
    out = cc.analyze(
        _frame(2000, 13, 1.0), G, treatment="X", outcome="Y", policy=FAST, estimand="att"
    )
    assert isinstance(out, cc.CausalResult) and out.triangulation is not None
    assert "(control arm;" in out.triangulation.detail

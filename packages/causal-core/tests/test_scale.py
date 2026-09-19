"""Scale: a million rows through the full pipeline, with refuters, on one core."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

import causal_core as cc


@pytest.mark.slow
def test_one_million_rows_binary_backdoor() -> None:
    rng = np.random.default_rng(0)
    n, p = 1_000_000, 10
    Z = rng.normal(size=(n, p))
    beta_x, beta_y = rng.normal(size=p) * 0.3, rng.normal(size=p)
    x = (Z @ beta_x + rng.logistic(size=n) > 0).astype(float)
    y = 1.0 * x + Z @ beta_y + rng.normal(size=n)
    frame = pd.DataFrame(Z, columns=[f"Z{i}" for i in range(p)]).assign(X=x, Y=y)
    graph = "; ".join(f"Z{i} -> X; Z{i} -> Y" for i in range(p)) + "; X -> Y"
    start = time.perf_counter()
    out = cc.analyze(frame, graph, treatment="X", outcome="Y")
    elapsed = time.perf_counter() - start
    assert isinstance(out, cc.CausalResult), cc.report(out)
    # Coverage is T0's job; one CI either covers or not 5% of the time by design.
    # Here: a gross-error bound that a correct kernel fails with probability ~1e-6.
    assert abs(out.primary.point - 1.0) < 5 * (out.primary.se or 0)
    assert elapsed < 60, f"{elapsed:.1f}s"

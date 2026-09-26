"""Scale: a million rows through the full pipeline, with refuters, on one core.

The budget is CPU time on one BLAS thread, not wall-clock time. It used to be
``time.perf_counter() < 60``, which failed at 82.6 s on a host busy with CI
runners: OpenBLAS starts one thread per core (20 here), and on a loaded machine
those threads contend with every other process -- the same call measured 19 s
wall and 151 s CPU with 20 threads, against 11.3 s wall and 11.3 s CPU with one,
on the same host under load. Wall time measured the neighbours.

So the test pins BLAS/OpenMP to one thread (``threadpoolctl``), which is what
"on one core" always claimed, and measures ``time.process_time()`` -- CPU time
of this process, summed over its threads, which with one thread is the kernel's
own work and is not inflated by other processes waiting for the CPU.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

import causal_core as cc

#: CPU seconds for analyze() on 1e6 rows x 10 confounders, one BLAS thread.
#: Measured at 11.3 s (2026-09-26, the gx10 host, load average ~8 on 20 cores).
#: 40 s is ~3.5x that: room for a slower CI core and for what contention still
#: costs a single thread (shared caches, SMT siblings), while a real regression --
#: a per-row Python loop, an O(n^2) step, a refuter run twice as often, an
#: estimator that quadruples its work -- lands well above it.
CPU_BUDGET_SECONDS = 40.0


@pytest.mark.slow
def test_one_million_rows_binary_backdoor() -> None:
    """The full pipeline on 1e6 rows stays inside CPU_BUDGET_SECONDS of one core."""
    rng = np.random.default_rng(0)
    n, p = 1_000_000, 10
    Z = rng.normal(size=(n, p))
    beta_x, beta_y = rng.normal(size=p) * 0.3, rng.normal(size=p)
    x = (Z @ beta_x + rng.logistic(size=n) > 0).astype(float)
    y = 1.0 * x + Z @ beta_y + rng.normal(size=n)
    frame = pd.DataFrame(Z, columns=[f"Z{i}" for i in range(p)]).assign(X=x, Y=y)
    graph = "; ".join(f"Z{i} -> X; Z{i} -> Y" for i in range(p)) + "; X -> Y"
    with threadpool_limits(limits=1):
        wall_start, cpu_start = time.perf_counter(), time.process_time()
        out = cc.analyze(frame, graph, treatment="X", outcome="Y")
        cpu = time.process_time() - cpu_start
        wall = time.perf_counter() - wall_start
    assert isinstance(out, cc.CausalResult), cc.report(out)
    # Coverage is T0's job; one CI either covers or not 5% of the time by design.
    # Here: a gross-error bound that a correct kernel fails with probability ~1e-6.
    assert abs(out.primary.point - 1.0) < 5 * (out.primary.se or 0)
    assert cpu < CPU_BUDGET_SECONDS, f"{cpu:.1f}s CPU on one thread ({wall:.1f}s wall)"

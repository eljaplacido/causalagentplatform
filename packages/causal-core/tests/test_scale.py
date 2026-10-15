"""Scale: a million rows through the full pipeline, with refuters, on one core.

The budget is CPU time on one BLAS thread, not wall-clock time. It used to be
``time.perf_counter() < 60``, which failed at 82.6 s on a host busy with CI
runners: OpenBLAS starts one thread per core (20 here), and on a loaded machine
those threads contend with every other process -- the same call measured 19 s
wall and 151 s CPU with 20 threads, against 11.3 s wall and 11.3 s CPU with one,
on the same host under load. Wall time measured the neighbours.

So the test pins BLAS/OpenMP to one thread (``threadpoolctl``), which is what
"on one core" always claimed, and measures ``time.process_time()``.

CPU time of one thread still measures the neighbours, less (septa-100): memory
bandwidth and shared caches are not isolated. On the gx10 host on 2026-10-15 the
call took 15.5-16.1 s CPU idle, ~37-47 s beside a parallel Rust build, 42-44 s
beside one other gate and 110-150 s beside twelve memory-bound processes. Two ways
of cancelling that inside the test were measured and failed, because no two
workloads have the same memory intensity: the ratio to a fixed numpy IRLS workload
moved 34 (idle) -> 16-21 (loaded), and the ratio to the same pipeline on 1e5 rows
(cache-resident) moved 13 -> 30-47. So contention is removed where it is made --
CI runs one gate at a time on a shared host (.github/workflows/ci.yml, job
``gate``) -- and this test bounds what a single gate beside other work costs.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

import causal_core as cc

#: CPU seconds for analyze() on 1e6 rows x 10 confounders, one BLAS thread.
#: Measured at 15.5-16.1 s idle (2026-10-15, gx10; 11.3 s on 2026-09-26, before
#: Rosenbaum sensitivity, the outcome-model specification test and overlap regions
#: joined the pipeline) and 42-44 s beside one other gate. 60 s covers the second;
#: an O(n^2) step, or an estimator or refuter that quadruples the pipeline's work,
#: lands above it even idle. A regression of 2x or less is not this test's to catch:
#: on a shared host it is inside the noise.
CPU_BUDGET_SECONDS = 60.0


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
    load = os.getloadavg()[0]
    assert cpu < CPU_BUDGET_SECONDS, (
        f"{cpu:.1f}s CPU on one thread ({wall:.1f}s wall), load average {load:.1f} on "
        f"{os.cpu_count()} cores; ~16s idle on gx10 -- a high load points at contention"
    )

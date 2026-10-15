"""Scale: a million rows through the full pipeline, with refuters, on one core.

The budget is USER CPU time on one BLAS thread: the kernel's own instructions,
not the host's.

It used to be ``time.perf_counter() < 60``, which failed at 82.6 s on a host busy
with CI runners: OpenBLAS starts one thread per core (20 here), and on a loaded
machine those threads contend with every other process. Wall time measured the
neighbours. So the test pinned BLAS/OpenMP to one thread (``threadpoolctl``) and
measured ``time.process_time()`` against 40 s -- and failed again (septa-100),
because process_time is user PLUS system time, and the system share is the host's:
with little free memory, every large numpy allocation makes the kernel reclaim,
compact and zero pages. On gx10 on 2026-10-15 the same call took 15.1 s user and
37.3 s system on an idle CPU with 5 GB of 121 free (3.5 million minor page
faults); an hour earlier, with more memory free, 15.5-16 s in all.

User time is not immune either: memory bandwidth is shared. Beside ten
memory-bound processes it rose to 36-41 s (2.5x). Two ways of cancelling that
inside the test were measured and failed, because no two workloads have the same
memory intensity: the ratio to a fixed numpy IRLS workload moved 34 (idle) ->
16-21 (loaded), and the ratio to the same pipeline on 1e5 rows (cache-resident)
moved 13 -> 30-47. So bandwidth contention is limited where it is made -- CI runs
one gate at a time on a shared host (.github/workflows/ci.yml, job ``gate``) --
and the budget below covers the rest.
"""

from __future__ import annotations

import os
import resource
import time

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

import causal_core as cc

#: User CPU seconds for analyze() on 1e6 rows x 10 confounders, one BLAS thread.
#: Measured 14.4-15.1 s idle (2026-10-15, gx10; 11.3 s CPU on 2026-09-26, before
#: Rosenbaum sensitivity, the outcome-model specification test and overlap regions
#: joined the pipeline) and 36-41 s beside ten memory-bound processes. 60 s is 1.5x
#: the second. Seeded check on an idle host: five times the pipeline's work fails it
#: (70.7 s user); four times (~57 s) does not. Any super-linear step fails it (one
#: O(n^2) pass over 1e6 rows is ~1e12 operations). A regression under ~4x is inside
#: what a shared host's bandwidth can cost, and is not this test's to catch.
USER_CPU_BUDGET_SECONDS = 60.0


@pytest.mark.slow
def test_one_million_rows_binary_backdoor() -> None:
    """The full pipeline on 1e6 rows stays inside USER_CPU_BUDGET_SECONDS of one core."""
    rng = np.random.default_rng(0)
    n, p = 1_000_000, 10
    Z = rng.normal(size=(n, p))
    beta_x, beta_y = rng.normal(size=p) * 0.3, rng.normal(size=p)
    x = (Z @ beta_x + rng.logistic(size=n) > 0).astype(float)
    y = 1.0 * x + Z @ beta_y + rng.normal(size=n)
    frame = pd.DataFrame(Z, columns=[f"Z{i}" for i in range(p)]).assign(X=x, Y=y)
    graph = "; ".join(f"Z{i} -> X; Z{i} -> Y" for i in range(p)) + "; X -> Y"
    with threadpool_limits(limits=1):
        before, wall_start = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
        out = cc.analyze(frame, graph, treatment="X", outcome="Y")
        after, wall = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter() - wall_start
    assert isinstance(out, cc.CausalResult), cc.report(out)
    # Coverage is T0's job; one CI either covers or not 5% of the time by design.
    # Here: a gross-error bound that a correct kernel fails with probability ~1e-6.
    assert abs(out.primary.point - 1.0) < 5 * (out.primary.se or 0)
    user = after.ru_utime - before.ru_utime
    system = after.ru_stime - before.ru_stime
    faults = after.ru_minflt - before.ru_minflt
    assert user < USER_CPU_BUDGET_SECONDS, (
        f"{user:.1f}s user CPU on one thread (~15s idle on gx10); {system:.1f}s system, "
        f"{faults} minor page faults, {wall:.1f}s wall, load average "
        f"{os.getloadavg()[0]:.1f} on {os.cpu_count()} cores"
    )

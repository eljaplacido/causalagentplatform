"""
Pinned numerics: the same analysis gives the same bits however many BLAS threads the host has.

A result's digest covers every number in it, and a decision's digest covers the result's
digest, so a replay reproduces a recorded decision only if every float comes out bit for
bit. A multithreaded BLAS does not promise that: it splits a matrix product into blocks
by thread count and sums the blocks in a different order, so ``X.T @ X`` moves in the last
bits between 1, 2, 4 and 8 threads, and the digest moves with it (septa-89: an analysis of
50,000 rows gave a different result digest at each of 1, 2 and 4 threads). Rounding the
numbers before hashing them would not cure that -- a value next to a rounding boundary
still lands on either side -- and would make the digest blind to edits below the
rounding. So the arithmetic is pinned instead: :func:`pinned` runs BLAS on one thread,
the way it runs on a one-core machine, and the digest stays exact over every bit.

What this does not establish: the same bits on a different CPU or BLAS build. An OpenBLAS
built with DYNAMIC_ARCH picks its kernels by CPU model, and two kernels may round
differently even single-threaded; numpy and scipy versions are not pinned here either.
A replay is bit-exact on the same build and CPU family, at any thread count.

The limit is process-wide (BLAS keeps one), so it is reference-counted: concurrent
analyses in one process all run pinned, and the host's own setting comes back when the
last one returns.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from threadpoolctl import threadpool_limits

#: The BLAS thread count every analysis runs at. One is the only count every host has.
BLAS_THREADS = 1

_lock = threading.Lock()
_depth = 0
_limiter: Any = None


@contextmanager
def pinned() -> Iterator[None]:
    """Run the body with BLAS on :data:`BLAS_THREADS` thread(s); restore when the last exits."""
    global _depth, _limiter
    with _lock:
        if _depth == 0:
            _limiter = threadpool_limits(limits=BLAS_THREADS, user_api="blas")
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0 and _limiter is not None:
                _limiter.restore_original_limits()
                _limiter = None

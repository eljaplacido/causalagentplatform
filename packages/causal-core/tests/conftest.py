from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from causal_core import LinearSCM, parse_graph

CONFOUNDED = "Z -> X; Z -> Y; X -> Y; W -> Y"


def simulate(
    graph: str,
    coef: dict[tuple[str, str], float],
    n: int = 2000,
    seed: int = 0,
    binary: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    scm = LinearSCM(graph=parse_graph(graph), coef=coef, binary=binary)
    return scm.sample(n, np.random.default_rng(seed))


@pytest.fixture
def confounded_binary() -> pd.DataFrame:
    return simulate(
        CONFOUNDED,
        {("Z", "X"): 0.8, ("Z", "Y"): 1.2, ("X", "Y"): 2.0, ("W", "Y"): 0.5},
        binary=frozenset({"X"}),
    )


@pytest.fixture
def confounded_continuous() -> pd.DataFrame:
    return simulate(
        CONFOUNDED, {("Z", "X"): 0.8, ("Z", "Y"): 1.2, ("X", "Y"): 2.0, ("W", "Y"): 0.5}
    )

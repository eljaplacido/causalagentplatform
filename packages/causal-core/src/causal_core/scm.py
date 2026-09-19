"""
Linear structural causal models with closed-form effects.

Used by the T0 correctness gate, and useful on its own to check a pipeline
against known truth before trusting it on real data. Each variable is a linear
function of its parents plus noise; a node listed in ``binary`` is instead
``1[linear predictor + logistic noise > 0]``. Because every variable depends
linearly on its parents, the effect of moving the treatment from 0 to 1 is the
sum over directed paths of the products of path coefficients -- exactly, for
binary and continuous treatments alike.

Nodes in the graph's ``latent`` list are simulated and then dropped from the
returned data; bidirected edges become an explicit shared standard-normal noise
term.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

from causal_core.contract import CausalGraph
from causal_core.graph import Graph, as_causal_graph


@dataclass
class LinearSCM:
    graph: CausalGraph
    coef: dict[tuple[str, str], float]
    noise_sd: dict[str, float] = field(default_factory=dict)
    intercept: dict[str, float] = field(default_factory=dict)
    binary: frozenset[str] = frozenset()
    #: Optional nonlinear terms: node -> f(frame) added to its structural equation.
    extra: Mapping[str, Callable[[pd.DataFrame], np.ndarray]] = field(default_factory=dict)

    @classmethod
    def random(
        cls,
        graph: CausalGraph | str,
        rng: np.random.Generator,
        binary: frozenset[str] = frozenset(),
        coef_range: tuple[float, float] = (0.5, 1.5),
    ) -> LinearSCM:
        spec = as_causal_graph(graph)
        g = Graph.from_spec(spec)
        coef = {}
        for a, b in g.dag.edges:
            mag = rng.uniform(*coef_range)
            coef[(a, b)] = float(mag * rng.choice([-1.0, 1.0]))
        return cls(graph=spec, coef=coef, binary=binary)

    @property
    def _g(self) -> Graph:
        return Graph.from_spec(self.graph)

    def sample(self, n: int, rng: np.random.Generator, observed_only: bool = True) -> pd.DataFrame:
        g = self._g
        cols: dict[str, np.ndarray] = {}
        for v in nx.lexicographical_topological_sort(g.dag):
            val = np.full(n, self.intercept.get(v, 0.0))
            for p in g.dag.predecessors(v):
                val = val + self.coef.get((p, v), 1.0) * cols[p]
            if v in self.extra:
                val = val + self.extra[v](pd.DataFrame(cols))
            if v in self.binary:
                u = rng.uniform(size=n)
                val = (val + np.log(u / (1 - u)) > 0).astype(float)
            else:
                val = val + rng.normal(0.0, self.noise_sd.get(v, 1.0), size=n)
            cols[v] = val
        frame = pd.DataFrame(cols)
        if observed_only:
            frame = frame[[c for c in frame.columns if c in g.observed]]
        return frame

    def total_effect(self, treatment: str, outcome: str) -> float:
        """d E[outcome | do(treatment = t)] / dt: the ATE of a unit change.

        Exact when ``extra`` terms do not depend on the treatment or its
        descendants (they may depend on anything else, e.g. a confounder).
        """
        g = self._g
        total = 0.0
        for path in nx.all_simple_paths(g.dag, treatment, outcome):
            prod = 1.0
            for a, b in zip(path, path[1:], strict=False):
                if b in self.binary:
                    raise ValueError(
                        f"{b} is binary and lies on a causal path: the effect is not linear"
                    )
                prod *= self.coef.get((a, b), 1.0)
            total += prod
        return total

"""Metamorphic relations: transformations of the input whose effect on the answer is known.

The gates check estimates against effects set in simulation. These tests check something the
gates cannot: that the kernel's answer changes, under a transformation of the data, exactly
as the transformation says it must, and in no other way. A relation needs no ground truth, so
it catches a different class of bug: a column read by position, a constant that should scale,
a variance that ignores n.

Measured when these were written (septa-99), on 3000 rows and the AIPW estimator:

- Renaming every column, reordering columns, or adding a column the graph does not name
  leaves the answer bit-identical.
- An affine change of the outcome's units (y -> a*y + b) scales the estimate and its standard
  error by a, exactly.
- Shuffling the rows, or swapping which arm is coded 1, is NOT bit-identical. Cross-fitting
  assigns folds by row position, so the nuisance models see different splits. The change is
  about 0.05 standard errors. It is held below 0.25 SE here, so a real order dependence
  would show. It also means two row orders of one dataset give two result digests (see
  septa-89 for the other source of digest drift).
- Doubling the data by duplicating every row divides the standard error by sqrt(2), to
  within 5%.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import causal_core as cc
from causal_core.contract import ResultStatus

GRAPH = "Z -> X; Z -> Y; X -> Y"
TRUE_EFFECT = 2.0


@pytest.fixture(scope="module")
def data() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 3000
    z = rng.normal(size=n)
    x = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(int)
    y = TRUE_EFFECT * x + 1.5 * z + rng.normal(size=n)
    return pd.DataFrame({"Z": z, "X": x, "Y": y})


def _primary(df: pd.DataFrame, graph: str = GRAPH, treatment: str = "X", outcome: str = "Y"):
    out = cc.analyze(df, graph, treatment=treatment, outcome=outcome)
    assert out.kind == "result", out
    return out.primary


@pytest.fixture(scope="module")
def base(data: pd.DataFrame):
    p = _primary(data)
    # Anchored to the simulation's truth, so every relation below starts from a right answer.
    assert p.ci is not None and p.ci.lo <= TRUE_EFFECT <= p.ci.hi
    return p


def _same(a, b) -> None:
    assert a.estimator == b.estimator
    assert a.point == b.point
    assert a.se == b.se


def test_column_order_is_irrelevant(data: pd.DataFrame, base) -> None:
    _same(_primary(data[["Y", "X", "Z"]]), base)


def test_names_are_irrelevant(data: pd.DataFrame, base) -> None:
    renamed = data.rename(columns={"Z": "age", "X": "emailed", "Y": "spend"})
    _same(_primary(renamed, "age -> emailed; age -> spend; emailed -> spend", "emailed", "spend"), base)


def test_a_column_outside_the_graph_is_ignored(data: pd.DataFrame, base) -> None:
    noisy = data.assign(unrelated=np.random.default_rng(9).normal(size=len(data)))
    _same(_primary(noisy), base)


@pytest.mark.parametrize(("a", "b"), [(10.0, 7.0), (0.01, -3.0), (-1.0, 0.0)])
def test_outcome_units_scale_the_answer(data: pd.DataFrame, base, a: float, b: float) -> None:
    p = _primary(data.assign(Y=a * data["Y"] + b))
    assert p.point == pytest.approx(a * base.point, rel=1e-9)
    assert p.se == pytest.approx(abs(a) * base.se, rel=1e-9)


def test_row_order_moves_the_answer_by_noise_only(data: pd.DataFrame, base) -> None:
    for seed in (1, 2, 3):
        p = _primary(data.sample(frac=1.0, random_state=seed).reset_index(drop=True))
        assert abs(p.point - base.point) < 0.25 * base.se, seed
        assert p.se == pytest.approx(base.se, rel=0.05)


def test_swapping_the_arms_flips_the_sign(data: pd.DataFrame, base) -> None:
    p = _primary(data.assign(X=1 - data["X"]))
    assert abs(p.point + base.point) < 0.25 * base.se
    assert p.se == pytest.approx(base.se, rel=0.05)


def test_twice_the_data_shrinks_the_interval_by_root_two(data: pd.DataFrame, base) -> None:
    p = _primary(pd.concat([data, data], ignore_index=True))
    assert base.se / p.se == pytest.approx(math.sqrt(2), rel=0.05)
    assert abs(p.point - base.point) < 0.25 * base.se


def test_a_randomized_design_obeys_the_same_relations() -> None:
    rng = np.random.default_rng(11)
    n = 2000
    w = rng.normal(size=n)
    x = rng.integers(0, 2, size=n)
    y = 0.5 * x + w + rng.normal(size=n)
    df = pd.DataFrame({"W": w, "X": x, "Y": y})
    graph = "X -> Y; W -> Y"
    out = cc.analyze(df, graph, treatment="X", outcome="Y", design="randomized")
    assert out.kind == "result" and out.status is not ResultStatus.contested
    scaled = cc.analyze(df.assign(Y=100 * df["Y"]), graph, treatment="X", outcome="Y", design="randomized")
    assert scaled.primary.point == pytest.approx(100 * out.primary.point, rel=1e-9)
    assert scaled.primary.se == pytest.approx(100 * out.primary.se, rel=1e-9)
    renamed = cc.analyze(df.rename(columns={"X": "arm"}), "arm -> Y; W -> Y", treatment="arm", outcome="Y",
                         design="randomized")
    _same(renamed.primary, out.primary)

"""
A specification test of the linear outcome model regression adjustment rests on.

Triangulation compares estimators, and every comparison of estimates is limited by the
standard error of their difference. Under outcome-model misspecification that standard
error is large, because the misfit enters the misspecified estimator's influence function:
against T0's planted misspecification (a confounder entering the outcome as Z**2), no
estimator contrast reaches power 0.8 below n of about 10,000. Testing the model directly
does: the omitted curvature is estimated from residuals that do not contain it.

Per arm the model regression adjustment fits (both arms for the ATE, the control arm for
the ATT), the outcome is regressed on the linear model plus added terms -- the square of
each non-binary covariate and the square of the arm's linear fitted value (Ramsey's RESET
term) -- and an HC2 Wald test asks whether all added coefficients are zero, jointly over
the arms. The p-value joins triangulation's Holm family (see ``pipeline._triangulate``);
a rejection makes the result ``contested`` like any other disagreement.

What it does not say: that the estimate is biased. A nonlinearity that leaves regression
adjustment unbiased is flagged too. Pre-registered in benchmarks/t0 (v4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

from causal_core.contract import Strategy
from causal_core.estimators import F, Fit, Prepared
from causal_core.estimators._linalg import add_intercept, ols

#: An arm is tested only with at least this many observations per parameter of its
#: augmented model; below it, HC2 Wald tests over-reject.
MIN_OBS_PER_PARAMETER = 10

#: A candidate term whose part not explained by the model already in hand has less than
#: this share of its own variance is collinear with it and is dropped.
COLLINEAR_TOL = 1e-8


@dataclass(frozen=True)
class SpecificationTest:
    model: str
    arms: tuple[str, ...]
    terms: tuple[str, ...]
    statistic: float
    df: int
    p_value: float

    def describe(self) -> str:
        return (
            f"specification test of the linear outcome model behind {self.model} "
            f"({', '.join(self.arms)}; added {', '.join(self.terms)}): "
            f"chi2 = {self.statistic:.3g} on {self.df} df, p = {self.p_value:.2g}"
        )


@dataclass(frozen=True)
class _ArmTest:
    statistic: float
    df: int
    terms: tuple[str, ...]


def _standardize(v: F) -> F | None:
    sd = float(np.std(v))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return np.asarray((v - v.mean()) / sd, dtype=float)


def arm_test(y: F, X: F, names: Sequence[str]) -> _ArmTest | None:
    """HC2 Wald test that the added curvature terms have zero coefficients in ``y ~ 1 + X``."""
    n = y.shape[0]
    L = add_intercept(X)
    base = ols(y, L)
    candidates: list[tuple[str, F]] = []
    for j, name in enumerate(names):
        col = X[:, j]
        if np.unique(col).size > 2:
            z = _standardize(col)
            if z is not None:
                candidates.append((f"{name}^2", z**2))
    s = _standardize(L @ base.beta)
    if s is not None:
        candidates.append(("fitted^2", s**2))
    # Keep a term only if it adds a direction the linear model and the kept terms lack.
    basis = L
    kept: list[tuple[str, F]] = []
    for name, h in candidates:
        hc = h - h.mean()
        total = float(hc @ hc)
        if total <= 0:
            continue
        coef, *_ = np.linalg.lstsq(basis, h, rcond=None)
        r = h - basis @ coef
        if float(r @ r) > COLLINEAR_TOL * total:
            kept.append((name, h))
            basis = np.column_stack([basis, h])
    if not kept:
        return None
    W = basis
    if n < MIN_OBS_PER_PARAMETER * W.shape[1]:
        return None
    fit = ols(y, W)
    k = L.shape[1]
    inf = fit.influence(hc2=True)[:, k:]
    cov = inf.T @ inf / n**2
    g = fit.beta[k:]
    rank = int(np.linalg.matrix_rank(cov))
    if rank == 0:
        return None
    stat = float(g @ np.linalg.pinv(cov, hermitian=True) @ g)
    return _ArmTest(statistic=stat, df=rank, terms=tuple(t for t, _ in kept))


def outcome_model_specification(
    d: Prepared, strategy: Strategy, fits: Sequence[Fit]
) -> SpecificationTest | None:
    """The test, when it applies: binary treatment, back-door, an outcome-model estimator ran."""
    if d.treatment_kind != "binary" or strategy is not Strategy.backdoor or d.X.shape[1] == 0:
        return None
    models = [f.record.estimator for f in fits if f.record.family == "outcome_model"]
    if not models:
        return None
    arms = ((0.0, "control arm"),) if d.estimand == "att" else ((1.0, "treated arm"),
                                                                   (0.0, "control arm"))
    stat, df = 0.0, 0
    tested: list[str] = []
    terms: list[str] = []
    for level, label in arms:
        m = d.t == level
        res = arm_test(d.y[m], d.X[m], d.covariate_names)
        if res is None:
            continue
        stat += res.statistic
        df += res.df
        tested.append(label)
        terms.extend(t for t in res.terms if t not in terms)
    if df == 0:
        return None
    return SpecificationTest(
        model=models[0],
        arms=tuple(tested),
        terms=tuple(terms),
        statistic=stat,
        df=df,
        p_value=float(stats.chi2.sf(stat, df)),
    )

"""
Built-in estimators. Every one returns an analytic standard error derived from
its influence function, so every interval is testable for coverage (the T0 gate
does exactly that) and no interval is ever a placeholder.

Families, so triangulation compares estimators that can fail differently:

* ``design``          -- difference in means (randomized experiments)
* ``outcome_model``   -- regression adjustment (Lin 2013 interacted OLS; OLS)
* ``weighting``       -- Hajek inverse-propensity weighting
* ``doubly_robust``   -- cross-fitted AIPW
* ``orthogonal``      -- cross-fitted double machine learning (partially linear)
* ``mediation``       -- linear front-door (product of coefficients)
* ``iv`` / ``proximal`` -- two-stage least squares variants
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats

from causal_core.contract import AnalysisPolicy, EstimateRecord, Interval, Strategy
from causal_core.estimators import F, Fit, Prepared, TreatmentKind
from causal_core.estimators._linalg import (
    add_intercept,
    folds,
    logistic,
    ols,
    robust_wald_f,
    stratified_folds,
    two_sls,
)

# ── shared helpers ────────────────────────────────────────────────────────────


def wald_interval(point: float, se: float | None, level: float, method: str) -> Interval | None:
    if se is None or not np.isfinite(se) or se < 0:
        return None
    z = float(stats.norm.ppf(0.5 + level / 2))
    return Interval(level=level, lo=point - z * se, hi=point + z * se, method=method)


def _se_from_if(inf: F) -> float:
    n = inf.shape[0]
    return float(np.sqrt(np.sum((inf - inf.mean()) ** 2)) / n)


def _estimand(d: Prepared) -> str:
    if d.treatment_kind == "binary":
        t0, t1 = d.levels
        return f"ATE: E[Y(T={t1:g}) - Y(T={t0:g})]"
    if d.scale != 1.0:
        t0, t1 = d.levels
        return f"E[Y(T={t1:g}) - Y(T={t0:g})], effect assumed linear in T"
    return "effect of a one-unit increase in T, assumed linear in T"


def _record(
    est: _Base,
    d: Prepared,
    policy: AnalysisPolicy,
    point: float,
    inf: F | None,
    se: float | None = None,
    details: dict[str, float | str] | None = None,
    extra_assumptions: tuple[str, ...] = (),
) -> Fit:
    if se is None and inf is not None:
        se = _se_from_if(inf)
    ci = wald_interval(point, se, policy.ci_level, f"wald/{est.se_method}")
    rec = EstimateRecord(
        estimator=est.name,
        family=est.family,
        estimand=_estimand(d),
        point=float(point),
        se=None if se is None else float(se),
        ci=ci,
        ci_null_reason=None if ci else "standard error unavailable",
        n_used=d.n,
        assumptions=est.assumptions + extra_assumptions,
        details=details or {},
    )
    return Fit(record=rec, influence=inf)


@dataclass(frozen=True)
class _Base:
    name: str = ""
    family: str = ""
    strategies: frozenset[Strategy] = frozenset()
    treatment_kinds: frozenset[TreatmentKind] = frozenset()
    priority: int = 100
    assumptions: tuple[str, ...] = ()
    se_method: str = "influence-function"

    def supports(self, d: Prepared, strategy: Strategy) -> bool:
        return True


# ── learners for cross-fitting ────────────────────────────────────────────────


def _fit_predict_regression(y: F, X: F, X_new: F, policy: AnalysisPolicy) -> F:
    if policy.learners == "flexible" and X.shape[1] > 0:
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError("learners='flexible' needs: pip install 'causal-core[ml]'") from exc
        model = HistGradientBoostingRegressor(random_state=policy.seed)
        model.fit(X, y)
        return np.asarray(model.predict(X_new), dtype=float)
    Xi, Xn = add_intercept(X), add_intercept(X_new)
    ridge = 1e-6 if Xi.shape[0] <= Xi.shape[1] + 1 else 0.0
    beta = ols(y, Xi, ridge=ridge).beta
    return np.asarray(Xn @ beta, dtype=float)


def _fit_predict_propensity(t: F, X: F, X_new: F, policy: AnalysisPolicy) -> F:
    if X.shape[1] == 0:
        return np.full(X_new.shape[0], float(t.mean()))
    if policy.learners == "flexible":
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
        except ImportError as exc:  # pragma: no cover
            raise ImportError("learners='flexible' needs: pip install 'causal-core[ml]'") from exc
        model = HistGradientBoostingClassifier(random_state=policy.seed)
        model.fit(X, t.astype(int))
        return np.asarray(model.predict_proba(X_new)[:, 1], dtype=float)
    fit = logistic(t, add_intercept(X))
    eta = np.clip(add_intercept(X_new) @ fit.gamma, -35, 35)
    return np.asarray(1.0 / (1.0 + np.exp(-eta)), dtype=float)


# ── design: difference in means ──────────────────────────────────────────────


@dataclass(frozen=True)
class DifferenceInMeans(_Base):
    name: str = "difference_in_means"
    family: str = "design"
    strategies: frozenset[Strategy] = frozenset({Strategy.randomized, Strategy.backdoor})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary"})
    priority: int = 10
    assumptions: tuple[str, ...] = ("treatment independent of potential outcomes",)
    se_method: str = "neyman"

    def supports(self, d: Prepared, strategy: Strategy) -> bool:
        return strategy is Strategy.randomized or d.X.shape[1] == 0

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        t, y = d.t, d.y
        n1, n0 = float(t.sum()), float(len(t) - t.sum())
        m1, m0 = float(y[t == 1].mean()), float(y[t == 0].mean())
        s1, s0 = float(y[t == 1].var(ddof=1)), float(y[t == 0].var(ddof=1))
        se = float(np.sqrt(s1 / n1 + s0 / n0))
        p1 = n1 / len(t)
        if_mu1 = t * (y - m1) / p1
        if_mu0 = (1 - t) * (y - m0) / (1 - p1)
        fit = _record(self, d, policy, m1 - m0, if_mu1 - if_mu0, se=se)
        fit.extras.update(mu1=m1, mu0=m0, if_mu1=if_mu1, if_mu0=if_mu0)
        return fit


# ── outcome models ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinRegression(_Base):
    """Interacted regression adjustment (Lin 2013); superpopulation IF."""

    name: str = "regression_adjustment"
    family: str = "outcome_model"
    strategies: frozenset[Strategy] = frozenset({Strategy.randomized, Strategy.backdoor})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary"})
    priority: int = 30
    assumptions: tuple[str, ...] = ("outcome linear in the covariates within each arm",)
    se_method: str = "hc2+covariate-mean"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        t, y, X = d.t, d.y, d.X
        p = X.shape[1]
        xbar = X.mean(axis=0) if p else np.zeros(0)
        Xc = X - xbar
        D = np.column_stack([np.ones(d.n), t, Xc, t[:, None] * Xc]) if p else np.column_stack(
            [np.ones(d.n), t]
        )
        f = ols(y, D)
        inf = f.influence(hc2=True)[:, 1]
        if p:
            inf = inf + Xc @ f.beta[2 + p : 2 + 2 * p]
        return _record(self, d, policy, float(f.beta[1]), inf)


@dataclass(frozen=True)
class OLSAdjustment(_Base):
    name: str = "ols_adjustment"
    family: str = "outcome_model"
    strategies: frozenset[Strategy] = frozenset({Strategy.randomized, Strategy.backdoor})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"continuous"})
    priority: int = 30
    assumptions: tuple[str, ...] = ("outcome linear in treatment and covariates",)
    se_method: str = "hc2"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        D = np.column_stack([np.ones(d.n), d.t, d.X])
        f = ols(d.y, D)
        inf = f.influence(hc2=True)[:, 1] * d.scale
        return _record(self, d, policy, float(f.beta[1]) * d.scale, inf)


# ── weighting ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HajekIPW(_Base):
    """Normalized IPW with a logistic propensity; the SE accounts for fitting it."""

    name: str = "ipw_hajek"
    family: str = "weighting"
    strategies: frozenset[Strategy] = frozenset({Strategy.backdoor})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary"})
    priority: int = 40
    assumptions: tuple[str, ...] = ("propensity logistic-linear in the covariates",)
    se_method: str = "m-estimation"

    def supports(self, d: Prepared, strategy: Strategy) -> bool:
        return d.X.shape[1] > 0

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        t, y = d.t, d.y
        Xi = add_intercept(d.X)
        lg = logistic(t, Xi)
        lo, hi = policy.propensity_bounds
        e = np.clip(lg.p, lo, hi)
        inside = ((lg.p > lo) & (lg.p < hi)).astype(float)
        w1, w0 = t / e, (1 - t) / (1 - e)
        mu1 = float(np.sum(w1 * y) / np.sum(w1))
        mu0 = float(np.sum(w0 * y) / np.sum(w0))
        phi_gamma = (Xi * (t - lg.p)[:, None]) @ lg.hessian_inv.T
        de = (e * (1 - e) * inside)[:, None] * Xi
        g1 = np.mean((-t * (y - mu1) / e**2)[:, None] * de, axis=0)
        g0 = np.mean(((1 - t) * (y - mu0) / (1 - e) ** 2)[:, None] * de, axis=0)
        if_mu1 = (t * (y - mu1) / e + phi_gamma @ g1) / np.mean(w1)
        if_mu0 = ((1 - t) * (y - mu0) / (1 - e) + phi_gamma @ g0) / np.mean(w0)
        fit = _record(
            self,
            d,
            policy,
            mu1 - mu0,
            if_mu1 - if_mu0,
            details={"share_clipped": float(1 - inside.mean())},
        )
        fit.extras.update(mu1=mu1, mu0=mu0, if_mu1=if_mu1, if_mu0=if_mu0)
        return fit


# ── doubly robust ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AIPW(_Base):
    name: str = "aipw"
    family: str = "doubly_robust"
    strategies: frozenset[Strategy] = frozenset({Strategy.backdoor, Strategy.randomized})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary"})
    priority: int = 20
    assumptions: tuple[str, ...] = (
        "either the propensity model or the outcome model is correctly specified",
    )
    se_method: str = "influence-function (cross-fitted)"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        t, y, X = d.t, d.y, d.X
        n = d.n
        e_hat, mu1_hat, mu0_hat = np.empty(n), np.empty(n), np.empty(n)
        all_idx = np.arange(n)
        for test in stratified_folds(t, policy.cross_fit_folds, rng):
            train = np.setdiff1d(all_idx, test, assume_unique=True)
            Xtr, ttr, ytr, Xte = X[train], t[train], y[train], X[test]
            e_hat[test] = _fit_predict_propensity(ttr, Xtr, Xte, policy)
            m1, m0 = ttr == 1, ttr == 0
            mu1_hat[test] = _fit_predict_regression(ytr[m1], Xtr[m1], Xte, policy)
            mu0_hat[test] = _fit_predict_regression(ytr[m0], Xtr[m0], Xte, policy)
        lo, hi = policy.propensity_bounds
        clipped = float(np.mean((e_hat <= lo) | (e_hat >= hi)))
        e_hat = np.clip(e_hat, lo, hi)
        psi1 = mu1_hat + t * (y - mu1_hat) / e_hat
        psi0 = mu0_hat + (1 - t) * (y - mu0_hat) / (1 - e_hat)
        mu1, mu0 = float(psi1.mean()), float(psi0.mean())
        inf = (psi1 - psi0) - (mu1 - mu0)
        fit = _record(
            self,
            d,
            policy,
            mu1 - mu0,
            inf,
            details={"share_clipped": clipped, "folds": float(policy.cross_fit_folds)},
            extra_assumptions=(f"nuisance learners: {policy.learners}",),
        )
        fit.extras.update(mu1=mu1, mu0=mu0, if_mu1=psi1 - mu1, if_mu0=psi0 - mu0)
        return fit


@dataclass(frozen=True)
class DMLPartiallyLinear(_Base):
    name: str = "dml_plr"
    family: str = "orthogonal"
    strategies: frozenset[Strategy] = frozenset({Strategy.backdoor, Strategy.randomized})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"continuous"})
    priority: int = 20
    assumptions: tuple[str, ...] = ("partially linear model: effect constant in T and X",)
    se_method: str = "influence-function (cross-fitted)"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        t, y, X = d.t, d.y, d.X
        n = d.n
        m_hat, l_hat = np.empty(n), np.empty(n)
        all_idx = np.arange(n)
        for test in folds(n, policy.cross_fit_folds, rng):
            train = np.setdiff1d(all_idx, test, assume_unique=True)
            m_hat[test] = _fit_predict_regression(t[train], X[train], X[test], policy)
            l_hat[test] = _fit_predict_regression(y[train], X[train], X[test], policy)
        V, U = t - m_hat, y - l_hat
        vv = float(np.mean(V * V))
        theta = float(np.mean(V * U) / vv)
        psi = (U - theta * V) * V / vv
        return _record(
            self,
            d,
            policy,
            theta * d.scale,
            psi * d.scale,
            extra_assumptions=(f"nuisance learners: {policy.learners}",),
        )


# ── front-door ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrontdoorLinear(_Base):
    name: str = "frontdoor_linear"
    family: str = "mediation"
    strategies: frozenset[Strategy] = frozenset({Strategy.frontdoor})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary", "continuous"})
    priority: int = 20
    assumptions: tuple[str, ...] = ("linear structural equations for mediators and outcome",)
    se_method: str = "delta-method influence function"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        assert d.mediators is not None
        M = d.mediators
        n, m = M.shape
        D1 = np.column_stack([np.ones(n), d.t])
        fy = ols(d.y, np.column_stack([np.ones(n), M, d.t]))
        inf_y = fy.influence(hc2=False)
        theta = 0.0
        inf = np.zeros(n)
        for j in range(m):
            fj = ols(M[:, j], D1)
            a_j = float(fj.beta[1])
            b_j = float(fy.beta[1 + j])
            theta += a_j * b_j
            inf += b_j * fj.influence(hc2=False)[:, 1] + a_j * inf_y[:, 1 + j]
        return _record(self, d, policy, theta * d.scale, inf * d.scale)


# ── instruments and proxies ───────────────────────────────────────────────────


@dataclass(frozen=True)
class TwoStageLeastSquares(_Base):
    name: str = "tsls"
    family: str = "iv"
    strategies: frozenset[Strategy] = frozenset({Strategy.iv})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary", "continuous"})
    priority: int = 20
    assumptions: tuple[str, ...] = ("constant treatment effect (linear IV model)",)
    se_method: str = "robust sandwich"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        assert d.instruments is not None
        exog = add_intercept(d.X)
        beta, inf, _ = two_sls(d.y, d.t[:, None], exog, d.instruments)
        k = exog.shape[1]
        f_stat = robust_wald_f(
            d.t,
            np.column_stack([exog, d.instruments]),
            list(range(k, k + d.instruments.shape[1])),
        )
        return _record(
            self,
            d,
            policy,
            float(beta[-1]) * d.scale,
            inf[:, -1] * d.scale,
            details={"first_stage_f": f_stat},
        )


@dataclass(frozen=True)
class ProximalTwoStage(_Base):
    """Proximal 2SLS: outcome proxies instrumented by treatment proxies."""

    name: str = "proximal_2sls"
    family: str = "proximal"
    strategies: frozenset[Strategy] = frozenset({Strategy.proximal})
    treatment_kinds: frozenset[TreatmentKind] = frozenset({"binary", "continuous"})
    priority: int = 20
    assumptions: tuple[str, ...] = ("linear outcome bridge function",)
    se_method: str = "robust sandwich"

    def fit(self, d: Prepared, policy: AnalysisPolicy, rng: np.random.Generator) -> Fit:
        assert d.outcome_proxies is not None and d.treatment_proxies is not None
        exog = add_intercept(np.column_stack([d.t, d.X]) if d.X.size else d.t[:, None])
        beta, inf, _ = two_sls(d.y, d.outcome_proxies, exog, d.treatment_proxies)
        return _record(self, d, policy, float(beta[1]) * d.scale, inf[:, 1] * d.scale)


BUILTIN: tuple[Any, ...] = (
    DifferenceInMeans(),
    AIPW(),
    LinRegression(),
    HajekIPW(),
    DMLPartiallyLinear(),
    OLSAdjustment(),
    FrontdoorLinear(),
    TwoStageLeastSquares(),
    ProximalTwoStage(),
)

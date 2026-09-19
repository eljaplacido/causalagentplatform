"""
Small, exact numerical building blocks shared by the estimators.

Everything returns per-observation influence functions (IFs) as well as point
estimates: ``estimate - truth ≈ mean(IF)``. IFs give standard errors without a
bootstrap, and let two estimators on the same data be compared with the
correlation between them taken into account (see triangulation).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

F = NDArray[np.float64]


def add_intercept(X: F) -> F:
    return np.column_stack([np.ones(X.shape[0]), X]) if X.size else np.ones((X.shape[0], 1))


@dataclass(frozen=True)
class OLS:
    X: F
    beta: F
    resid: F
    bread: F  # (X'X / n)^-1
    leverage: F

    @property
    def n(self) -> int:
        return int(self.X.shape[0])

    @property
    def p(self) -> int:
        return int(self.X.shape[1])

    def influence(self, hc2: bool = True) -> F:
        """n x p matrix; row i is the IF of beta for observation i."""
        e = self.resid / np.sqrt(np.clip(1.0 - self.leverage, 1e-12, None)) if hc2 else self.resid
        return (self.X * e[:, None]) @ self.bread.T


def ols(y: F, X: F, ridge: float = 0.0) -> OLS:
    """OLS of y on X (no intercept added). HC2 leverage kept for robust SEs."""
    n, p = X.shape
    xtx = X.T @ X / n
    if ridge:
        xtx = xtx + ridge * np.eye(p)
    # A pseudo-inverse with a relative cutoff, not inv(): a near-singular X'X (a binary
    # covariate that is constant inside one arm of one cross-fitting fold, say) makes
    # inv() return a huge finite matrix and predictions of order 1e16, silently. Here a
    # direction the data do not vary in is treated as unidentified and given weight 0.
    # For a well-conditioned X'X this equals the inverse.
    bread = np.linalg.pinv(xtx, rcond=1e-10, hermitian=True)
    beta = bread @ (X.T @ y / n)
    resid = y - X @ beta
    leverage = np.einsum("ij,jk,ik->i", X, bread, X) / n
    return OLS(X=X, beta=beta, resid=resid, bread=bread, leverage=leverage)


def hc2_cov(fit: OLS) -> F:
    inf = fit.influence(hc2=True)
    return inf.T @ inf / fit.n**2


def rank_deficient(X: F, tol: float = 1e-10) -> bool:
    if X.shape[1] == 0:
        return False
    s = np.linalg.svd(X / np.sqrt(X.shape[0]), compute_uv=False)
    return bool(s[-1] < tol * max(s[0], 1.0))


@dataclass(frozen=True)
class Logit:
    gamma: F
    p: F
    converged: bool
    separated: bool
    hessian_inv: F  # (E[p(1-p) x x'])^-1


def logistic(t: F, X: F, max_iter: int = 60, ridge: float = 1e-8) -> Logit:
    """Logistic regression by IRLS (X should include an intercept column)."""
    n, k = X.shape
    gamma = np.zeros(k)
    converged = False
    separated = False
    for _ in range(max_iter):
        eta = np.clip(X @ gamma, -35, 35)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1 - p), 1e-12, None)
        H = (X * w[:, None]).T @ X / n + ridge * np.eye(k)
        g = X.T @ (t - p) / n
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        gamma = gamma + step
        if np.max(np.abs(step)) < 1e-9:
            converged = True
            break
        if np.max(np.abs(gamma)) > 30:
            separated = True
            break
    eta = np.clip(X @ gamma, -35, 35)
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1 - p), 1e-12, None)
    H = (X * w[:, None]).T @ X / n + ridge * np.eye(k)
    try:
        hinv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        hinv = np.linalg.pinv(H)
    if not converged and not separated and np.max(np.abs(gamma)) > 15:
        separated = True
    return Logit(gamma=gamma, p=p, converged=converged, separated=separated, hessian_inv=hinv)


def folds(n: int, k: int, rng: np.random.Generator) -> list[NDArray[np.intp]]:
    idx = rng.permutation(n)
    return [np.sort(f) for f in np.array_split(idx, k)]


def stratified_folds(t: F, k: int, rng: np.random.Generator) -> list[NDArray[np.intp]]:
    """Folds that keep the treated share roughly equal (binary t)."""
    out: list[list[int]] = [[] for _ in range(k)]
    for level in (0.0, 1.0):
        idx = rng.permutation(np.flatnonzero(t == level))
        for j, chunk in enumerate(np.array_split(idx, k)):
            out[j].extend(chunk.tolist())
    return [np.sort(np.asarray(f, dtype=np.intp)) for f in out]


def two_sls(y: F, endog: F, exog: F, instruments: F) -> tuple[F, F, F]:
    """2SLS with robust (HC0) covariance.

    Regressors are ``[exog, endog]``; the instrument matrix is ``[exog, instruments]``.
    ``exog`` should contain the intercept. Returns (beta, influence n x p, fitted regressors).
    """
    n = y.shape[0]
    Xr = np.column_stack([exog, endog])
    Zf = np.column_stack([exog, instruments])
    zz_inv = np.linalg.pinv(Zf.T @ Zf / n)
    Xhat = Zf @ (zz_inv @ (Zf.T @ Xr / n))
    A = Xhat.T @ Xr / n
    try:
        A_inv = np.linalg.inv(A)
    except np.linalg.LinAlgError:
        A_inv = np.linalg.pinv(A)
    beta = A_inv @ (Xhat.T @ y / n)
    u = y - Xr @ beta
    inf = (Xhat * u[:, None]) @ A_inv.T
    return beta, inf, Xhat


def robust_wald_f(y: F, X: F, test_cols: list[int]) -> float:
    """Heteroskedasticity-robust Wald F for H0: beta[test_cols] = 0."""
    fit = ols(y, X)
    V = hc2_cov(fit)
    b = fit.beta[test_cols]
    Vs = V[np.ix_(test_cols, test_cols)]
    try:
        stat = float(b @ np.linalg.solve(Vs, b))
    except np.linalg.LinAlgError:
        stat = float(b @ np.linalg.pinv(Vs) @ b)
    return stat / len(test_cols)

"""
Design checks: is the data fit to answer the identified question at all?

Runs after identification (which fixes the variables the analysis needs) and
before any estimator. Every failed check becomes a typed refusal with the
reason and what would fix it; nothing is silently imputed, dropped or clipped
without being counted in the diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from causal_core.contract import (
    AbstentionReason,
    AnalysisPolicy,
    BalanceRow,
    CausalQuery,
    DesignCheck,
    Diagnostics,
    IdentificationCertificate,
    PropensityDiagnostics,
    Strategy,
)
from causal_core.estimators import Prepared
from causal_core.estimators._linalg import add_intercept, logistic, ols, robust_wald_f

SRM_ALPHA = 0.001  # sample-ratio mismatch threshold (Fabijan et al. 2019 use 0.0005)
MIN_TREATMENT_RESIDUAL_SHARE = 0.01  # continuous T: variation left after the covariates


@dataclass
class DesignResult:
    prepared: Prepared | None
    diagnostics: Diagnostics | None
    frame: pd.DataFrame
    abstention: tuple[AbstentionReason, str, tuple[str, ...]] | None = None
    warnings: list[str] = field(default_factory=list)


def _refuse(
    frame: pd.DataFrame,
    reason: AbstentionReason,
    detail: str,
    fix: tuple[str, ...] = (),
    diagnostics: Diagnostics | None = None,
    warnings: list[str] | None = None,
) -> DesignResult:
    return DesignResult(None, diagnostics, frame, (reason, detail, fix), warnings or [])


def _numeric(frame: pd.DataFrame, cols: list[str]) -> list[str]:
    return [
        c
        for c in cols
        if not (pd.api.types.is_numeric_dtype(frame[c]) or pd.api.types.is_bool_dtype(frame[c]))
    ]


def _encode_covariates(frame: pd.DataFrame, cols: list[str], warnings: list[str]) -> pd.DataFrame:
    if not cols:
        return pd.DataFrame(index=frame.index)
    enc = pd.get_dummies(frame[cols], drop_first=True, dtype=float)
    enc = enc.astype(float)
    const = [c for c in enc.columns if enc[c].nunique() <= 1]
    if const:
        warnings.append(f"dropped constant covariate columns: {const}")
        enc = enc.drop(columns=const)
    # Keep a linearly independent subset (collinear dummies or duplicated columns).
    # Rank decisions use the p x p correlation matrix, so the cost does not grow with n.
    Xc = enc.to_numpy() - enc.to_numpy().mean(axis=0)
    sd = np.sqrt(np.mean(Xc**2, axis=0))
    corr = (Xc.T @ Xc / len(Xc)) / np.outer(sd, sd)
    kept_idx: list[int] = []
    for j in range(corr.shape[0]):
        sub = corr[np.ix_([*kept_idx, j], [*kept_idx, j])]
        if np.linalg.eigvalsh(sub).min() > 1e-8:
            kept_idx.append(j)
    kept = [str(enc.columns[j]) for j in kept_idx]
    dropped = [c for c in enc.columns if c not in kept]
    if dropped:
        warnings.append(f"dropped linearly dependent covariate columns: {dropped}")
    return enc[kept]


def smd(x: np.ndarray, t: np.ndarray, w: np.ndarray | None = None) -> float:
    w = np.ones_like(x) if w is None else w
    m1 = np.average(x[t == 1], weights=w[t == 1])
    m0 = np.average(x[t == 0], weights=w[t == 0])
    v1 = np.average((x[t == 1] - m1) ** 2, weights=w[t == 1])
    v0 = np.average((x[t == 0] - m0) ** 2, weights=w[t == 0])
    pooled = np.sqrt((v1 + v0) / 2)
    return float((m1 - m0) / pooled) if pooled > 0 else 0.0


def prepare(
    data: pd.DataFrame,
    query: CausalQuery,
    cert: IdentificationCertificate,
    policy: AnalysisPolicy,
) -> DesignResult:
    warnings: list[str] = []
    x, y = query.treatment, query.outcome
    adj = list(cert.adjustment_set)
    groups = {
        "instruments": list(cert.instruments),
        "mediators": list(cert.mediators),
        "treatment_proxies": list(cert.treatment_proxies),
        "outcome_proxies": list(cert.outcome_proxies),
        "negative_control_outcomes": list(query.negative_control_outcomes),
    }
    needed = list(dict.fromkeys([x, y, *adj, *(c for g in groups.values() for c in g)]))
    absent = [c for c in needed if c not in data.columns]
    if absent:
        return _refuse(
            data.iloc[:0],
            AbstentionReason.INVALID_INPUT,
            f"the analysis needs columns that are not in the data: {absent}",
            (f"add columns {absent}, or mark them latent in the graph",),
        )

    frame = data[needed]
    n_rows = len(frame)
    na_rows = int(frame.isna().any(axis=1).sum())
    if na_rows:
        na_cols = [c for c in needed if frame[c].isna().any()]
        if policy.missing == "error":
            return _refuse(
                frame,
                AbstentionReason.DESIGN_CHECK_FAILED,
                f"{na_rows} of {n_rows} rows have missing values in {na_cols}",
                (
                    "impute upstream with a documented method, or set policy.missing='drop' to "
                    "use complete cases (valid only if values are missing completely at random)",
                ),
            )
        frame = frame.dropna()
        warnings.append(
            f"dropped {na_rows} rows with missing values (assumes missing completely at random)"
        )

    non_numeric = _numeric(frame, [c for c in needed if c not in adj])
    if non_numeric:
        return _refuse(
            frame,
            AbstentionReason.INVALID_INPUT,
            f"columns must be numeric or boolean: {non_numeric}",
            ("encode them numerically; only adjustment covariates may be categorical",),
        )

    def refuse(reason: AbstentionReason, detail: str, fix: tuple[str, ...] = ()) -> DesignResult:
        return _refuse(frame, reason, detail, fix, warnings=warnings)

    n = len(frame)
    checks: list[DesignCheck] = []
    tv = frame[x].astype(float).to_numpy()
    yv = frame[y].astype(float).to_numpy()
    uniq = np.unique(tv)
    if len(uniq) < 2:
        return refuse(AbstentionReason.DESIGN_CHECK_FAILED, f"treatment {x!r} does not vary")
    if np.ptp(yv) == 0:
        return refuse(AbstentionReason.DESIGN_CHECK_FAILED, f"outcome {y!r} does not vary")

    if len(uniq) == 2:
        kind = "binary"
        levels = query.treatment_levels or (float(uniq[0]), float(uniq[1]))
        if set(levels) != {float(u) for u in uniq}:
            return refuse(
                AbstentionReason.INVALID_INPUT,
                f"treatment_levels {levels} do not match the observed values {uniq.tolist()}",
            )
        if query.treatment_levels is None and set(levels) != {0.0, 1.0}:
            warnings.append(f"binary treatment coded {levels[0]:g} (control) / {levels[1]:g}")
        t = (tv == levels[1]).astype(float)
        scale = 1.0
    else:
        kind = "continuous"
        if query.treatment_levels is not None:
            levels = query.treatment_levels
            scale = levels[1] - levels[0]
        else:
            levels = (0.0, 1.0)
            scale = 1.0
        t = tv

    n_treated = int(t.sum()) if kind == "binary" else None
    n_control = n - n_treated if n_treated is not None else None
    ok_n = n >= policy.min_n
    checks.append(DesignCheck(name="sample size", passed=ok_n, detail=f"n={n}"))
    if not ok_n:
        return refuse(AbstentionReason.LOW_N, f"n={n} is below policy.min_n={policy.min_n}")
    if kind == "binary":
        small = min(n_treated or 0, n_control or 0)
        ok_arm = small >= policy.min_per_arm
        checks.append(
            DesignCheck(
                name="units per arm",
                passed=ok_arm,
                detail=f"treated={n_treated}, control={n_control}",
            )
        )
        if not ok_arm:
            return refuse(
                AbstentionReason.LOW_N,
                f"smallest arm has {small} units; policy.min_per_arm={policy.min_per_arm}",
            )

    Xdf = _encode_covariates(frame, adj, warnings)
    X = Xdf.to_numpy(dtype=float)

    def arr(cols: list[str]) -> np.ndarray | None:
        return frame[cols].to_numpy(dtype=float) if cols else None

    prepared = Prepared(
        y=yv,
        t=t,
        X=X,
        covariate_names=tuple(Xdf.columns),
        treatment_kind=kind,  # type: ignore[arg-type]
        levels=(float(levels[0]), float(levels[1])),
        scale=float(scale),
        instruments=arr(groups["instruments"]),
        mediators=arr(groups["mediators"]),
        treatment_proxies=arr(groups["treatment_proxies"]),
        outcome_proxies=arr(groups["outcome_proxies"]),
    )

    propensity: PropensityDiagnostics | None = None
    balance: list[BalanceRow] = []
    srm_p: float | None = None
    first_stage_f: float | None = None

    def diagnostics() -> Diagnostics:
        return Diagnostics(
            n_rows=n_rows,
            n_used=n,
            n_dropped_missing=n_rows - n,
            treatment_kind=kind,  # type: ignore[arg-type]
            n_treated=n_treated,
            n_control=n_control,
            propensity=propensity,
            balance=tuple(balance),
            srm_p_value=srm_p,
            first_stage_f=first_stage_f,
            checks=tuple(checks),
        )

    def refuse_with_diag(
        reason: AbstentionReason, detail: str, fix: tuple[str, ...] = ()
    ) -> DesignResult:
        return _refuse(frame, reason, detail, fix, diagnostics(), warnings)

    adjusting = cert.strategy in (Strategy.backdoor, Strategy.randomized) and X.shape[1] > 0

    if kind == "binary" and adjusting:
        lg = logistic(t, add_intercept(X))
        lo, hi = policy.propensity_bounds
        e = lg.p
        outside = float(np.mean((e < lo) | (e > hi)))
        ec = np.clip(e, lo, hi)
        w = np.where(t == 1, 1 / ec, 1 / (1 - ec))
        ess = {
            arm: float(w[t == arm].sum() ** 2 / np.sum(w[t == arm] ** 2)) for arm in (0.0, 1.0)
        }
        propensity = PropensityDiagnostics(
            min=float(e.min()),
            max=float(e.max()),
            share_outside_bounds=outside,
            ess_treated=ess[1.0],
            ess_control=ess[0.0],
        )
        for j, name in enumerate(Xdf.columns):
            balance.append(
                BalanceRow(
                    covariate=str(name),
                    smd_raw=smd(X[:, j], t),
                    smd_weighted=smd(X[:, j], t, w),
                )
            )
        ok = not lg.separated and outside <= policy.max_extreme_propensity_share
        checks.append(
            DesignCheck(
                name="overlap (positivity)",
                passed=ok,
                detail=f"{outside:.1%} of units have propensity outside [{lo}, {hi}]"
                + ("; covariates separate the arms perfectly" if lg.separated else ""),
            )
        )
        if not ok:
            return refuse_with_diag(
                AbstentionReason.POSITIVITY,
                f"{outside:.1%} of units have an estimated propensity outside [{lo}, {hi}] "
                f"(policy allows {policy.max_extreme_propensity_share:.1%})"
                + ("; the covariates predict treatment perfectly" if lg.separated else ""),
                (
                    "restrict the population to the region where both arms occur",
                    "or drop covariates that (nearly) determine treatment, if the graph allows",
                ),
            )
        imbalanced = [b.covariate for b in balance if abs(b.smd_weighted or 0) > 0.1]
        if imbalanced:
            warnings.append(f"weighted |SMD| > 0.1 for {imbalanced}")

    if kind == "continuous" and adjusting:
        fit = ols(t, add_intercept(X))
        resid_share = float(np.var(fit.resid) / np.var(t))
        ok = resid_share >= MIN_TREATMENT_RESIDUAL_SHARE
        checks.append(
            DesignCheck(
                name="treatment variation left after covariates",
                passed=ok,
                detail=f"{resid_share:.2%} of treatment variance",
            )
        )
        if not ok:
            return refuse_with_diag(
                AbstentionReason.POSITIVITY,
                f"covariates explain {1 - resid_share:.2%} of the treatment's variance; "
                "there is no independent variation to learn the effect from",
            )

    if kind == "binary" and query.expected_treated_share is not None:
        expected = np.array([query.expected_treated_share, 1 - query.expected_treated_share]) * n
        observed = np.array([n_treated, n_control], dtype=float)
        srm_p = float(stats.chisquare(observed, expected).pvalue)
        ok = srm_p >= SRM_ALPHA
        checks.append(
            DesignCheck(
                name="sample ratio",
                passed=ok,
                detail=f"treated share {n_treated / n:.4f} vs expected "
                f"{query.expected_treated_share}; p={srm_p:.2g}",
            )
        )
        if not ok:
            return refuse_with_diag(
                AbstentionReason.DESIGN_CHECK_FAILED,
                f"sample ratio mismatch: treated share {n_treated / n:.4f}, expected "
                f"{query.expected_treated_share} (chi-square p={srm_p:.2g} < {SRM_ALPHA})",
                ("find the assignment or logging fault before reading any effect",),
            )

    if cert.strategy is Strategy.iv and prepared.instruments is not None:
        exog = add_intercept(X)
        k = exog.shape[1]
        first_stage_f = robust_wald_f(
            t,
            np.column_stack([exog, prepared.instruments]),
            list(range(k, k + prepared.instruments.shape[1])),
        )
        ok = first_stage_f >= policy.min_first_stage_f
        checks.append(
            DesignCheck(
                name="instrument strength",
                passed=ok,
                detail=f"robust first-stage F = {first_stage_f:.1f}",
            )
        )
        if not ok:
            return refuse_with_diag(
                AbstentionReason.WEAK_INSTRUMENT,
                f"first-stage F = {first_stage_f:.1f} < {policy.min_first_stage_f}; a weak "
                "instrument biases 2SLS toward OLS and its intervals under-cover",
            )

    return DesignResult(prepared, diagnostics(), frame, None, warnings)

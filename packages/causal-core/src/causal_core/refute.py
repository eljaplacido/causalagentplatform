"""
Falsification tests run after estimation.

A refuter can only ever *fail* an analysis; passing is not evidence that the
assumptions hold. Each test has a declared level (``policy.refutation_alpha``);
the T0 gate measures their false-alarm rate on valid designs and their power
against planted violations, so their verdicts carry a known error rate.

* ``placebo_treatment``          -- permute the treatment; a real estimator must
                                    then find nothing. Catches leakage and bugs.
* ``negative_control_outcomes``  -- estimate the effect on outcomes the treatment
                                    cannot affect; a non-zero "effect" signals
                                    confounding the graph missed.
* ``implied_independences``      -- test conditional independences the graph
                                    implies among measured variables; a rejection
                                    means the graph contradicts the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

import networkx as nx
import numpy as np
import pandas as pd
from scipy import stats

from causal_core.contract import (
    AnalysisPolicy,
    CausalQuery,
    IdentificationCertificate,
    RefutationResult,
    Strategy,
)
from causal_core.estimators import Estimator, Prepared
from causal_core.estimators._linalg import add_intercept, ols
from causal_core.graph import Graph


@dataclass
class RefutationContext:
    prepared: Prepared
    primary: Estimator
    policy: AnalysisPolicy
    query: CausalQuery
    cert: IdentificationCertificate
    graph: Graph
    frame: pd.DataFrame  # analysis columns after missing-value handling
    data: pd.DataFrame  # the full input, for implied-independence tests
    seed: int
    notes: list[str] = field(default_factory=list)  # informational, never change status


def _p_value(point: float, se: float | None) -> float | None:
    if se is None or se <= 0 or not np.isfinite(se):
        return None
    return float(2 * stats.norm.sf(abs(point / se)))


def placebo_treatment(ctx: RefutationContext) -> RefutationResult | None:
    if ctx.cert.strategy not in (Strategy.backdoor, Strategy.randomized):
        return None
    rng = np.random.default_rng([ctx.seed, 1])
    d = replace(ctx.prepared, t=rng.permutation(ctx.prepared.t))
    fit = ctx.primary.fit(d, ctx.policy, np.random.default_rng([ctx.seed, 2]))
    p = _p_value(fit.record.point, fit.record.se)
    alpha = ctx.policy.refutation_alpha
    return RefutationResult(
        name="placebo_treatment",
        passed=p is None or p >= alpha,
        statistic=fit.record.point,
        p_value=p,
        threshold=alpha,
        detail="effect of a randomly permuted treatment; should be indistinguishable from 0",
    )


def negative_control_outcomes(ctx: RefutationContext) -> list[RefutationResult]:
    ncos = list(ctx.query.negative_control_outcomes)
    if not ncos or ctx.cert.strategy not in (Strategy.backdoor, Strategy.randomized):
        return []
    alpha = ctx.policy.refutation_alpha / len(ncos)
    out = []
    for k, nco in enumerate(ncos):
        yv = ctx.frame[nco].to_numpy(dtype=float)
        d = replace(ctx.prepared, y=yv)
        fit = ctx.primary.fit(d, ctx.policy, np.random.default_rng([ctx.seed, 10 + k]))
        p = _p_value(fit.record.point, fit.record.se)
        out.append(
            RefutationResult(
                name=f"negative_control_outcome:{nco}",
                passed=p is None or p >= alpha,
                statistic=fit.record.point,
                p_value=p,
                threshold=alpha,
                detail=f"estimated 'effect' of {ctx.query.treatment} on {nco}, which the "
                "analyst declared it cannot affect (Bonferroni over negative controls)",
            )
        )
    return out


def implied_independences(
    g: Graph, data: pd.DataFrame, focus: set[str], limit: int
) -> list[tuple[str, str, tuple[str, ...]]]:
    """(a, b, S) with a ⊥ b | S implied by the graph, all measured and numeric."""
    measured = {
        c
        for c in g.observed
        if c in data.columns
        and (pd.api.types.is_numeric_dtype(data[c]) or pd.api.types.is_bool_dtype(data[c]))
    }
    directed, bidirected = g.admg
    adjacent = {frozenset(e) for e in directed.edges} | set(bidirected)
    pairs = [
        (a, b) for a, b in combinations(sorted(measured), 2) if frozenset((a, b)) not in adjacent
    ]
    pairs.sort(key=lambda ab: (-len({ab[0], ab[1]} & focus), ab))
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for a, b in pairs:
        if len(out) >= limit:
            break
        sep = nx.find_minimal_d_separator(g.dag, {a}, {b}, restricted=measured - {a, b})
        if sep is not None:
            out.append((a, b, tuple(sorted(sep))))
    return out


def partial_correlation_test(
    data: pd.DataFrame, a: str, b: str, cond: tuple[str, ...]
) -> tuple[float, float]:
    """Fisher-z test of the partial correlation of a and b given cond (linear)."""
    frame = data[[a, b, *cond]].dropna()
    n = len(frame)
    Z = add_intercept(frame[list(cond)].to_numpy(dtype=float)) if cond else np.ones((n, 1))
    ra = ols(frame[a].to_numpy(dtype=float), Z).resid
    rb = ols(frame[b].to_numpy(dtype=float), Z).resid
    denom = np.sqrt(np.sum(ra**2) * np.sum(rb**2))
    r = float(np.sum(ra * rb) / denom) if denom > 0 else 0.0
    r = float(np.clip(r, -0.999999, 0.999999))
    dof = n - len(cond) - 3
    if dof <= 0:
        return r, 1.0
    z = np.arctanh(r) * np.sqrt(dof)
    return r, float(2 * stats.norm.sf(abs(z)))


def holm(pvals: list[float], alpha: float) -> list[bool]:
    """Holm step-down: True where the hypothesis is rejected."""
    order = np.argsort(pvals)
    m = len(pvals)
    reject = [False] * m
    for rank, i in enumerate(order):
        if pvals[i] <= alpha / (m - rank):
            reject[i] = True
        else:
            break
    return reject


def graph_implications(ctx: RefutationContext) -> RefutationResult | None:
    focus = {ctx.query.treatment, ctx.query.outcome, *ctx.cert.adjustment_set}
    tests = implied_independences(ctx.graph, ctx.data, focus, ctx.policy.implied_independence_tests)
    if not tests:
        return None
    results = [(a, b, s, *partial_correlation_test(ctx.data, a, b, s)) for a, b, s in tests]

    def fmt(r: tuple[str, str, tuple[str, ...], float, float]) -> str:
        given = f" | {', '.join(r[2])}" if r[2] else ""
        return f"{r[0]} ⊥ {r[1]}{given} (r={r[3]:.3f}, p={r[4]:.2g})"

    # An implication between two variables that are both adjusted for cannot change
    # this estimate: whatever links them is conditioned away. Its violation still
    # means the graph is wrong, so it is reported -- but as a note, not a failure.
    adjusted = set(ctx.cert.adjustment_set)
    background = [r for r in results if {r[0], r[1]} <= adjusted]
    results = [r for r in results if not {r[0], r[1]} <= adjusted]
    if background:
        rej = holm([r[4] for r in background], ctx.policy.refutation_alpha)
        bad_bg = [r for r, x in zip(background, rej, strict=True) if x]
        if bad_bg:
            ctx.notes.append(
                f"the graph omits dependencies among adjustment covariates "
                f"({'; '.join(fmt(r) for r in bad_bg[:3])}"
                f"{'; ...' if len(bad_bg) > 3 else ''}). This does not affect this estimate, "
                "because all of them are adjusted for, but the graph should be corrected."
            )
    if not results:
        return None
    pvals = [r[4] for r in results]
    rejected = holm(pvals, ctx.policy.refutation_alpha)
    bad = [r for r, rej in zip(results, rejected, strict=True) if rej]
    worst = sorted(results, key=lambda r: r[4])[:3]
    detail = (
        f"{len(bad)} of {len(results)} graph-implied independences rejected (Holm); "
        + (
            "rejected: " + "; ".join(fmt(r) for r in bad[:5])
            if bad
            else "closest: " + "; ".join(fmt(r) for r in worst)
        )
        + ". Linear partial-correlation tests: nonlinear dependence can go undetected."
    )
    return RefutationResult(
        name="implied_independences",
        passed=not bad,
        statistic=float(len(bad)),
        p_value=float(min(pvals)),
        threshold=ctx.policy.refutation_alpha,
        detail=detail,
    )


def run(ctx: RefutationContext) -> list[RefutationResult]:
    out: list[RefutationResult] = []
    wanted = set(ctx.policy.refuters)
    if "placebo_treatment" in wanted and (r := placebo_treatment(ctx)) is not None:
        out.append(r)
    if "negative_control_outcomes" in wanted:
        out.extend(negative_control_outcomes(ctx))
    if "implied_independences" in wanted and (r := graph_implications(ctx)) is not None:
        out.append(r)
    return out

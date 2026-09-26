"""
T5: the difference-in-differences gate. Bars come from benchmarks/t5-did/PRE-REGISTRATION.md.

Simulated staggered-adoption panels with exact truths -- five since v1, five more
since v4 (covariate-driven selection into timing, each nuisance model misspecified in
turn, and not-yet-treated comparisons). What is measured: bias and interval coverage
of the overall ATT, coverage of the post-adoption event-time effects, the pre-trends
test's false-alarm rate where trends are parallel and its power where they are not,
sensitivity coverage, and that designs the estimator cannot answer are refused with
the reason registered for each.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import zlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import causal_core as cc
from causal_core.bench.t0 import git_sha
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalResult

GRAPH = "rollout -> latency"
NEVER = -1


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t5-did" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t5-did/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


# ── designs ───────────────────────────────────────────────────────────────────
#
# Periods are 1..T; a cohort is the first treated period (NEVER: never treated).

STAGGERED = {4: 80, 5: 80, 6: 80, NEVER: 160}


def _dynamic(g: int, t: int) -> float:
    return {4: 1.0, 5: 2.0, 6: 3.0}[g] * (1 + 0.5 * (t - g))


#: Selection-score coefficients (a, b_x, c_w, q) of a cohort: a + b·x + c·w + q·(x² − 1).
Score = tuple[float, float, float, float]
#: Covariate trend coefficients (x, w, x² − 1): h(X) per period.
Trend = tuple[float, float, float]


@dataclass(frozen=True)
class Design:
    id: str
    cohorts: Mapping[int, int]
    effect: Any  # (g, t) -> tau
    trend: float = 0.0  # cohort-specific linear trend for treated cohorts (violates PT)
    #: v4: cohort drawn from a multinomial logit on the covariates (the baseline cohort --
    #: never-treated, or the last -- scores 0); None: drawn with the tabled proportions.
    scores: Mapping[int, Score] | None = None
    covariate_trend: Trend = (0.0, 0.0, 0.0)


def _dynamic5(g: int, t: int) -> float:
    return {3: 1.0, 4: 1.5, 5: 2.0, 6: 2.5, 8: 3.0}[g] * (1 + 0.5 * (t - g))


ROLLOUT5 = {3: 80, 4: 80, 5: 80, 6: 80, 8: 80}  # every unit eventually treated


def _slopes(a: float, c: float = 0.0, q: float = 0.0) -> dict[int, Score]:
    return {g: (a, b, c, q) for g, b in ((4, 0.5), (5, 0.75), (6, 1.0))} | {NEVER: (0, 0, 0, 0)}


DESIGNS = {
    "staggered_dynamic": Design("staggered_dynamic", STAGGERED, _dynamic),
    "staggered_constant": Design("staggered_constant", STAGGERED, lambda g, t: 2.0),
    "single_cohort": Design("single_cohort", {5: 200, NEVER: 200}, lambda g, t: 1.5),
    "null_effect": Design("null_effect", STAGGERED, lambda g, t: 0.0),
    "trends_violated": Design("trends_violated", STAGGERED, lambda g, t: 2.0, trend=0.3),
    # v4
    "covariate_selection": Design("covariate_selection", STAGGERED, _dynamic,
                                  scores=_slopes(-0.943, c=0.5), covariate_trend=(0.3, 0.3, 0.0)),
    "outcome_misspecified": Design("outcome_misspecified", STAGGERED, lambda g, t: 2.0,
                                   scores=_slopes(-0.693), covariate_trend=(0.0, 0.0, 0.25)),
    "propensity_misspecified": Design("propensity_misspecified", STAGGERED, lambda g, t: 2.0,
                                      scores=_slopes(-0.693, q=0.5),
                                      covariate_trend=(0.3, 0.0, 0.0)),
    "not_yet_treated": Design("not_yet_treated", ROLLOUT5, _dynamic5),
    "covariates_not_yet_treated": Design(
        "covariates_not_yet_treated", ROLLOUT5, _dynamic5,
        scores={3: (0, 0.9, 0, 0), 4: (0, 0.6, 0, 0), 5: (0, 0.3, 0, 0), 6: (0, 0.15, 0, 0),
                8: (0, 0, 0, 0)},
        covariate_trend=(0.3, 0.0, 0.0)),
}


def _probabilities(scores: Mapping[int, Score], x: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Cohort probabilities per unit (columns in the order of `scores`)."""
    s = np.column_stack([a + b * x + c * w + q * (x**2 - 1) for a, b, c, q in scores.values()])
    s -= s.max(axis=1, keepdims=True)
    p = np.exp(s)
    out: np.ndarray = p / p.sum(axis=1, keepdims=True)
    return out


def cohort_probabilities(design: Design, nodes: int = 80) -> dict[int, float]:
    """Population cohort probabilities: tabled, or integrated over the covariates (v4).

    Gauss-Hermite quadrature over x ~ N(0, 1), exact over w ~ Bernoulli(0.5).
    """
    if design.scores is None:
        total = sum(design.cohorts.values())
        return {g: n / total for g, n in design.cohorts.items()}
    z, wt = np.polynomial.hermite_e.hermegauss(nodes)
    wt = wt / wt.sum()
    probs = sum(
        0.5 * (wt[:, None] * _probabilities(design.scores, z, np.full_like(z, w))).sum(axis=0)
        for w in (0.0, 1.0)
    )
    return {g: float(p) for g, p in zip(design.scores, probs, strict=True)}


def estimable(design: Design, g: int, t: int) -> bool:
    """Whether cell (g, t) has comparison units: never-treated, or a cohort first treated later."""
    return NEVER in design.cohorts or any(c > t and c != g for c in design.cohorts)


def truth(design: Design, periods: int, nodes: int = 80) -> tuple[float, dict[int, float]]:
    """Overall ATT and post-adoption event-time truths, by the estimator's own aggregation:
    cohort-probability-weighted means over the post-adoption cells the design can estimate."""
    pi = cohort_probabilities(design, nodes)
    num = den = 0.0
    by_e: dict[int, list[tuple[float, float]]] = {}
    for g, p_g in pi.items():
        if g == NEVER:
            continue
        for t in range(g, periods + 1):
            if not estimable(design, g, t):
                continue
            tau = design.effect(g, t)
            num += p_g * tau
            den += p_g
            by_e.setdefault(t - g, []).append((p_g, tau))
    events = {e: sum(n * tau for n, tau in v) / sum(n for n, _ in v) for e, v in by_e.items()}
    return num / den, events


def simulate(
    design: Design, periods: int, rho: float, rng: np.random.Generator, *, drawn: bool = False
) -> pd.DataFrame:
    """One panel. ``drawn``: each unit's cohort is sampled with the tabled proportions (v2);
    otherwise the tabled sizes are exact (v1, and every refusal panel). A design with
    selection ``scores`` (v4) draws covariates x, w first and each cohort from them."""
    if design.scores is not None:
        return _simulate_covariates(design, periods, rho, rng)
    labels = list(design.cohorts)
    sizes = np.array([design.cohorts[g] for g in labels], dtype=float)
    if drawn:
        n_total = int(sizes.sum())
        cohort = np.asarray(labels)[rng.choice(len(labels), size=n_total, p=sizes / sizes.sum())]
    else:
        cohort = np.concatenate([np.full(int(k), g) for g, k in zip(labels, sizes, strict=True)])
    level = np.where(cohort == NEVER, 0.0, 0.3 * cohort)  # selection on levels is allowed
    return _outcomes(design, cohort, rng.normal(level, 1.0), periods, rho, rng)


def _simulate_covariates(
    design: Design, periods: int, rho: float, rng: np.random.Generator
) -> pd.DataFrame:
    assert design.scores is not None
    n = int(sum(design.cohorts.values()))
    x = rng.normal(size=n)
    w = (rng.random(n) < 0.5).astype(float)
    p = _probabilities(design.scores, x, w)
    idx = (rng.random(n)[:, None] > p.cumsum(axis=1)).sum(axis=1)
    cohort = np.asarray(list(design.scores))[np.minimum(idx, p.shape[1] - 1)]
    level = np.where(cohort == NEVER, 0.0, 0.3 * cohort) + 0.5 * x
    cx, cw, cq = design.covariate_trend
    frame = _outcomes(design, cohort, rng.normal(level, 1.0), periods, rho, rng,
                      h=cx * x + cw * w + cq * (x**2 - 1))
    return frame.assign(x=np.repeat(x, periods), w=np.repeat(w, periods))


def _outcomes(
    design: Design, cohort: np.ndarray, alpha: np.ndarray, periods: int, rho: float,
    rng: np.random.Generator, h: np.ndarray | None = None,
) -> pd.DataFrame:
    n = cohort.shape[0]
    lam = rng.normal(size=periods)
    eps = np.empty((n, periods))
    eps[:, 0] = rng.normal(size=n)
    for t in range(1, periods):
        eps[:, t] = rho * eps[:, t - 1] + np.sqrt(1 - rho**2) * rng.normal(size=n)
    t_idx = np.arange(1, periods + 1)
    treated = (cohort[:, None] != NEVER) & (t_idx[None, :] >= cohort[:, None])
    tau = np.zeros((n, periods))
    for i in range(n):
        g = int(cohort[i])
        if g != NEVER:
            tau[i] = [design.effect(g, t) if t >= g else 0.0 for t in t_idx]
    drift: np.ndarray = (
        np.where(cohort == NEVER, 0.0, design.trend)[:, None] * (t_idx[None, :] - 1)
    )
    if h is not None:
        drift = drift + h[:, None] * (t_idx[None, :] - 1)
    y = alpha[:, None] + lam[None, :] + tau + drift + eps
    return pd.DataFrame({
        "unit": np.repeat(np.arange(n), periods),
        "period": np.tile(t_idx, n),
        "rollout": treated.astype(float).ravel(),
        "latency": y.ravel(),
    })


def analyse(frame: pd.DataFrame, policy: AnalysisPolicy | None = None,
            **options: Any) -> Any:
    """The kernel's analysis of one panel; ``options``: covariates, control_group (v4)."""
    return cc.analyze(frame, GRAPH, treatment="rollout", outcome="latency",
                      design="difference_in_differences", unit="unit", time="period",
                      estimand="att", policy=policy or AnalysisPolicy(), **options)


def options(cfg: Mapping[str, Any], case: str) -> dict[str, Any]:
    """How the pre-registration says a design or refusal panel is analysed (v4's [extensions])."""
    ext = cfg.get("extensions", {})
    out: dict[str, Any] = {}
    if case in ext.get("covariates", {}):
        out["covariates"] = tuple(ext["covariates"][case])
    if case in ext.get("control_group", {}):
        out["control_group"] = ext["control_group"][case]
    return out


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


# ── checks ────────────────────────────────────────────────────────────────────


@dataclass
class DesignReport:
    id: str
    truth: float
    reps: int
    results: int
    mean: float
    mcse: float
    bias: float
    bias_in_mcse: float
    coverage: float
    event_coverage: float
    pretrend_rejection_rate: float
    sensitivity_m: float | None = None
    sensitivity_coverage: float | None = None
    median_breakdown: float | None = None
    first_failure: str = ""
    extra: dict[str, float] = field(default_factory=dict)


def run_design(design: Design, cfg: Mapping[str, Any], reps: int | None = None) -> DesignReport:
    p = cfg["panels"]
    n_reps = reps or p["reps"]
    rng = _rng(p["seed"], design.id)
    true_att, true_events = truth(design, p["periods"],
                                  cfg.get("extensions", {}).get("quadrature_nodes", 80))
    opts = options(cfg, design.id)
    drawn = p.get("cohort_membership", "fixed") == "drawn"
    sens = cfg.get("sensitivity")
    m_at = None
    if sens is not None:
        m_true = sens["m_true"][design.id]
        m_at = min((m for m in sens["grid"] if m >= m_true - 1e-12), default=None)
    sens_hit = sens_n = 0
    breakdowns: list[float] = []
    points: list[float] = []
    covered = ev_hit = ev_total = rejected = 0
    first_failure = ""
    for _ in range(n_reps):
        out = analyse(simulate(design, p["periods"], p["ar1_rho"], rng, drawn=drawn), **opts)
        if not isinstance(out, CausalResult):
            first_failure = first_failure or f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            continue
        ci = out.primary.ci
        points.append(out.primary.point)
        if ci is not None and ci.lo <= true_att <= ci.hi:
            covered += 1
        for ev in out.event_study:
            if ev.event_time >= 0 and ev.estimate.ci is not None:
                ev_total += 1
                # An event time the design cannot estimate has no truth to cover.
                true_e = true_events.get(ev.event_time)
                ev_hit += true_e is not None and ev.estimate.ci.lo <= true_e <= ev.estimate.ci.hi
        if any(r.name == "pre_trends" and not r.passed for r in out.refutations):
            rejected += 1
        pt = out.sensitivity.parallel_trends if out.sensitivity is not None else None
        if m_at is not None and pt is not None:
            row = next(r for r in pt.rows if abs(r.m - m_at) < 1e-12)
            sens_n += 1
            sens_hit += row.lo <= true_att <= row.hi
            breakdowns.append(pt.breakdown if pt.breakdown is not None else 0.0)
    k = len(points)
    mean = float(np.mean(points)) if k else float("nan")
    mcse = float(np.std(points, ddof=1) / np.sqrt(k)) if k > 1 else float("nan")
    return DesignReport(
        id=design.id,
        truth=true_att,
        reps=n_reps,
        results=k,
        mean=mean,
        mcse=mcse,
        bias=mean - true_att,
        bias_in_mcse=abs(mean - true_att) / mcse if mcse and mcse > 0 else float("inf"),
        coverage=covered / k if k else 0.0,
        event_coverage=ev_hit / ev_total if ev_total else 0.0,
        pretrend_rejection_rate=rejected / k if k else 0.0,
        sensitivity_m=m_at,
        sensitivity_coverage=sens_hit / sens_n if sens_n else None,
        median_breakdown=float(np.median(breakdowns)) if breakdowns else None,
        first_failure=first_failure,
    )


def check_estimation(cfg: Mapping[str, Any], reps: int | None = None,
                     only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    est, pt = cfg["estimation"], cfg["pretrends"]
    names = list(only or (*est["designs"], "trends_violated"))
    reports = [run_design(DESIGNS[name], cfg, reps) for name in names]
    failures: list[str] = []
    lo, hi = est["coverage_band"]
    elo, ehi = est["event_coverage_band"]
    for r in reports:
        if r.results < r.reps:
            failures.append(f"{r.id}: {r.reps - r.results} replications did not produce a "
                            f"result ({r.first_failure})")
        if r.id in est["designs"]:
            if r.bias_in_mcse > est["bias_mcse_max"]:
                failures.append(f"{r.id}: bias {r.bias:+.4f} is {r.bias_in_mcse:.1f} MCSE")
            if not lo <= r.coverage <= hi:
                failures.append(f"{r.id}: coverage {r.coverage:.3f} outside [{lo}, {hi}]")
            if not elo <= r.event_coverage <= ehi:
                failures.append(f"{r.id}: event-time coverage {r.event_coverage:.3f} outside "
                                f"[{elo}, {ehi}]")
            if r.pretrend_rejection_rate > pt["false_alarm_max"]:
                failures.append(f"{r.id}: pre-trends false alarms {r.pretrend_rejection_rate:.3f}"
                                f" > {pt['false_alarm_max']}")
        sens = cfg.get("sensitivity")
        if sens is not None and r.id in sens["m_true"] and (
            r.sensitivity_coverage is None or r.sensitivity_coverage < sens["coverage_min"]
        ):
            failures.append(f"{r.id}: sensitivity coverage {r.sensitivity_coverage} at M="
                            f"{r.sensitivity_m} < {sens['coverage_min']}")
        if r.id == "trends_violated" and r.pretrend_rejection_rate < pt["power_min"]:
            failures.append(f"trends_violated: pre-trends power {r.pretrend_rejection_rate:.3f}"
                            f" < {pt['power_min']}")
    return reports, failures


# ── refusals ──────────────────────────────────────────────────────────────────

EXPECTED_REFUSAL = {
    "unbalanced": AbstentionReason.DESIGN_CHECK_FAILED,
    "treatment_turns_off": AbstentionReason.DESIGN_CHECK_FAILED,
    "no_never_treated": AbstentionReason.DESIGN_CHECK_FAILED,
    "treated_from_the_start": AbstentionReason.DESIGN_CHECK_FAILED,
    "tiny_cohort": AbstentionReason.LOW_N,
    "poor_overlap": AbstentionReason.POSITIVITY,
    "time_varying_covariate": AbstentionReason.INVALID_INPUT,
    "no_comparison_units": AbstentionReason.DESIGN_CHECK_FAILED,
}


def refusal_panel(case: str, cfg: Mapping[str, Any]) -> pd.DataFrame:
    p = cfg["panels"]
    rng = _rng(p["seed"], "refusal:" + case)
    base = DESIGNS["staggered_constant"]
    if case == "unbalanced":
        frame = simulate(base, p["periods"], p["ar1_rho"], rng)
        return frame.drop(index=rng.choice(len(frame), size=len(frame) // 20, replace=False))
    if case == "treatment_turns_off":
        frame = simulate(base, p["periods"], p["ar1_rho"], rng)
        off = frame["unit"].isin(range(20)) & (frame["period"] >= 6)
        frame.loc[off, "rollout"] = 0.0  # units 0-19 are cohort 4: on in 4-5, then off
        return frame
    if case == "no_never_treated":
        return simulate(Design("x", {4: 200, 6: 200}, lambda g, t: 2.0), p["periods"],
                        p["ar1_rho"], rng)
    if case == "treated_from_the_start":
        return simulate(Design("x", {1: 200, NEVER: 200}, lambda g, t: 2.0), p["periods"],
                        p["ar1_rho"], rng)
    if case == "tiny_cohort":
        return simulate(Design("x", {4: 3, 5: 200, NEVER: 197}, lambda g, t: 2.0),
                        p["periods"], p["ar1_rho"], rng)
    if case == "poor_overlap":
        return simulate(Design("x", STAGGERED, lambda g, t: 2.0,
                               scores=dict.fromkeys((4, 5, 6), (-0.693, 4.0, 0.0, 0.0))
                               | {NEVER: (0, 0, 0, 0)},
                               covariate_trend=(0.3, 0.0, 0.0)),
                        p["periods"], p["ar1_rho"], rng)
    if case == "time_varying_covariate":
        frame = simulate(DESIGNS["covariate_selection"], p["periods"], p["ar1_rho"], rng)
        return frame.assign(x=frame["x"] + rng.normal(0.0, 0.5, size=len(frame)))
    if case == "no_comparison_units":
        return simulate(Design("x", {5: 400}, lambda g, t: 2.0), p["periods"], p["ar1_rho"], rng)
    raise KeyError(case)


def check_refusals(cfg: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    got: dict[str, str] = {}
    failures: list[str] = []
    for case in cfg["refusals"]["must_refuse"]:
        out = analyse(refusal_panel(case, cfg), **options(cfg, case))
        reason = getattr(out, "reason", None)
        got[case] = out.kind if reason is None else f"{out.kind}:{reason.value}"
        if reason is not EXPECTED_REFUSAL[case]:
            failures.append(f"refusal {case}: expected {EXPECTED_REFUSAL[case].value}, got "
                            f"{got[case]}")
    return got, failures


# ── seeded defects ────────────────────────────────────────────────────────────

#: Which check the pre-registration says must catch each defect.
CATCHER: dict[str, tuple[str, str]] = {
    "twfe_static": ("staggered_dynamic", "bias"),
    "forbidden_comparisons": ("staggered_dynamic", "bias"),
    "cells_independent": ("staggered_dynamic", "coverage"),
    "pretrend_blind": ("trends_violated", "power"),
    "last_pre_period_only": ("trends_violated", "sensitivity"),
    "ignore_covariates": ("covariate_selection", "bias"),
    "not_yet_treated_includes_treated": ("not_yet_treated", "bias"),
    "propensity_ignored": ("outcome_misspecified", "bias"),
    "outcome_regression_ignored": ("propensity_misspecified", "bias"),
}


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    from causal_core.bench.defects_t5 import DEFECTS

    design, bar = CATCHER[name]
    with DEFECTS[name]():
        _, failures = check_estimation(cfg, reps=reps, only=(design,))
    marker = {"bias": "bias", "coverage": ": coverage", "power": "power",
              "sensitivity": "sensitivity coverage"}[bar]
    return any(f.startswith(design) and marker in f for f in failures)


def check_defects(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, bool]:
    from causal_core.bench.defects_t5 import DEFECTS

    return {name: defect_caught(name, cfg, reps) for name in sorted(DEFECTS)}


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    reports, failures = check_estimation(cfg, reps)
    refusals, refusal_failures = check_refusals(cfg)
    caught = check_defects(cfg, reps)
    failures += refusal_failures
    required = set(cfg["seeded_defects"]["required"])
    if required != set(caught):
        failures.append(f"registered defects {sorted(required)} != implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "designs": [asdict(r) for r in reports],
        "refusals": refusals,
        "seeded_defects_caught": caught,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        description="Run the pre-registered T5 difference-in-differences gate"
    )
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {
        "suite": "T5",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        **run(cfg, args.reps),
    }
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

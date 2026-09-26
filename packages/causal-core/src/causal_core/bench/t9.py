"""
T9: the Rosenbaum sensitivity gate. Bars from benchmarks/t9-rosenbaum/PRE-REGISTRATION.md.

Paired designs drawn from Rosenbaum's model at its worst case (the true Γ is the planted one,
analytically) test the bound itself; end-to-end designs with an unmeasured confounder of known
strength test the matched design ``analyze`` builds. Measured: how often the reported sensitivity
value Γ* claims more robustness than the planted bias allows, how often it retains the
conclusion below the truth, and whether it tracks the design sensitivity -- computed here from
the structural model, never from a fit.
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
import zlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

import causal_core as cc
import causal_core.sensitivity as sens_mod
from causal_core.bench.t0 import git_sha
from causal_core.contract import AnalysisPolicy, CausalResult

GRAPH = "X -> T; X -> Y; T -> Y"
POLICY = AnalysisPolicy(refuters=())


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t9-rosenbaum" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t9-rosenbaum/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


# ── designs ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairsDesign:
    id: str
    gamma0: float  # planted hidden bias (sharp); 1 = none
    shift: float  # additive effect with N(shift, 1) differences; used when gamma0 == 1


PAIRS = {
    "pairs_null": PairsDesign("pairs_null", 1.0, 0.0),
    "pairs_bias_2": PairsDesign("pairs_bias_2", 2.0, 0.0),
    "pairs_bias_4": PairsDesign("pairs_bias_4", 4.0, 0.0),
    "pairs_effect": PairsDesign("pairs_effect", 1.0, 0.5),
}


@dataclass(frozen=True)
class E2EDesign:
    id: str
    continuous_x: bool
    a: float
    b: float
    gamma0: float
    c: float
    beta: float
    tau: float


E2E = {
    "e2e_null_measured": E2EDesign("e2e_null_measured", False, -1.5, 1.2, 1.0, 2.0, 0.0, 0.0),
    "e2e_hidden_bias": E2EDesign("e2e_hidden_bias", False, -1.5, 0.8, 3.0, 1.0, 2.0, 0.0),
    "e2e_effect": E2EDesign("e2e_effect", False, -1.5, 0.8, 1.0, 1.0, 0.0, 0.5),
    "e2e_continuous": E2EDesign("e2e_continuous", True, -1.0, 0.8, 3.0, 1.0, 2.0, 0.0),
}


def simulate_pairs(d: PairsDesign, n: int, rng: np.random.Generator) -> np.ndarray:
    if d.gamma0 > 1.0:
        mag = np.abs(rng.normal(size=n))
        pos = rng.random(n) < d.gamma0 / (1.0 + d.gamma0)
        return np.where(pos, mag, -mag)
    return rng.normal(d.shift, 1.0, size=n)


def _sigmoid(v: np.ndarray | float) -> Any:
    return 1.0 / (1.0 + np.exp(-np.asarray(v, dtype=float)))


def simulate_e2e(d: E2EDesign, n: int, rng: np.random.Generator) -> pd.DataFrame:
    x = rng.normal(size=n) if d.continuous_x else rng.binomial(1, 0.5, n).astype(float)
    u = rng.binomial(1, 0.5, n).astype(float)
    t = (rng.random(n) < _sigmoid(d.a + d.b * x + math.log(d.gamma0) * u)).astype(float)
    y = d.c * x + d.beta * u + d.tau * t + rng.normal(size=n)
    return pd.DataFrame({"X": x, "T": t, "Y": y})


# ── truth ────────────────────────────────────────────────────────────────────


def pairs_truth(d: PairsDesign) -> float:
    """Γ₀ for the sharp designs (there Γ̃ = Γ₀); Γ̃ = Φ(δ√2)/(1 − Φ(δ√2)) for a shift δ."""
    if d.gamma0 > 1.0 or d.shift == 0.0:
        return d.gamma0
    p = float(stats.norm.cdf(d.shift * math.sqrt(2.0)))
    return p / (1.0 - p)


def e2e_design_sensitivity(d: E2EDesign) -> float:
    """Γ̃ = p/(1−p), p = P(D₁ + D₂ > 0), for exact matching on a binary X with every treated
    unit matched: an exact finite sum over the mixture of β(U_t − U_c) + τ + N(0, 2)."""
    if d.continuous_x:
        raise ValueError("the oracle needs a binary X")
    comps: list[tuple[float, float]] = []
    lg = math.log(d.gamma0)
    for x in (0.0, 1.0):
        e0, e1 = float(_sigmoid(d.a + d.b * x)), float(_sigmoid(d.a + d.b * x + lg))
        px_treated = 0.5 * (e0 + e1) / 2  # P(X = x, T = 1)
        q1 = e1 / (e1 + e0)  # P(U = 1 | T = 1, X = x)
        q0 = (1 - e1) / ((1 - e1) + (1 - e0))  # P(U = 1 | T = 0, X = x)
        for ut in (0, 1):
            for uc in (0, 1):
                w = px_treated * (q1 if ut else 1 - q1) * (q0 if uc else 1 - q0)
                comps.append((w, d.beta * (ut - uc) + d.tau))
    total = sum(w for w, _ in comps)
    p = sum(
        (w1 / total) * (w2 / total) * float(stats.norm.cdf((m1 + m2) / 2.0))
        for w1, m1 in comps
        for w2, m2 in comps
    )
    return p / (1.0 - p)


# ── measurement ──────────────────────────────────────────────────────────────


@dataclass
class DesignReport:
    id: str
    layer: str
    gamma0: float
    design_sensitivity: float | None
    reps: int
    results: int
    false_rate: float  # Γ* not null
    exceed_truth_rate: float  # Γ* ≥ Γ₀
    retain_rate: float | None  # Γ* ≥ Γ_ref / retain_factor
    exceed_oracle_rate: float | None  # Γ* ≥ factor · Γ̃
    median_gamma_star: float | None
    mean_pairs: float | None = None
    first_failure: str = ""


def _rates(gs: list[float | None], gamma0: float, ref: float | None, retain_factor: float,
           exceed_factor: float, gt: float | None) -> dict[str, Any]:
    k = len(gs)
    vals = [g if g is not None else 0.0 for g in gs]  # null is below every Γ ≥ 1
    arr = np.asarray(vals, dtype=float)
    defined = [g for g in gs if g is not None]
    return {
        "false_rate": float(np.mean([g is not None for g in gs])) if k else 0.0,
        "exceed_truth_rate": float(np.mean(arr >= gamma0)) if k and gamma0 > 1 else 0.0,
        "retain_rate": float(np.mean(arr >= ref / retain_factor)) if k and ref else None,
        "exceed_oracle_rate": float(np.mean(arr >= exceed_factor * gt)) if k and gt else None,
        "median_gamma_star": float(np.median(defined)) if defined else None,
    }


def run_pairs_design(d: PairsDesign, cfg: Mapping[str, Any], reps: int | None = None
                     ) -> DesignReport:
    run = cfg["run"]
    n_reps = reps or run["pairs_reps"]
    rng = _rng(run["seed"], d.id)
    alpha = sens_mod.one_sided_alpha(POLICY.ci_level)
    gs: list[float | None] = []
    for _ in range(n_reps):
        diffs = simulate_pairs(d, run["n_pairs"], rng)
        direction = 1.0 if diffs.mean() >= 0 else -1.0
        g, _ = sens_mod.rosenbaum_pairs(diffs, alpha, direction)
        gs.append(g)
    truth = pairs_truth(d)
    p = cfg["pairs"]
    gt = truth if d.id in p["effect_designs"] else None
    ref = truth if truth > 1 else None
    return DesignReport(
        id=d.id, layer="pairs", gamma0=d.gamma0, design_sensitivity=truth, reps=n_reps,
        results=len(gs),
        **_rates(gs, d.gamma0, ref, p["retain_factor"], p["effect_exceed_factor"], gt),
    )


def analyse(frame: pd.DataFrame, graph: str = GRAPH, **kw: Any) -> Any:
    return cc.analyze(frame, graph, treatment="T", outcome="Y", policy=POLICY, **kw)


def run_e2e_design(d: E2EDesign, cfg: Mapping[str, Any], reps: int | None = None
                   ) -> DesignReport:
    run = cfg["run"]
    n_reps = reps or run["e2e_reps"]
    rng = _rng(run["seed"], d.id)
    gs: list[float | None] = []
    pairs: list[int] = []
    first_failure = ""
    for _ in range(n_reps):
        out = analyse(simulate_e2e(d, run["n_rows"], rng))
        rb = out.sensitivity.rosenbaum if isinstance(out, CausalResult) and out.sensitivity \
            else None
        if rb is None:
            detail = (out.sensitivity.rosenbaum_null_reason
                      if isinstance(out, CausalResult) and out.sensitivity
                      else f"{out.kind}: {getattr(out, 'detail', '')}")
            first_failure = first_failure or str(detail)[:200]
            continue
        gs.append(rb.gamma_star)
        pairs.append(rb.n_pairs)
    e = cfg["e2e"]
    gt = None if d.continuous_x else e2e_design_sensitivity(d)
    ref = gt if gt is not None and gt > 1.0 + 1e-9 else None
    return DesignReport(
        id=d.id, layer="e2e", gamma0=d.gamma0, design_sensitivity=gt, reps=n_reps,
        results=len(gs), mean_pairs=float(np.mean(pairs)) if pairs else None,
        first_failure=first_failure,
        **_rates(gs, d.gamma0, ref, e["retain_factor"], e["oracle_exceed_factor"], ref),
    )


def _judge(r: DesignReport, sec: Mapping[str, Any], layer: str) -> list[str]:
    f: list[str] = []
    if r.results < r.reps:
        f.append(f"{r.id}: {r.reps - r.results} replications reported no Rosenbaum bound "
                 f"({r.first_failure})")
    if r.id in sec["null_designs"] and r.false_rate > sec["false_rate_max"]:
        f.append(f"{r.id}: false rate {r.false_rate:.4f} > {sec['false_rate_max']}")
    if r.id in sec["bias_designs"] and r.exceed_truth_rate > sec["exceed_truth_rate_max"]:
        f.append(f"{r.id}: exceedance of truth {r.exceed_truth_rate:.4f} > "
                 f"{sec['exceed_truth_rate_max']}")
    retained = r.retain_rate is not None and r.retain_rate >= sec["retain_rate_min"]
    if (r.id in sec["bias_designs"] or r.id in sec["effect_designs"]) and not retained:
        f.append(f"{r.id}: retention {r.retain_rate} < {sec['retain_rate_min']}")
    oracle_max = (sec["effect_exceed_rate_max"] if layer == "pairs"
                  else sec["oracle_exceed_rate_max"])
    gated_oracle = (r.id in sec["effect_designs"] or
                    (layer == "e2e" and r.id in sec["bias_designs"]))
    if gated_oracle and (r.exceed_oracle_rate is None or r.exceed_oracle_rate > oracle_max):
        f.append(f"{r.id}: exceedance of design sensitivity {r.exceed_oracle_rate} > {oracle_max}")
    return f


def check_pairs(cfg: Mapping[str, Any], reps: int | None = None,
                only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    p = cfg["pairs"]
    names = list(only or [*p["null_designs"], *p["bias_designs"], *p["effect_designs"]])
    reports = [run_pairs_design(PAIRS[n], cfg, reps) for n in names]
    return reports, [x for r in reports for x in _judge(r, p, "pairs")]


def check_e2e(cfg: Mapping[str, Any], reps: int | None = None,
              only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    e = cfg["e2e"]
    names = list(only or [*e["null_designs"], *e["bias_designs"], *e["effect_designs"],
                          *e["reported"]])
    reports = [run_e2e_design(E2E[n], cfg, reps) for n in names]
    failures = [x for r in reports if r.id not in e["reported"] for x in _judge(r, e, "e2e")]
    return reports, failures


# ── withheld ─────────────────────────────────────────────────────────────────


def withheld(case: str, cfg: Mapping[str, Any]) -> Any:
    rng = _rng(cfg["run"]["seed"], "withheld:" + case)
    n = cfg["run"]["n_rows"]
    x = rng.binomial(1, 0.5, n).astype(float)
    u = rng.binomial(1, 0.5, n).astype(float)
    if case == "continuous_treatment":
        t = x + u + rng.normal(size=n)
        return analyse(pd.DataFrame({"X": x, "T": t, "Y": x + u + 0.5 * t + rng.normal(size=n)}))
    if case == "randomized":
        t = rng.binomial(1, 0.5, n).astype(float)
        frame = pd.DataFrame({"X": x, "T": t, "Y": x + 0.5 * t + rng.normal(size=n)})
        return analyse(frame, "X -> Y; T -> Y", design="randomized")
    if case == "instrument":
        z = rng.binomial(1, 0.5, n).astype(float)
        t = (rng.random(n) < _sigmoid(-1.0 + 1.5 * z + u)).astype(float)
        frame = pd.DataFrame({"Z": z, "T": t, "Y": u + 0.5 * t + rng.normal(size=n)})
        return analyse(frame, "Z -> T; T -> Y; U -> T; U -> Y")
    raise KeyError(case)


def check_withheld(cfg: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    got: dict[str, str] = {}
    failures: list[str] = []
    for case in cfg["withheld"]["must_withhold"]:
        out = withheld(case, cfg)
        if not isinstance(out, CausalResult) or out.sensitivity is None:
            got[case] = f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            failures.append(f"withheld {case}: expected a result with sensitivity, got {got[case]}")
            continue
        s = out.sensitivity
        got[case] = f"withheld: {s.rosenbaum_null_reason}" if s.rosenbaum is None else \
            f"REPORTED gamma_star={s.rosenbaum.gamma_star}"
        if s.rosenbaum is not None or not s.rosenbaum_null_reason:
            failures.append(f"withheld {case}: {got[case]}")
    return got, failures


# ── seeded defects and the run ───────────────────────────────────────────────

CATCHER: dict[str, tuple[str, str]] = {
    "random_pairs": ("e2e_null_measured", ": false rate"),
    "lower_bound": ("pairs_bias_2", ": exceedance of truth"),
    "alpha_not_halved": ("pairs_bias_2", ": exceedance of truth"),
}


def defect_report(name: str, cfg: Mapping[str, Any],
                  reps: int | None = None) -> tuple[bool, DesignReport]:
    from causal_core.bench.defects_t9 import DEFECTS

    design, marker = CATCHER[name]
    check = check_pairs if design in PAIRS else check_e2e
    with DEFECTS[name]():
        reports, failures = check(cfg, reps=reps, only=(design,))
    return any(f.startswith(design) and marker in f for f in failures), reports[0]


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    return defect_report(name, cfg, reps)[0]


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t9 import DEFECTS

    pair_reports, failures = check_pairs(cfg, reps)
    e2e_reports, e2e_failures = check_e2e(cfg, reps)
    failures += e2e_failures
    held, held_failures = check_withheld(cfg)
    failures += held_failures
    under = {name: defect_report(name, cfg, reps) for name in sorted(DEFECTS)}
    caught = {name: ok for name, (ok, _) in under.items()}
    if set(cfg["seeded_defects"]["required"]) != set(caught):
        failures.append(f"registered defects {sorted(cfg['seeded_defects']['required'])} != "
                        f"implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "designs": [asdict(r) for r in [*pair_reports, *e2e_reports]],
        "withheld": held,
        "seeded_defects_caught": caught,
        "seeded_defect_designs": {n: asdict(rep) for n, (_, rep) in under.items()},
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Run the pre-registered T9 Rosenbaum gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {"suite": "T9", "prereg_sha256": prereg_hash, "prereg_version": cfg["meta"]["version"],
           "git": git_sha(), **run(cfg, args.reps)}
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

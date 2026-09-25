"""
T7: the cluster-randomized designs gate. Bars from benchmarks/t7-cluster/PRE-REGISTRATION.md.

Clusters of units, treatment assigned to whole clusters, outcomes correlated within
them. Measured: bias and interval coverage of the unit-weighted ATE, and that designs
the method cannot answer are refused with the registered reason.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import zlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import causal_core as cc
from causal_core.bench.t0 import git_sha
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalResult

GRAPH = "canary -> latency"


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t7-cluster" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t7-cluster/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Design:
    id: str
    k: int
    icc: float
    effect: Callable[[np.ndarray], np.ndarray]  # cluster sizes -> tau_j
    heavy_sizes: bool = False  # 10 + floor(LogNormal(ln 40, sigma)) instead of 10 + Poisson(40)


DESIGNS = {
    "many_clusters": Design("many_clusters", 80, 0.1, lambda m: np.full(m.shape, 0.3)),
    "moderate": Design("moderate", 40, 0.3, lambda m: np.full(m.shape, 0.3)),
    "few_clusters": Design("few_clusters", 20, 0.3, lambda m: np.full(m.shape, 0.3)),
    "null_effect": Design("null_effect", 40, 0.3, lambda m: np.zeros(m.shape)),
    "size_dependent": Design("size_dependent", 40, 0.1, lambda m: 0.3 + 0.01 * (m - 50),
                             heavy_sizes=True),
}


#: The log-normal sigma for `size_dependent`'s cluster sizes, by registered version.
SIZE_SIGMA = {2: 0.8, 3: 0.5}


def simulate(d: Design, rng: np.random.Generator, heavy: bool | None = None,
             sigma: float = 0.5) -> tuple[pd.DataFrame, float]:
    use_heavy = d.heavy_sizes if heavy is None else heavy
    sizes = (10 + np.floor(rng.lognormal(np.log(40), sigma, d.k)).astype(int) if use_heavy
             else 10 + rng.poisson(40, d.k))
    treated = np.zeros(d.k)
    treated[rng.permutation(d.k)[: d.k // 2]] = 1.0
    tau = d.effect(sizes.astype(float))
    u = rng.normal(0, np.sqrt(d.icc), d.k)
    cl = np.repeat(np.arange(d.k), sizes)
    t = treated[cl]
    y = 10 + tau[cl] * t + u[cl] + rng.normal(0, np.sqrt(1 - d.icc), cl.shape[0])
    truth = float((sizes * tau).sum() / sizes.sum())
    return pd.DataFrame({"service": cl, "canary": t, "latency": y}), truth


def policy(cfg: Mapping[str, Any]) -> AnalysisPolicy:
    c = cfg["clusters"]
    extra = {"max_cluster_size_cv": c["max_cluster_size_cv"]} if "max_cluster_size_cv" in c else {}
    return AnalysisPolicy(min_clusters_per_arm=c["min_clusters_per_arm"], **extra)


def analyse(frame: pd.DataFrame, cfg: Mapping[str, Any], design: str = "randomized") -> Any:
    return cc.analyze(frame, GRAPH, treatment="canary", outcome="latency", design=design,
                      cluster="service", policy=policy(cfg))


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


@dataclass
class DesignReport:
    id: str
    reps: int
    results: int
    mean_error: float
    mcse: float
    bias_in_mcse: float
    coverage: float
    mean_icc: float
    size_guard_refusals: int = 0
    first_failure: str = ""


def run_design(d: Design, cfg: Mapping[str, Any], reps: int | None = None) -> DesignReport:
    c = cfg["clusters"]
    n_reps = reps or c["reps"]
    rng = _rng(c["seed"], d.id)
    errors: list[float] = []
    covered = 0
    iccs: list[float] = []
    first_failure = ""
    guard = 0
    version = cfg["meta"]["version"]
    heavy = d.heavy_sizes and version >= 2  # each version reproducible from its own text
    sigma = SIZE_SIGMA.get(version, 0.5)
    for _ in range(n_reps):
        frame, truth = simulate(d, rng, heavy=heavy, sigma=sigma)
        out = analyse(frame, cfg)
        if "cluster sizes vary too much" in getattr(out, "detail", ""):
            guard += 1
            continue
        if not isinstance(out, CausalResult):
            first_failure = first_failure or f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            continue
        errors.append(out.primary.point - truth)
        ci = out.primary.ci
        covered += bool(ci and ci.lo <= truth <= ci.hi)
        if out.diagnostics.icc is not None:
            iccs.append(out.diagnostics.icc)
    k = len(errors)
    mean = float(np.mean(errors)) if k else float("nan")
    mcse = float(np.std(errors, ddof=1) / np.sqrt(k)) if k > 1 else float("nan")
    return DesignReport(
        id=d.id, reps=n_reps, results=k, mean_error=mean, mcse=mcse,
        bias_in_mcse=abs(mean) / mcse if mcse and mcse > 0 else float("inf"),
        coverage=covered / k if k else 0.0,
        mean_icc=float(np.mean(iccs)) if iccs else float("nan"),
        size_guard_refusals=guard,
        first_failure=first_failure,
    )


def check_estimation(cfg: Mapping[str, Any], reps: int | None = None,
                     only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    est = cfg["estimation"]
    reports = [run_design(DESIGNS[n], cfg, reps) for n in (only or est["designs"])]
    failures: list[str] = []
    lo, hi = est["coverage_band"]
    guard_max = est.get("size_guard_refusals_max")
    for r in reports:
        allowed = r.size_guard_refusals if guard_max is not None else 0
        if guard_max is not None and r.size_guard_refusals / r.reps > guard_max:
            failures.append(f"{r.id}: the size guard refused {r.size_guard_refusals} of {r.reps} "
                            f"(> {guard_max})")
        unexplained = r.reps - r.results - allowed
        if unexplained > 0:
            failures.append(f"{r.id}: {unexplained} replications produced no result "
                            f"({r.first_failure or 'refused by the size guard'})")
        if r.bias_in_mcse > est["bias_mcse_max"]:
            failures.append(f"{r.id}: bias {r.mean_error:+.4f} is {r.bias_in_mcse:.1f} MCSE")
        if r.id == "few_clusters":
            if r.coverage < est["few_clusters_coverage_min"]:
                failures.append(f"{r.id}: coverage {r.coverage:.3f} < "
                                f"{est['few_clusters_coverage_min']}")
        elif not lo <= r.coverage <= hi:
            failures.append(f"{r.id}: coverage {r.coverage:.3f} outside [{lo}, {hi}]")
    return reports, failures


EXPECTED_REFUSAL = {
    "varies_within_cluster": AbstentionReason.DESIGN_CHECK_FAILED,
    "too_few_clusters": AbstentionReason.LOW_N,
    "not_randomized": AbstentionReason.INVALID_INPUT,
    "extreme_size_variation": AbstentionReason.DESIGN_CHECK_FAILED,
}


def refusal(case: str, cfg: Mapping[str, Any]) -> Any:
    rng = _rng(cfg["clusters"]["seed"], "refusal:" + case)
    if case == "too_few_clusters":
        frame, _ = simulate(Design("x", 6, 0.3, lambda m: np.full(m.shape, 0.3)), rng)
        return analyse(frame, cfg)
    frame, _ = simulate(DESIGNS["moderate"], rng)
    if case == "varies_within_cluster":
        treated = frame.index[frame["canary"] == 1]
        frame.loc[treated[: len(treated) // 4], "canary"] = 0.0
        return analyse(frame, cfg)
    if case == "not_randomized":
        return analyse(frame, cfg, design="observational")
    if case == "extreme_size_variation":
        heavy, _ = simulate(Design("x", 40, 0.1, lambda m: np.full(m.shape, 0.3),
                                   heavy_sizes=True), rng, sigma=1.0)
        return analyse(heavy, cfg)
    raise KeyError(case)


def check_refusals(cfg: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    got: dict[str, str] = {}
    failures: list[str] = []
    for case in cfg["refusals"]["must_refuse"]:
        out = refusal(case, cfg)
        reason = getattr(out, "reason", None)
        got[case] = out.kind if reason is None else f"{out.kind}:{reason.value}"
        if reason is not EXPECTED_REFUSAL[case]:
            failures.append(f"refusal {case}: expected {EXPECTED_REFUSAL[case].value}, got "
                            f"{got[case]}")
    return got, failures


CATCHER: dict[str, tuple[str, str]] = {
    "unit_level_se": ("moderate", ": coverage"),
    "cluster_weighted_estimate": ("size_dependent", ": bias"),
}


def defect_report(name: str, cfg: Mapping[str, Any],
                  reps: int | None = None) -> tuple[bool, DesignReport]:
    from causal_core.bench.defects_t7 import DEFECTS

    design, marker = CATCHER[name]
    with DEFECTS[name]():
        reports, failures = check_estimation(cfg, reps=reps, only=(design,))
    return any(f.startswith(design) and marker in f for f in failures), reports[0]


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    return defect_report(name, cfg, reps)[0]


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t7 import DEFECTS

    reports, failures = check_estimation(cfg, reps)
    refusals, refusal_failures = check_refusals(cfg)
    under = {name: defect_report(name, cfg, reps) for name in sorted(DEFECTS)}
    caught = {name: ok for name, (ok, _) in under.items()}
    failures += refusal_failures
    if set(cfg["seeded_defects"]["required"]) != set(caught):
        failures.append(f"registered defects {sorted(cfg['seeded_defects']['required'])} != "
                        f"implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "designs": [asdict(r) for r in reports],
        "refusals": refusals,
        "seeded_defects_caught": caught,
        "seeded_defect_designs": {n: asdict(rep) for n, (_, rep) in under.items()},
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Run the pre-registered T7 cluster-randomized gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {"suite": "T7", "prereg_sha256": prereg_hash, "prereg_version": cfg["meta"]["version"],
           "git": git_sha(), **run(cfg, args.reps)}
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

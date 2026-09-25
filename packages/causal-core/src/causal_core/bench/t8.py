"""
T8: the switchback designs gate. Bars from benchmarks/t8-switchback/PRE-REGISTRATION.md.

One system, randomized hourly between arms, measured every minute: carryover after each
switch, AR(1) noise, a daily cycle. Measured: bias and coverage of the steady-state effect
with a declared washout, the carryover check's rejection rate (reported), and refusals.
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
from scipy.signal import lfilter

import causal_core as cc
from causal_core.bench.t0 import git_sha
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalResult

GRAPH = "policy -> latency"


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t8-switchback" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t8-switchback/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Design:
    id: str
    tau: float
    lam: float  # carryover time constant, minutes (0: instantaneous)
    rho: float  # minute-level AR(1)


DESIGNS = {
    "instant": Design("instant", 0.3, 0.0, 0.8),
    "carryover": Design("carryover", 0.3, 5.0, 0.8),
    "null_effect": Design("null_effect", 0.0, 5.0, 0.8),
    "strong_autocorrelation": Design("strong_autocorrelation", 0.3, 5.0, 0.97),
    "long_carryover": Design("long_carryover", 0.3, 30.0, 0.8),
}


def simulate(d: Design, cfg: Mapping[str, Any], rng: np.random.Generator,
             n_blocks: int | None = None) -> pd.DataFrame:
    st = cfg["streams"]
    b_len = st["block_minutes"]
    k = n_blocks or st["days"] * 1440 // b_len
    while True:
        arm = (rng.random(k) < 0.5).astype(float)
        if 0 < arm.sum() < k:
            break
    minutes = k * b_len
    t = np.arange(minutes)
    target = np.repeat(arm, b_len)
    if d.lam > 0:
        a = math.exp(-1.0 / d.lam)
        r, _ = lfilter([1 - a], [1, -a], target, zi=[a * target[0]])
    else:
        r = target
    z = rng.normal(size=minutes)
    eps, _ = lfilter([math.sqrt(1 - d.rho**2)], [1, -d.rho], z, zi=[d.rho * rng.normal()])
    y = 10 + np.sin(2 * np.pi * t / 1440) + d.tau * r + eps
    return pd.DataFrame({"block": t // b_len, "minute": t, "policy": target, "latency": y})


def analyse(frame: pd.DataFrame, cfg: Mapping[str, Any], washout: float | None = None,
            design: str = "randomized") -> Any:
    w = cfg["streams"]["washout"] if washout is None else washout
    return cc.analyze(frame, GRAPH, treatment="policy", outcome="latency", design=design,
                      cluster="block", time="minute", washout=w, policy=AnalysisPolicy())


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


@dataclass
class DesignReport:
    id: str
    tau: float
    reps: int
    results: int
    mean_error: float
    mcse: float
    bias_in_mcse: float
    coverage: float
    carryover_flag_rate: float
    first_failure: str = ""


def run_design(d: Design, cfg: Mapping[str, Any], reps: int | None = None) -> DesignReport:
    st = cfg["streams"]
    n_reps = reps or st["reps"]
    rng = _rng(st["seed"], d.id)
    errors: list[float] = []
    covered = flagged = 0
    first_failure = ""
    for _ in range(n_reps):
        out = analyse(simulate(d, cfg, rng), cfg)
        if not isinstance(out, CausalResult):
            first_failure = first_failure or f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            continue
        errors.append(out.primary.point - d.tau)
        ci = out.primary.ci
        covered += bool(ci and ci.lo <= d.tau <= ci.hi)
        flagged += any(c.name == "no carryover beyond the washout" and not c.passed
                       for c in out.diagnostics.checks)
    k = len(errors)
    mean = float(np.mean(errors)) if k else float("nan")
    mcse = float(np.std(errors, ddof=1) / np.sqrt(k)) if k > 1 else float("nan")
    return DesignReport(
        id=d.id, tau=d.tau, reps=n_reps, results=k, mean_error=mean, mcse=mcse,
        bias_in_mcse=abs(mean) / mcse if mcse and mcse > 0 else float("inf"),
        coverage=covered / k if k else 0.0,
        carryover_flag_rate=flagged / k if k else 0.0,
        first_failure=first_failure,
    )


def check_estimation(cfg: Mapping[str, Any], reps: int | None = None,
                     only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    est = cfg["estimation"]
    names = list(only or [*est["designs"], "long_carryover"])
    reports = [run_design(DESIGNS[n], cfg, reps) for n in names]
    failures: list[str] = []
    lo, hi = est["coverage_band"]
    for r in reports:
        if r.id not in est["designs"]:
            continue  # reported, not gated
        if r.results < r.reps:
            failures.append(f"{r.id}: {r.reps - r.results} replications produced no result "
                            f"({r.first_failure})")
        if r.bias_in_mcse > est["bias_mcse_max"]:
            failures.append(f"{r.id}: bias {r.mean_error:+.4f} is {r.bias_in_mcse:.1f} MCSE")
        if not lo <= r.coverage <= hi:
            failures.append(f"{r.id}: coverage {r.coverage:.3f} outside [{lo}, {hi}]")
    return reports, failures


EXPECTED_REFUSAL = {
    "varies_within_block": AbstentionReason.DESIGN_CHECK_FAILED,
    "washout_consumes_blocks": AbstentionReason.INVALID_INPUT,
    "too_few_blocks": AbstentionReason.LOW_N,
    "not_randomized": AbstentionReason.INVALID_INPUT,
}


def refusal(case: str, cfg: Mapping[str, Any]) -> Any:
    rng = _rng(cfg["streams"]["seed"], "refusal:" + case)
    if case == "too_few_blocks":
        return analyse(simulate(DESIGNS["carryover"], cfg, rng, n_blocks=8), cfg)
    frame = simulate(DESIGNS["carryover"], cfg, rng)
    if case == "varies_within_block":
        blk = int(frame.loc[frame["policy"] == 1, "block"].iloc[0])
        rows = frame.index[frame["block"] == blk][:15]
        frame.loc[rows, "policy"] = 0.0
        return analyse(frame, cfg)
    if case == "washout_consumes_blocks":
        return analyse(frame, cfg, washout=cfg["streams"]["block_minutes"])
    if case == "not_randomized":
        return analyse(frame, cfg, design="observational")
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
    "no_washout": ("carryover", ": bias"),
    "minute_level_se": ("instant", ": coverage"),
}


def defect_report(name: str, cfg: Mapping[str, Any],
                  reps: int | None = None) -> tuple[bool, DesignReport]:
    from causal_core.bench.defects_t8 import DEFECTS

    design, marker = CATCHER[name]
    with DEFECTS[name]():
        reports, failures = check_estimation(cfg, reps=reps, only=(design,))
    return any(f.startswith(design) and marker in f for f in failures), reports[0]


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    return defect_report(name, cfg, reps)[0]


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t8 import DEFECTS

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

    ap = argparse.ArgumentParser(description="Run the pre-registered T8 switchback gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {"suite": "T8", "prereg_sha256": prereg_hash, "prereg_version": cfg["meta"]["version"],
           "git": git_sha(), **run(cfg, args.reps)}
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

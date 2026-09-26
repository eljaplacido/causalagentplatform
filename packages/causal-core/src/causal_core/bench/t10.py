"""
T10: the positivity and overlap gate. Bars from benchmarks/t10-positivity/PRE-REGISTRATION.md.

Observational designs whose positivity structure is set by construction: clean overlap,
limited overlap, a violation below the tolerance, structural violations in a stratum, in a
three-way stratum every single covariate hides, and beyond a continuous threshold, and the
ATT's one-way requirement in both directions. Measured: that violations are refused for
POSITIVITY, that answerable designs are answered (and cover), that small violations and
limited overlap are warned about, and that nothing is trimmed silently.

    python -m causal_core.bench.t10 --out benchmarks/t10-positivity/results/<date>-v1-run1.json
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import zlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import expit

import causal_core as cc
from causal_core.bench.t0 import git_sha
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalResult

REGION_CHECK = "overlap regions (positivity)"


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t10-positivity" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t10-positivity/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Design:
    id: str
    high_dim: bool
    estimand: str
    violation: str | None  # see simulate()


DESIGNS = {
    d.id: d
    for d in (
        Design("clean", False, "ate", None),
        Design("clean_highdim", True, "ate", None),
        Design("limited_overlap", True, "ate", "plateau"),
        Design("small_violation", False, "ate", "x0>1.8"),
        Design("structural_stratum", False, "ate", "b0&b1"),
        Design("joint_violation_highdim", True, "ate", "b0&b1&b2"),
        Design("continuous_violation", False, "ate", "x0>1.15"),
        Design("att_no_controls", True, "att", "b0&b1&b2 always"),
        Design("att_never_treated", True, "att", "b0&b1&b2"),
    )
}


def columns(d: Design) -> list[str]:
    pc, pb = (6, 14) if d.high_dim else (2, 2)
    return [f"x{i}" for i in range(pc)] + [f"b{i}" for i in range(pb)]


def graph(d: Design) -> str:
    return "; ".join([f"{c} -> t; {c} -> y" for c in columns(d)] + ["t -> y"])


def simulate(d: Design, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """One draw of design ``d``: covariates, a treatment with the registered positivity
    structure, and y = 1 + tau*t + mu(X) + eps with tau = 1 + 0.5*b_h (ATE = 1.25)."""
    pc, pb = (6, 14) if d.high_dim else (2, 2)
    Xc = rng.normal(size=(n, pc))
    Xb = (rng.random((n, pb)) < 0.5).astype(float)
    lin = 0.6 * Xc[:, 0] - 0.4 * Xc[:, 1] + 0.4 * Xb[:, 0] - 0.4 * Xb[:, 1]
    if d.high_dim:
        lin = lin + 0.1 * Xc[:, 2:].sum(1) + 0.15 * (Xb[:, 2:6].sum(1) - 2)
    e = expit(lin)
    three = (Xb[:, 0] == 1) & (Xb[:, 1] == 1) & ((Xb[:, 2] == 1) if pb > 2 else True)
    if d.violation == "plateau":
        e = np.clip(expit(2.0 * Xc[:, 0] - 0.4 * Xc[:, 1] + 0.4 * Xb[:, 0] - 0.4 * Xb[:, 1]),
                    0.06, 0.94)
    elif d.violation == "x0>1.8":
        e[Xc[:, 0] > 1.8] = 0.0
    elif d.violation == "x0>1.15":
        e[Xc[:, 0] > 1.15] = 0.0
    elif d.violation == "b0&b1":
        e[(Xb[:, 0] == 1) & (Xb[:, 1] == 1)] = 0.0
    elif d.violation == "b0&b1&b2":
        e[three] = 0.0
    elif d.violation == "b0&b1&b2 always":
        e[three] = 1.0
    t = (rng.random(n) < e).astype(float)
    tau = 1.0 + 0.5 * Xb[:, -1]
    mu = Xc[:, 0] + 0.5 * Xc[:, 1] + 0.5 * Xb[:, 0] + 0.5 * Xb[:, 1]
    if d.high_dim:
        mu = mu + 0.25 * Xc[:, 2:].sum(1) + 0.25 * Xb[:, 2:].sum(1)
    y = 1.0 + tau * t + mu + rng.normal(size=n)
    frame = pd.DataFrame(np.column_stack([Xc, Xb]), columns=columns(d))
    return frame.assign(t=t, y=y)


def policy() -> AnalysisPolicy:
    """The registered policy: the defaults, without refuters or sensitivity."""
    return AnalysisPolicy(refuters=(), sensitivity=False)


def analyse(d: Design, frame: pd.DataFrame) -> Any:
    return cc.analyze(frame, graph(d), treatment="t", outcome="y", estimand=d.estimand,
                      policy=policy())


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


@dataclass
class DesignReport:
    id: str
    estimand: str
    reps: int
    results: int
    refusals: dict[str, int] = field(default_factory=dict)
    positivity_rate: float = 0.0
    refusal_rate: float = 0.0
    warned_rate: float = 0.0  # among all replications: a result with a violation share > 0
    limited_rate: float = 0.0  # among results: the limited-overlap flag
    coverage: float = 0.0
    mean_error: float = 0.0
    mcse: float = 0.0
    bias_in_mcse: float = 0.0
    trimmed_results: int = 0  # results whose n_used or estimand differs from the question
    unnamed_region_refusals: int = 0
    apparatus_failures: int = 0
    mean_violation_share: float = 0.0
    first_refusal: str = ""


def run_design(d: Design, cfg: Mapping[str, Any], reps: int | None = None) -> DesignReport:
    ds = cfg["designs"]
    n, truth = ds["n"], ds["truth"]
    n_reps = reps or ds["reps"]
    rng = _rng(ds["seed"], d.id)
    errors: list[float] = []
    reasons: Counter[str] = Counter()
    covered = warned = limited = trimmed = unnamed = failures = 0
    shares: list[float] = []
    first = ""
    for _ in range(n_reps):
        out = analyse(d, simulate(d, n, rng))
        diag = getattr(out, "diagnostics", None)
        ov = diag.overlap if diag is not None else None
        if ov is not None:
            shares.append(ov.violation_share)
        if isinstance(out, CausalResult):
            errors.append(out.primary.point - truth)
            ci = out.primary.ci
            covered += bool(ci and ci.lo <= truth <= ci.hi)
            warned += bool(ov and ov.violation_share > 0)
            limited += bool(ov and ov.limited_overlap)
            trimmed += (out.diagnostics.n_used != n) or (out.query.estimand != d.estimand)
            continue
        if out.kind == "apparatus_failure":
            failures += 1
            first = first or f"apparatus_failure: {out.detail}"[:200]
            continue
        reasons[out.reason.value] += 1
        first = first or out.detail[:300]
        region_failed = diag is not None and any(
            c.name == REGION_CHECK and not c.passed for c in diag.checks
        )
        if region_failed and not (ov and ov.regions and all(r.rule for r in ov.regions)):
            unnamed += 1
    k = len(errors)
    mean = float(np.mean(errors)) if k else 0.0
    mcse = float(np.std(errors, ddof=1) / np.sqrt(k)) if k > 1 else 0.0
    return DesignReport(
        id=d.id,
        estimand=d.estimand,
        reps=n_reps,
        results=k,
        refusals=dict(reasons),
        positivity_rate=reasons[AbstentionReason.POSITIVITY.value] / n_reps,
        refusal_rate=sum(reasons.values()) / n_reps,
        warned_rate=warned / n_reps,
        limited_rate=limited / k if k else 0.0,
        coverage=covered / k if k else 0.0,
        mean_error=mean,
        mcse=mcse,
        bias_in_mcse=abs(mean) / mcse if mcse > 0 else (0.0 if mean == 0 else float("inf")),
        trimmed_results=trimmed,
        unnamed_region_refusals=unnamed,
        apparatus_failures=failures,
        mean_violation_share=float(np.mean(shares)) if shares else 0.0,
        first_refusal=first,
    )


def check_method(cfg: Mapping[str, Any]) -> list[str]:
    """The registered thresholds are the defaults."""
    from causal_core.overlap import min_region_units

    m, pol = cfg["method"], AnalysisPolicy()
    failures = []
    for name in ("overlap_region_bound", "overlap_region_alpha", "overlap_region_depth"):
        if getattr(pol, name) != m[name]:
            failures.append(f"method: {name} = {getattr(pol, name)} is not the registered "
                            f"{m[name]}")
    if tuple(pol.limited_overlap_bounds) != tuple(m["limited_overlap_bounds"]):
        failures.append(f"method: limited_overlap_bounds {pol.limited_overlap_bounds} is not the "
                        f"registered {m['limited_overlap_bounds']}")
    if min_region_units(pol) != m["min_region_units"]:
        failures.append(f"method: min_region_units {min_region_units(pol)} is not the registered "
                        f"{m['min_region_units']}")
    return failures


def judge(reports: list[DesignReport], cfg: Mapping[str, Any]) -> list[str]:
    det, ans, lim, est = cfg["detection"], cfg["answer"], cfg["limited_overlap"], cfg["estimation"]
    lo, hi = est["coverage_band"]
    failures: list[str] = []
    for r in reports:
        if r.apparatus_failures:
            failures.append(f"{r.id}: {r.apparatus_failures} apparatus failures "
                            f"({r.first_refusal})")
        if r.trimmed_results:
            failures.append(f"{r.id}: trimming: {r.trimmed_results} results answered a different "
                            "question (n_used or estimand changed)")
        if r.unnamed_region_refusals:
            failures.append(f"{r.id}: regions: {r.unnamed_region_refusals} region refusals name "
                            "no region")
        if r.id in det["designs"] and r.positivity_rate < det["power_min"]:
            failures.append(f"{r.id}: detection {r.positivity_rate:.3f} < {det['power_min']}")
        if r.id in ans["designs"] and r.refusal_rate > ans["false_refusal_max"]:
            failures.append(f"{r.id}: false refusals {r.refusal_rate:.3f} > "
                            f"{ans['false_refusal_max']} ({r.first_refusal[:120]})")
        if r.id in ans["warning_designs"] and r.warned_rate < ans["warning_min"]:
            failures.append(f"{r.id}: warnings {r.warned_rate:.3f} < {ans['warning_min']}")
        if r.id in lim["flag_designs"] and r.limited_rate < lim["flag_min"]:
            failures.append(f"{r.id}: limited-overlap flag {r.limited_rate:.3f} < "
                            f"{lim['flag_min']}")
        if r.id in lim["quiet_designs"] and r.limited_rate > lim["false_flag_max"]:
            failures.append(f"{r.id}: limited-overlap false flags {r.limited_rate:.3f} > "
                            f"{lim['false_flag_max']}")
        if r.id in est["designs"]:
            if r.results == 0:
                failures.append(f"{r.id}: coverage: no results")
                continue
            if not lo <= r.coverage <= hi:
                failures.append(f"{r.id}: coverage {r.coverage:.3f} outside [{lo}, {hi}]")
            if r.bias_in_mcse > est["bias_mcse_max"]:
                failures.append(f"{r.id}: bias {r.mean_error:+.4f} is {r.bias_in_mcse:.1f} MCSE")
    return failures


def all_design_ids(cfg: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys([*cfg["answer"]["designs"], *cfg["detection"]["designs"],
                               *cfg["estimation"]["reported"]]))


def check_designs(cfg: Mapping[str, Any], reps: int | None = None,
                  only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    reports = [run_design(DESIGNS[n], cfg, reps) for n in (only or all_design_ids(cfg))]
    return reports, judge(reports, cfg)


CATCHER: dict[str, tuple[str, str]] = {
    "no_region_check": ("joint_violation_highdim", ": detection"),
    "marginal_only": ("joint_violation_highdim", ": detection"),
    "in_sample_regions": ("limited_overlap", ": false refusals"),
    "share_threshold_ignored": ("small_violation", ": false refusals"),
    "att_checks_both_arms": ("att_never_treated", ": false refusals"),
    "silent_trimming": ("structural_stratum", ": detection"),
}


def defect_report(name: str, cfg: Mapping[str, Any],
                  reps: int | None = None) -> tuple[bool, DesignReport]:
    from causal_core.bench.defects_t10 import DEFECTS

    design, marker = CATCHER[name]
    with DEFECTS[name]():
        reports, failures = check_designs(cfg, reps or cfg["designs"]["defect_reps"],
                                          only=(design,))
    return any(f.startswith(design) and marker in f for f in failures), reports[0]


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    return defect_report(name, cfg, reps)[0]


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t10 import DEFECTS

    failures = check_method(cfg)
    reports, design_failures = check_designs(cfg, reps)
    failures += design_failures
    under = {name: defect_report(name, cfg, reps) for name in sorted(DEFECTS)}
    caught = {name: ok for name, (ok, _) in under.items()}
    if set(cfg["seeded_defects"]["required"]) != set(caught):
        failures.append(f"registered defects {sorted(cfg['seeded_defects']['required'])} != "
                        f"implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "designs": [asdict(r) for r in reports],
        "seeded_defects_caught": caught,
        "seeded_defect_designs": {n: asdict(rep) for n, (_, rep) in under.items()},
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Run the pre-registered T10 positivity gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {"suite": "T10", "prereg_sha256": prereg_hash, "prereg_version": cfg["meta"]["version"],
           "git": git_sha(), **run(cfg, args.reps)}
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

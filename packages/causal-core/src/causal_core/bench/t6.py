"""
T6: the anytime-valid monitoring gate. Bars come from benchmarks/t6-sequential/PRE-REGISTRATION.md.

Six streamed designs with known effects. What is measured: the share of runs whose
confidence sequence contains the effect at EVERY look (the property a watched canary
needs), power to see a benefit and a harm, and that five streams the method cannot
answer are refused with the reason registered for each.
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
        cand = parent / "benchmarks" / "t6-sequential" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t6-sequential/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


def _normal(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.normal(size=n)


def _skewed(n: int, rng: np.random.Generator) -> np.ndarray:
    s = 0.75
    return np.asarray(rng.lognormal(0.0, s, size=n) - float(np.exp(s * s / 2)), dtype=float)


@dataclass(frozen=True)
class Stream:
    id: str
    p: float
    tau: float
    noise: Callable[[int, np.random.Generator], np.ndarray] = _normal
    drift: float = 0.0


STREAMS = {
    "null": Stream("null", 0.5, 0.0),
    "benefit": Stream("benefit", 0.5, 0.2),
    "harm": Stream("harm", 0.5, -0.25),
    "skewed_null": Stream("skewed_null", 0.5, 0.0, _skewed),
    "drifting_null": Stream("drifting_null", 0.5, 0.0, drift=2.0),
    "unequal_allocation": Stream("unequal_allocation", 0.2, 0.2),
}


def simulate(s: Stream, n: int, rng: np.random.Generator) -> pd.DataFrame:
    t = (rng.random(n) < s.p).astype(float)
    y = 10 + s.drift * np.arange(n) / n + s.tau * t + s.noise(n, rng)
    return pd.DataFrame({"arrival": np.arange(n), "canary": t, "latency": y})


def policy(cfg: Mapping[str, Any]) -> AnalysisPolicy:
    st = cfg["streams"]
    return AnalysisPolicy(cs_target_n=st["target_n"], cs_look_every=st["look_every"],
                          cs_burn_in=st["burn_in"])


def analyse(frame: pd.DataFrame, p: float | None, cfg: Mapping[str, Any],
            design: str = "randomized") -> Any:
    return cc.analyze(frame, GRAPH, treatment="canary", outcome="latency", design=design,
                      sequence="arrival", expected_treated_share=p, policy=policy(cfg))


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


@dataclass
class StreamReport:
    id: str
    tau: float
    reps: int
    results: int
    uniform_coverage: float
    above_rate: float
    below_rate: float
    median_first_above: float | None
    median_first_below: float | None
    first_failure: str = ""


def run_stream(s: Stream, cfg: Mapping[str, Any], reps: int | None = None) -> StreamReport:
    st = cfg["streams"]
    n_reps = reps or st["reps"]
    rng = _rng(st["seed"], s.id)
    covered = 0
    results = 0
    above: list[int] = []
    below: list[int] = []
    first_failure = ""
    for _ in range(n_reps):
        out = analyse(simulate(s, st["units"], rng), s.p, cfg)
        if not isinstance(out, CausalResult) or out.sequential is None:
            first_failure = first_failure or f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            continue
        results += 1
        seq = out.sequential
        covered += all(look.lo <= s.tau <= look.hi for look in seq.looks)
        if seq.first_above_zero is not None:
            above.append(seq.first_above_zero)
        if seq.first_below_zero is not None:
            below.append(seq.first_below_zero)
    k = results or 1
    return StreamReport(
        id=s.id,
        tau=s.tau,
        reps=n_reps,
        results=results,
        uniform_coverage=covered / k,
        above_rate=len(above) / k,
        below_rate=len(below) / k,
        median_first_above=float(np.median(above)) if above else None,
        median_first_below=float(np.median(below)) if below else None,
        first_failure=first_failure,
    )


def check_streams(cfg: Mapping[str, Any], reps: int | None = None,
                  only: tuple[str, ...] | None = None) -> tuple[list[StreamReport], list[str]]:
    names = list(only or cfg["coverage"]["designs"])
    reports = [run_stream(STREAMS[name], cfg, reps) for name in names]
    failures: list[str] = []
    for r in reports:
        if r.results < r.reps:
            failures.append(f"{r.id}: {r.reps - r.results} replications produced no sequence "
                            f"({r.first_failure})")
        if r.uniform_coverage < cfg["coverage"]["uniform_min"]:
            failures.append(f"{r.id}: uniform coverage {r.uniform_coverage:.3f} < "
                            f"{cfg['coverage']['uniform_min']}")
        if r.id == "benefit" and r.above_rate < cfg["power"]["benefit_min"]:
            failures.append(f"benefit: power {r.above_rate:.3f} < {cfg['power']['benefit_min']}")
        if r.id == "harm" and r.below_rate < cfg["power"]["harm_min"]:
            failures.append(f"harm: power {r.below_rate:.3f} < {cfg['power']['harm_min']}")
    return reports, failures


EXPECTED_REFUSAL = {
    "not_randomized": AbstentionReason.INVALID_INPUT,
    "allocation_unknown": AbstentionReason.INVALID_INPUT,
    "repeated_order": AbstentionReason.INVALID_INPUT,
    "non_binary_treatment": AbstentionReason.INVALID_INPUT,
    "too_short": AbstentionReason.LOW_N,
}


def refusal(case: str, cfg: Mapping[str, Any]) -> Any:
    rng = _rng(cfg["streams"]["seed"], "refusal:" + case)
    frame = simulate(STREAMS["null"], 1000, rng)
    if case == "not_randomized":
        return analyse(frame, 0.5, cfg, design="observational")
    if case == "allocation_unknown":
        return analyse(frame, None, cfg)
    if case == "repeated_order":
        frame.loc[5, "arrival"] = 4
        return analyse(frame, 0.5, cfg)
    if case == "non_binary_treatment":
        frame.loc[frame.index[:30], "canary"] = 2.0
        return analyse(frame, 0.5, cfg)
    if case == "too_short":
        return analyse(frame.head(150), 0.5, cfg)
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


CATCHER: dict[str, str] = {
    "fixed_n_interval": "null",
    "boundary_without_mixture_term": "null",
    "ignore_allocation": "unequal_allocation",
}


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    from causal_core.bench.defects_t6 import DEFECTS

    stream = CATCHER[name]
    with DEFECTS[name]():
        _, failures = check_streams(cfg, reps=reps, only=(stream,))
    return any(f.startswith(f"{stream}: uniform coverage") for f in failures)


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t6 import DEFECTS

    reports, failures = check_streams(cfg, reps)
    refusals, refusal_failures = check_refusals(cfg)
    caught = {name: defect_caught(name, cfg, reps) for name in sorted(DEFECTS)}
    failures += refusal_failures
    if set(cfg["seeded_defects"]["required"]) != set(caught):
        failures.append(f"registered defects {sorted(cfg['seeded_defects']['required'])} != "
                        f"implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "streams": [asdict(r) for r in reports],
        "refusals": refusals,
        "seeded_defects_caught": caught,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Run the pre-registered T6 monitoring gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {
        "suite": "T6",
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

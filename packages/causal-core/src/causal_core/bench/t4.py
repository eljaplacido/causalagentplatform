"""
T4: the segment-effect gate. Bars come from benchmarks/t4-segments/PRE-REGISTRATION.md.

Seven data-generating processes with closed-form segment truths. What is measured:
per-segment bias and coverage, family-wise coverage across all segments of one
analysis, the false-discovery rate when every segment is null, the heterogeneity
test's size and power, and that a segment which cannot be estimated refuses.

The interesting bars are the multiplicity ones. A segment analysis without them is
a machine for producing confident nonsense, and nothing in a per-segment coverage
number would reveal that.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import causal_core as cc
from causal_core.bench.t0 import _map, git_sha
from causal_core.contract import AnalysisPolicy, CausalResult

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t4-segments" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t4-segments/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


# ── Designs, each with the truth per segment written down ────────────────────


@dataclass(frozen=True)
class SegCase:
    id: str
    #: segment level -> true effect. A level mapped to None must refuse.
    truth: Mapping[str, float | None]
    sampler: Callable[[int, np.random.Generator], pd.DataFrame]
    n: int = 4000
    note: str = ""


def _base(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    load = rng.normal(size=n)
    treated = (0.8 * load + rng.logistic(size=n) > 0).astype(float)
    noise = rng.normal(0, 8.0, size=n)
    return load, treated, noise


def _frame(seg: np.ndarray, load: np.ndarray, t: np.ndarray, y: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"seg": seg, "load": load, "playbook": t, "minutes": y})


def homogeneous(n: int, rng: np.random.Generator) -> pd.DataFrame:
    load, t, e = _base(n, rng)
    seg = rng.choice(["a", "b", "c"], size=n)
    y = 60 - 9.0 * t + 10 * load + e
    return _frame(seg, load, t, y)


def sign_flip(n: int, rng: np.random.Generator) -> pd.DataFrame:
    load, t, e = _base(n, rng)
    seg = rng.choice(["p1", "p3"], size=n)
    effect = np.where(seg == "p1", -12.0, 6.0)
    y = 60 + effect * t + 10 * load + e
    return _frame(seg, load, t, y)


def one_responder(n: int, rng: np.random.Generator) -> pd.DataFrame:
    load, t, e = _base(n, rng)
    seg = rng.choice(["w", "x", "y", "z"], size=n)
    effect = np.where(seg == "w", -10.0, 0.0)
    y = 60 + effect * t + 10 * load + e
    return _frame(seg, load, t, y)


def graded(n: int, rng: np.random.Generator) -> pd.DataFrame:
    load, t, e = _base(n, rng)
    seg = rng.choice(["s1", "s2", "s3", "s4"], size=n)
    effect = np.select(
        [seg == "s1", seg == "s2", seg == "s3", seg == "s4"], [0.0, -4.0, -8.0, -12.0]
    )
    y = 60 + effect * t + 10 * load + e
    return _frame(seg, load, t, y)


def confounded_segments(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Confounding that differs by segment: only per-stratum adjustment gets it right."""
    seg = rng.choice(["a", "b"], size=n)
    load = rng.normal(size=n)
    strength = np.where(seg == "a", 1.6, -1.6)
    t = (strength * load + rng.logistic(size=n) > 0).astype(float)
    y = 60 - 9.0 * t + 10 * load + rng.normal(0, 8.0, size=n)
    return _frame(seg, load, t, y)


def sparse_segment(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """One segment with a handful of rows: it must refuse, not guess."""
    load, t, e = _base(n, rng)
    seg = np.where(np.arange(n) < 8, "tiny", "big")
    y = 60 - 9.0 * t + 10 * load + e
    return _frame(seg, load, t, y)


def no_overlap_segment(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """One segment where nobody was treated: no contrast exists to estimate."""
    load, t, e = _base(n, rng)
    seg = rng.choice(["mixed", "untreated"], size=n)
    t = np.where(seg == "untreated", 0.0, t)
    y = 60 - 9.0 * t + 10 * load + e
    return _frame(seg, load, t, y)


CASES: tuple[SegCase, ...] = (
    SegCase("homogeneous", {"a": -9.0, "b": -9.0, "c": -9.0}, homogeneous,
            note="the null for heterogeneity"),
    SegCase("sign_flip", {"p1": -12.0, "p3": 6.0}, sign_flip,
            note="opposite effects; the pooled answer is meaningless"),
    SegCase("one_responder", {"w": -10.0, "x": 0.0, "y": 0.0, "z": 0.0}, one_responder),
    SegCase("graded", {"s1": 0.0, "s2": -4.0, "s3": -8.0, "s4": -12.0}, graded, n=6000),
    SegCase("confounded_segments", {"a": -9.0, "b": -9.0}, confounded_segments,
            note="segment-dependent confounding"),
    SegCase("sparse_segment", {"tiny": None, "big": -9.0}, sparse_segment),
    SegCase("no_overlap_segment", {"mixed": -9.0, "untreated": None}, no_overlap_segment),
)

FAST = AnalysisPolicy(seed=0)


def analyse(frame: pd.DataFrame, policy: AnalysisPolicy = FAST) -> CausalResult | Any:
    return cc.analyze(
        frame,
        GRAPH,
        treatment="playbook",
        outcome="minutes",
        segment_by=("seg",),
        policy=policy,
    )


# ── Per-case measurement ─────────────────────────────────────────────────────


@dataclass
class CaseReport:
    id: str
    reps: int
    per_segment: dict[str, dict[str, float]] = field(default_factory=dict)
    simultaneous_coverage: float = 0.0
    refusal_rate: dict[str, float] = field(default_factory=dict)
    heterogeneity_rate: float = 0.0
    any_rejected_rate: float = 0.0
    failures: list[str] = field(default_factory=list)


def run_case(args: tuple[SegCase, int, int]) -> CaseReport:
    case, reps, seed = args
    # crc32, not hash(): Python randomises string hashing per process, so hash()
    # here made the whole suite unreproducible -- the same committed bars produced
    # different numbers on every run, and a borderline bar flickered between pass
    # and fail. A pre-registered benchmark that cannot be re-run is not evidence.
    rng = np.random.default_rng(seed + zlib.crc32(case.id.encode()) % 10_000)
    covered: dict[str, list[bool]] = {k: [] for k in case.truth}
    fw_covered: dict[str, list[bool]] = {k: [] for k in case.truth}
    points: dict[str, list[float]] = {k: [] for k in case.truth}
    refused: dict[str, int] = dict.fromkeys(case.truth, 0)
    all_fw_covered = 0
    het_fired = 0
    any_rejected = 0
    usable = 0

    for _ in range(reps):
        frame = case.sampler(case.n, rng)
        out = analyse(frame)
        if not isinstance(out, CausalResult):
            continue
        usable += 1
        if out.heterogeneity is not None and out.heterogeneity.differs:
            het_fired += 1
        if any(s.significant_after_correction for s in out.segments):
            any_rejected += 1

        every_fw_ok = True
        for seg in out.segments:
            truth = case.truth.get(seg.level)
            if seg.estimate is None:
                refused[seg.level] = refused.get(seg.level, 0) + 1
                continue
            if truth is None:
                continue  # estimated a segment that should have refused: caught below
            points[seg.level].append(seg.estimate.point)
            ci = seg.estimate.ci
            if ci is not None:
                covered[seg.level].append(ci.lo <= truth <= ci.hi)
            fw = seg.family_wise_ci
            if fw is not None:
                ok = fw.lo <= truth <= fw.hi
                fw_covered[seg.level].append(ok)
                every_fw_ok = every_fw_ok and ok
        if every_fw_ok:
            all_fw_covered += 1

    report = CaseReport(id=case.id, reps=usable)
    for level, truth in case.truth.items():
        n_ref = refused.get(level, 0)
        report.refusal_rate[level] = n_ref / usable if usable else 0.0
        if truth is None:
            continue
        pts = np.array(points[level], dtype=float)
        if pts.size == 0:
            report.failures.append(f"{case.id}/{level}: never estimated")
            continue
        bias = float(pts.mean() - truth)
        mcse = float(pts.std(ddof=1) / np.sqrt(pts.size)) if pts.size > 1 else float("inf")
        report.per_segment[level] = {
            "bias": bias,
            "bias_in_mcse": abs(bias) / mcse if mcse else float("inf"),
            "coverage": float(np.mean(covered[level])) if covered[level] else 0.0,
            "fw_coverage": float(np.mean(fw_covered[level])) if fw_covered[level] else 0.0,
            "n": float(pts.size),
        }
    report.simultaneous_coverage = all_fw_covered / usable if usable else 0.0
    report.heterogeneity_rate = het_fired / usable if usable else 0.0
    report.any_rejected_rate = any_rejected / usable if usable else 0.0
    return report


def check_estimation(
    cfg: Mapping[str, Any],
    reps: int | None = None,
    parallel: bool = True,
    cases: Sequence[SegCase] = CASES,
) -> tuple[list[CaseReport], list[str]]:
    e = cfg["estimation"]
    fw = cfg["family_wise"]
    het = cfg["heterogeneity"]
    ref = cfg["refusals"]
    n_reps = reps or e["reps"]

    reports = list(
        _map(run_case, [(c, n_reps, e["seed"] + i) for i, c in enumerate(cases)], parallel)
    )
    failures: list[str] = []
    by_id = {r.id: r for r in reports}

    for case in cases:
        r = by_id[case.id]
        failures.extend(r.failures)
        for level, stats_ in r.per_segment.items():
            if stats_["bias_in_mcse"] > e["bias_tolerance_se"]:
                failures.append(
                    f"{case.id}/{level}: bias {stats_['bias']:+.3f} = "
                    f"{stats_['bias_in_mcse']:.1f} MCSE"
                )
            if not (e["coverage_lo"] <= stats_["coverage"] <= e["coverage_hi"]):
                failures.append(
                    f"{case.id}/{level}: coverage {stats_['coverage']:.3f} outside "
                    f"[{e['coverage_lo']}, {e['coverage_hi']}]"
                )
        # Family-wise coverage: across every segment of one analysis, at once.
        if r.simultaneous_coverage < fw["simultaneous_coverage_min"]:
            failures.append(
                f"{case.id}: simultaneous coverage {r.simultaneous_coverage:.3f} < "
                f"{fw['simultaneous_coverage_min']}"
            )
        # Segments that cannot be estimated must refuse every time, not sometimes.
        for level, truth in case.truth.items():
            if truth is None and r.refusal_rate.get(level, 0.0) < ref[
                "sparse_and_overlap_refusal_rate"
            ]:
                failures.append(
                    f"{case.id}/{level}: refused {r.refusal_rate.get(level, 0.0):.2f} of the "
                    "time; a segment that cannot be estimated must refuse every time"
                )

    # Every segment shares one effect here, so "this segment differs" is never true;
    # what Holm must control separately is claiming a *non-zero effect* where there
    # is none, which check_false_discovery measures on its own all-null design.
    homogeneous_report = by_id.get("homogeneous")
    if (
        homogeneous_report is not None
        and homogeneous_report.heterogeneity_rate > het["false_positive_max"]
    ):
        failures.append(
            f"heterogeneity false positives {homogeneous_report.heterogeneity_rate:.3f} > "
            f"{het['false_positive_max']} on homogeneous segments"
        )
    responder = by_id.get("one_responder")
    if responder is not None:
        nulls = [lvl for lvl, t in CASES[2].truth.items() if t == 0.0]
        for level in nulls:
            stats_ = responder.per_segment.get(level)
            if stats_ and stats_["fw_coverage"] < fw["simultaneous_coverage_min"]:
                failures.append(
                    f"one_responder/{level}: family-wise coverage {stats_['fw_coverage']:.3f} "
                    "on a null segment"
                )

    flip = by_id.get("sign_flip")
    if flip is not None and flip.heterogeneity_rate < het["power_min"]:
        failures.append(
            f"heterogeneity power {flip.heterogeneity_rate:.3f} < {het['power_min']} on "
            "opposite effects"
        )
    return reports, failures


def check_false_discovery(cfg: Mapping[str, Any], reps: int | None = None) -> tuple[float, bool]:
    """With every segment null, how often does Holm reject anything at all?

    This is the number that says whether the multiplicity correction is real. Run
    on its own design: four segments, no effect anywhere.
    """
    fw = cfg["family_wise"]
    n_reps = reps or cfg["estimation"]["reps"]
    rng = np.random.default_rng(cfg["estimation"]["seed"] + 7)
    fired = 0
    usable = 0
    for _ in range(n_reps):
        load, t, e = _base(3000, rng)
        seg = rng.choice(["a", "b", "c", "d"], size=3000)
        frame = _frame(seg, load, t, 60 + 0.0 * t + 10 * load + e)
        out = analyse(frame)
        if not isinstance(out, CausalResult):
            continue
        usable += 1
        if any(s.significant_after_correction for s in out.segments):
            fired += 1
    rate = fired / usable if usable else 1.0
    return rate, rate <= fw["false_discovery_max"]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from dataclasses import asdict

    ap = argparse.ArgumentParser(description="Run the pre-registered T4 segment gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)

    cfg, prereg_hash = load_prereg()
    reports, failures = check_estimation(cfg, reps=args.reps)
    fdr, fdr_ok = check_false_discovery(cfg, reps=args.reps)
    if not fdr_ok:
        failures.append(
            f"false discovery rate {fdr:.3f} > {cfg['family_wise']['false_discovery_max']} "
            "with every segment null"
        )
    doc = {
        "suite": "T4",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        "cases": [asdict(r) for r in reports],
        "false_discovery_rate": fdr,
        "failures": failures,
        "passed": not failures,
    }
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": failures[:10]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

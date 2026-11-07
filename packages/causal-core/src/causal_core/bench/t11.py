"""
T11: the blocked and paired designs gate. Bars from benchmarks/t11-blocked/PRE-REGISTRATION.md.

Treatment randomized within blocks -- matched pairs and larger strata -- with continuous
outcomes, and binary outcomes in pairs. Measured: bias and coverage of the per-unit average
effect, what pairing buys in interval width, that a binary pair's interval never excludes zero
where the exact McNemar test does not reject, the gnomon S1 table as a known answer, and that
designs the method cannot answer are refused with the registered reason.

    python -m causal_core.bench.t11 --out benchmarks/t11-blocked/results/<date>-v1-run1.json
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

GRAPH = "treated -> y"
#: The unblocked comparison: its primary interval is the difference in means, which neither
#: the refuters nor the sensitivity analysis change.
UNBLOCKED = AnalysisPolicy(refuters=(), sensitivity=False)


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t11-blocked" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t11-blocked/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode())])


# ── designs ───────────────────────────────────────────────────────────────────────────

Sizes = Callable[[np.random.Generator, int], np.ndarray]
Treated = Callable[[np.random.Generator, np.ndarray], np.ndarray]
Effects = Callable[[np.random.Generator, np.ndarray, np.ndarray], np.ndarray]


def _pairs(rng: np.random.Generator, k: int) -> np.ndarray:
    return np.full(k, 2)


def _half(rng: np.random.Generator, sizes: np.ndarray) -> np.ndarray:
    return sizes // 2


@dataclass(frozen=True)
class ContinuousDesign:
    id: str
    k: int
    rho: float
    sizes: Sizes
    treated: Treated
    effects: Effects  # (rng, sizes, n_treated) -> tau_b
    truth: float


def _constant(tau: float) -> Effects:
    return lambda rng, sizes, nt: np.full(sizes.shape, tau)


CONTINUOUS = {
    "pairs_continuous": ContinuousDesign("pairs_continuous", 40, 0.7, _pairs, _half,
                                         _constant(0.3), 0.3),
    "pairs_few": ContinuousDesign("pairs_few", 16, 0.7, _pairs, _half, _constant(0.3), 0.3),
    "pairs_heterogeneous": ContinuousDesign(
        "pairs_heterogeneous", 40, 0.5, _pairs, _half,
        lambda rng, sizes, nt: np.asarray(0.3 + 0.3 * rng.standard_normal(sizes.shape[0])),
        0.3),
    "blocks_unequal": ContinuousDesign(
        "blocks_unequal", 40, 0.5, lambda rng, k: 2 * (1 + rng.poisson(3, k)), _half,
        lambda rng, sizes, nt: np.asarray(0.3 + 0.05 * (sizes - 8.0)), 0.375),
    "blocks_varying_allocation": ContinuousDesign(
        "blocks_varying_allocation", 40, 0.5, lambda rng, k: np.full(k, 8),
        lambda rng, sizes: rng.choice(np.array([2, 4, 6]), sizes.shape[0]),
        lambda rng, sizes, nt: np.where(nt == 4, 0.0, 0.6), 0.4),
}


def simulate_continuous(d: ContinuousDesign, rng: np.random.Generator) -> pd.DataFrame:
    sizes = d.sizes(rng, d.k).astype(int)
    nt = d.treated(rng, sizes).astype(int)
    tau = d.effects(rng, sizes.astype(float), nt)
    block = np.repeat(np.arange(d.k), sizes)
    t = np.concatenate([rng.permutation(np.r_[np.ones(a), np.zeros(s - a)])
                        for s, a in zip(sizes, nt, strict=True)])
    u = rng.normal(0, np.sqrt(d.rho), d.k)
    y = 10 + u[block] + tau[block] * t + rng.normal(0, np.sqrt(1 - d.rho), block.shape[0])
    return pd.DataFrame({"block": block, "treated": t, "y": y})


#: (rng, q per pair, control outcomes) -> treated outcomes
Mechanism = Callable[[np.random.Generator, np.ndarray, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class BinaryDesign:
    id: str
    k: int
    baselines: tuple[float, ...]  # q_b drawn from these, equally likely
    treated: Mechanism
    truth: float


def _independent(shift: float) -> Mechanism:
    def treated(rng: np.random.Generator, q: np.ndarray, y0: np.ndarray) -> np.ndarray:
        return np.asarray(rng.random(q.shape[0]) < q + shift, dtype=float)

    return treated


BINARY = {
    "binary_pairs_correlated": BinaryDesign("binary_pairs_correlated", 30, (0.05, 0.85),
                                            _independent(0.1), 0.1),
    "binary_pairs_null": BinaryDesign("binary_pairs_null", 30, (0.05, 0.85), _independent(0.0),
                                      0.0),
    "binary_pairs_one_sided": BinaryDesign(
        "binary_pairs_one_sided", 40, (0.3, 0.7),
        lambda rng, q, y0: np.where(
            y0 == 1, 1.0, np.asarray(rng.random(q.shape[0]) < 0.05 / (1 - q), dtype=float)),
        0.05),
    "binary_pairs_s1_like": BinaryDesign(
        "binary_pairs_s1_like", 30, (19 / 30,),
        lambda rng, q, y0: np.where(
            y0 == 0, 0.0, np.asarray(rng.random(q.shape[0]) < 15 / 19, dtype=float)),
        -4 / 30),
}


def simulate_binary(d: BinaryDesign, rng: np.random.Generator) -> pd.DataFrame:
    q = rng.choice(np.array(d.baselines), d.k)
    y0 = (rng.random(d.k) < q).astype(float)
    y1 = d.treated(rng, q, y0)
    block = np.r_[np.arange(d.k), np.arange(d.k)]
    return pd.DataFrame({"block": block, "treated": np.r_[np.ones(d.k), np.zeros(d.k)],
                         "y": np.r_[y1, y0]})


def analyse(frame: pd.DataFrame, policy: AnalysisPolicy | None = None,
            design: str = "randomized", block: str | None = "block") -> Any:
    kw: dict[str, Any] = {"block": block} if block else {}
    return cc.analyze(frame, GRAPH, treatment="treated", outcome="y", design=design,
                      policy=policy or AnalysisPolicy(), **kw)


# ── estimation ────────────────────────────────────────────────────────────────────────


@dataclass
class DesignReport:
    id: str
    reps: int
    results: int
    truth: float
    mean_error: float
    mcse: float
    bias_in_mcse: float
    coverage: float
    mean_width: float
    mean_unblocked_width: float | None = None
    width_ratio: float | None = None
    mcnemar_inconsistent: int = 0
    first_failure: str = ""


def run_design(design_id: str, cfg: Mapping[str, Any], reps: int | None = None) -> DesignReport:
    c = cfg["blocks"]
    n_reps = reps or c["reps"]
    rng = _rng(c["seed"], design_id)
    widths_ratio = cfg["efficiency"]["width_ratio_max"]
    binary = design_id in BINARY
    truth = BINARY[design_id].truth if binary else CONTINUOUS[design_id].truth
    level = AnalysisPolicy().ci_level
    errors: list[float] = []
    widths: list[float] = []
    unblocked: list[float] = []
    covered = 0
    inconsistent = 0
    first_failure = ""
    for _ in range(n_reps):
        frame = (simulate_binary(BINARY[design_id], rng) if binary
                 else simulate_continuous(CONTINUOUS[design_id], rng))
        out = analyse(frame)
        if not isinstance(out, CausalResult) or out.primary.ci is None:
            first_failure = first_failure or f"{out.kind}: {getattr(out, 'detail', '')}"[:200]
            continue
        ci = out.primary.ci
        errors.append(out.primary.point - truth)
        widths.append(ci.hi - ci.lo)
        covered += bool(ci.lo <= truth <= ci.hi)
        if binary and out.blocks is not None and out.blocks.mcnemar_exact_p is not None:
            excludes = ci.lo > 0 or ci.hi < 0
            inconsistent += bool(excludes and out.blocks.mcnemar_exact_p >= 1 - level)
        if design_id in widths_ratio:
            plain = analyse(frame, UNBLOCKED, block=None)
            if isinstance(plain, CausalResult) and plain.primary.ci is not None:
                unblocked.append(plain.primary.ci.hi - plain.primary.ci.lo)
    k = len(errors)
    mean = float(np.mean(errors)) if k else float("nan")
    mcse = float(np.std(errors, ddof=1) / np.sqrt(k)) if k > 1 else float("nan")
    mean_w = float(np.mean(widths)) if widths else float("nan")
    mean_u = float(np.mean(unblocked)) if unblocked else None
    return DesignReport(
        id=design_id, reps=n_reps, results=k, truth=truth, mean_error=mean, mcse=mcse,
        bias_in_mcse=abs(mean) / mcse if mcse and mcse > 0 else (0.0 if mean == 0 else
                                                                  float("inf")),
        coverage=covered / k if k else 0.0,
        mean_width=mean_w,
        mean_unblocked_width=mean_u,
        width_ratio=mean_w / mean_u if mean_u else None,
        mcnemar_inconsistent=inconsistent,
        first_failure=first_failure,
    )


def check_estimation(cfg: Mapping[str, Any], reps: int | None = None,
                     only: tuple[str, ...] | None = None) -> tuple[list[DesignReport], list[str]]:
    est = cfg["estimation"]
    ids = only or (*est["continuous_designs"], *est["binary_designs"])
    reports = [run_design(i, cfg, reps) for i in ids]
    failures: list[str] = []
    lo, hi = est["coverage_band"]
    ratio_max = cfg["efficiency"]["width_ratio_max"]
    for r in reports:
        if r.results < r.reps:
            failures.append(f"{r.id}: {r.reps - r.results} replications produced no result "
                            f"({r.first_failure})")
        if r.bias_in_mcse > est["bias_mcse_max"]:
            failures.append(f"{r.id}: bias {r.mean_error:+.4f} is {r.bias_in_mcse:.1f} MCSE")
        if r.id in BINARY:
            if r.coverage < est["binary_coverage_min"]:
                failures.append(f"{r.id}: coverage {r.coverage:.3f} < {est['binary_coverage_min']}")
            if r.mcnemar_inconsistent > est["mcnemar_inconsistent_max"]:
                failures.append(f"{r.id}: {r.mcnemar_inconsistent} intervals exclude 0 where the "
                                "exact McNemar test does not reject")
        elif r.id == "pairs_few":
            if r.coverage < est["pairs_few_coverage_min"]:
                failures.append(f"{r.id}: coverage {r.coverage:.3f} < "
                                f"{est['pairs_few_coverage_min']}")
        elif not lo <= r.coverage <= hi:
            failures.append(f"{r.id}: coverage {r.coverage:.3f} outside [{lo}, {hi}]")
        if r.id in ratio_max:
            if r.width_ratio is None:
                failures.append(f"{r.id}: width ratio not measured")
            elif r.width_ratio > ratio_max[r.id]:
                failures.append(f"{r.id}: width ratio {r.width_ratio:.3f} > {ratio_max[r.id]}")
    return reports, failures


# ── the known answer: gnomon S1 ────────────────────────────────────────────────────────


def s1_frame() -> pd.DataFrame:
    """gnomon S1 (steering delivered vs dropped): 30 pairs, discordant 0-4, and one cell valid
    only in the delivered arm (solved), which makes the delivered arm 16/31 unpaired."""
    both, delivered_only, dropped_only, neither = 15, 0, 4, 11
    rows: list[tuple[str, float, float]] = []
    kinds = ([(1.0, 1.0)] * both + [(1.0, 0.0)] * delivered_only + [(0.0, 1.0)] * dropped_only
             + [(0.0, 0.0)] * neither)
    for i, (y_delivered, y_dropped) in enumerate(kinds):
        rows += [(f"task{i:02d}#1", 1.0, y_delivered), (f"task{i:02d}#1", 0.0, y_dropped)]
    rows.append(("task30#1", 1.0, 1.0))
    return pd.DataFrame(rows, columns=["pair", "delivered", "solved"])


def s1_analyse(policy: AnalysisPolicy) -> Any:
    return cc.analyze(s1_frame(), "delivered -> solved", treatment="delivered",
                      outcome="solved", design="randomized", block="pair", policy=policy)


def check_s1(cfg: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    want = cfg["s1"]
    failures: list[str] = []
    strict = s1_analyse(AnalysisPolicy())
    if getattr(strict, "reason", None) is not AbstentionReason.DESIGN_CHECK_FAILED:
        failures.append(f"s1 with missing='error': expected DESIGN_CHECK_FAILED, got "
                        f"{strict.kind}:{getattr(strict, 'reason', None)}")
    out = s1_analyse(AnalysisPolicy(missing="drop"))
    if not isinstance(out, CausalResult) or out.blocks is None or out.primary.ci is None:
        return {"kind": out.kind}, [*failures, f"s1 with missing='drop': no result ({out.kind})"]
    bs, ci = out.blocks, out.primary.ci
    frame = s1_frame()
    complete = frame[frame["pair"] != "task30#1"]
    got = {
        "pairs": bs.n_blocks,
        "incomplete_blocks": bs.incomplete_blocks,
        "units_left_out": bs.units_left_out,
        "discordant_treated": bs.discordant_treated,
        "discordant_control": bs.discordant_control,
        "treated_solved": int(complete.loc[complete["delivered"] == 1, "solved"].sum()),
        "control_solved": int(complete.loc[complete["delivered"] == 0, "solved"].sum()),
        "point": out.primary.point,
        "ci": [ci.lo, ci.hi],
        "mcnemar_exact_p": bs.mcnemar_exact_p,
        "interval_contains_zero": ci.lo <= 0 <= ci.hi,
        "estimator": out.primary.estimator,
    }
    for key in ("pairs", "incomplete_blocks", "units_left_out", "discordant_treated",
                "discordant_control", "treated_solved", "control_solved",
                "interval_contains_zero"):
        if got[key] != want[key]:
            failures.append(f"s1 {key}: expected {want[key]}, got {got[key]}")
    if abs(got["point"] - want["point"]) > want["point_tolerance"]:
        failures.append(f"s1 point: expected {want['point']}, got {got['point']}")
    p_exact = got["mcnemar_exact_p"]
    if p_exact is None or abs(p_exact - want["mcnemar_exact_p"]) > 1e-12:
        failures.append(f"s1 mcnemar_exact_p: expected {want['mcnemar_exact_p']}, got "
                        f"{got['mcnemar_exact_p']}")
    return got, failures


# ── refusals ──────────────────────────────────────────────────────────────────────────

EXPECTED_REFUSAL = {
    "not_randomized": AbstentionReason.INVALID_INPUT,
    "constant_treatment": AbstentionReason.NO_VARIATION,
    "incomplete_blocks": AbstentionReason.DESIGN_CHECK_FAILED,
    "too_few_blocks": AbstentionReason.LOW_N,
}


def _incomplete(cfg: Mapping[str, Any]) -> pd.DataFrame:
    rng = _rng(cfg["blocks"]["seed"], "refusal:incomplete_blocks")
    frame = simulate_continuous(CONTINUOUS["pairs_continuous"], rng)
    drop = [frame.index[frame["block"] == b][0] for b in (0, 1, 2)]
    return frame.drop(index=drop)


def refusal(case: str, cfg: Mapping[str, Any]) -> Any:
    rng = _rng(cfg["blocks"]["seed"], "refusal:" + case)
    if case == "too_few_blocks":
        few = ContinuousDesign("x", 8, 0.7, lambda r, k: np.full(k, 4), _half, _constant(0.3),
                               0.3)
        return analyse(simulate_continuous(few, rng))
    if case == "incomplete_blocks":
        return analyse(_incomplete(cfg))
    frame = simulate_continuous(CONTINUOUS["pairs_continuous"], rng)
    if case == "not_randomized":
        return analyse(frame, design="observational")
    if case == "constant_treatment":
        return analyse(frame.assign(treated=0.0))
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
    if cfg["refusals"]["drop_counts_exact"]:
        out = analyse(_incomplete(cfg), AnalysisPolicy(missing="drop"))
        bs = out.blocks if isinstance(out, CausalResult) else None
        counts = None if bs is None else (bs.incomplete_blocks, bs.units_left_out, bs.n_blocks)
        got["incomplete_blocks_dropped"] = str(counts)
        if counts != (3, 3, 37):
            failures.append(f"with missing='drop', expected (3 incomplete, 3 units left out, 37 "
                            f"blocks), got {counts}")
    return got, failures


# ── seeded defects ────────────────────────────────────────────────────────────────────

CATCHER: dict[str, tuple[str, str]] = {
    "unpaired_se": ("pairs_continuous", ": width ratio"),
    "wald_for_binary_pairs": ("binary_pairs_one_sided", ": coverage"),
    "fixed_effects_weights": ("blocks_varying_allocation", ": bias"),
}


def defect_report(name: str, cfg: Mapping[str, Any],
                  reps: int | None = None) -> tuple[bool, DesignReport]:
    from causal_core.bench.defects_t11 import DEFECTS

    design, marker = CATCHER[name]
    with DEFECTS[name]():
        reports, failures = check_estimation(cfg, reps=reps, only=(design,))
    return any(f.startswith(design) and marker in f for f in failures), reports[0]


def defect_caught(name: str, cfg: Mapping[str, Any], reps: int | None = None) -> bool:
    return defect_report(name, cfg, reps)[0]


def run(cfg: Mapping[str, Any], reps: int | None = None) -> dict[str, Any]:
    from causal_core.bench.defects_t11 import DEFECTS

    reports, failures = check_estimation(cfg, reps)
    s1, s1_failures = check_s1(cfg)
    refusals, refusal_failures = check_refusals(cfg)
    under = {name: defect_report(name, cfg, reps) for name in sorted(DEFECTS)}
    caught = {name: ok for name, (ok, _) in under.items()}
    failures += s1_failures + refusal_failures
    if set(cfg["seeded_defects"]["required"]) != set(caught):
        failures.append(f"registered defects {sorted(cfg['seeded_defects']['required'])} != "
                        f"implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "designs": [asdict(r) for r in reports],
        "s1": s1,
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

    ap = argparse.ArgumentParser(description="Run the pre-registered T11 blocked-designs gate")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {"suite": "T11", "prereg_sha256": prereg_hash, "prereg_version": cfg["meta"]["version"],
           "git": git_sha(), **run(cfg, args.reps)}
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:12]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

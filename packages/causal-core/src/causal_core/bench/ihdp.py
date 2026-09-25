"""
T2 IHDP: real covariates, simulated nonlinear potential outcomes, overlap
broken by design. Thresholds come from ``benchmarks/t2-ihdp/PRE-REGISTRATION.md``.

    python -m causal_core.bench.ihdp --out benchmarks/t2-ihdp/results/latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import multiprocessing
import os
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from causal_core.bench.lalonde import load_prereg as _load_toml_block
from causal_core.contract import Abstention, AnalysisPolicy, CausalResult, ResultStatus
from causal_core.pipeline import analyze

COVARIATES = tuple(f"x{i}" for i in range(25))
GRAPH = "; ".join([f"{c} -> t; {c} -> y" for c in COVARIATES] + ["t -> y"])
CONFIGS = {"C1": {}, "C2": {"learners": "flexible"}}


def prereg_path() -> Path:
    env = os.environ.get("CAUSAL_IHDP_PREREG")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t2-ihdp" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t2-ihdp/PRE-REGISTRATION.md not found")


def cache_dir() -> Path:
    base = os.environ.get("CAUSAL_CORE_CACHE")
    d = (Path(base) if base else Path.home() / ".cache" / "causal-core") / "ihdp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(cfg: dict[str, Any]) -> dict[str, bytes]:
    out = {}
    for split in ("train", "test"):
        name = f"ihdp_npci_1-100.{split}.npz"
        path = cache_dir() / name
        want = cfg["data"][split]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            with urllib.request.urlopen(cfg["data"]["base_url"] + name, timeout=120) as resp:
                blob = resp.read()
            got = hashlib.sha256(blob).hexdigest()
            if got != want:
                raise ValueError(f"{name}: sha256 {got} does not match the pinned {want}")
            path.write_bytes(blob)
        out[split] = path.read_bytes()
    return out


def realizations(blobs: dict[str, bytes]) -> list[tuple[pd.DataFrame, float, float]]:
    """(frame, ATE truth, ATT truth) for each of the 100 realizations (train + test pooled)."""
    parts = [np.load(io.BytesIO(blobs[s])) for s in ("train", "test")]

    def cat(key: str) -> np.ndarray:
        return np.concatenate([p[key] for p in parts], axis=0)

    x, t, y, mu1, mu0 = cat("x"), cat("t"), cat("yf"), cat("mu1"), cat("mu0")
    out = []
    for r in range(t.shape[1]):
        frame = pd.DataFrame(x[:, :, r], columns=list(COVARIATES)).assign(t=t[:, r], y=y[:, r])
        ite = mu1[:, r] - mu0[:, r]
        out.append((frame, float(ite.mean()), float(ite[t[:, r] == 1].mean())))
    return out


def _one(args: tuple[int, pd.DataFrame, float, str, str]) -> dict[str, Any]:
    r, frame, truth, estimand, config = args
    out = analyze(
        frame,
        GRAPH,
        treatment="t",
        outcome="y",
        estimand=estimand,
        policy=AnalysisPolicy(seed=r, **CONFIGS[config]),
    )
    rec: dict[str, Any] = {"r": r, "truth": truth, "kind": out.kind}
    if isinstance(out, CausalResult):
        ci = out.primary.ci
        rec.update(
            status=out.status.value,
            point=out.primary.point,
            se=out.primary.se,
            covered=bool(ci and ci.lo <= truth <= ci.hi),
            abs_error=abs(out.primary.point - truth),
        )
    else:
        reason = out.reason.value if isinstance(out, Abstention) else out.error_type
        rec.update(reason=reason, detail=out.detail[:200])
    return rec


def _ols_hc1(frame: pd.DataFrame) -> tuple[float, float]:
    """Coefficient on t from OLS of y on t and every covariate, with its HC1 standard error."""
    x = np.column_stack([np.ones(len(frame)), frame["t"].to_numpy(float),
                         frame[list(COVARIATES)].to_numpy(float)])
    y = frame["y"].to_numpy(float)
    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    resid = y - x @ beta
    n, k = x.shape
    meat = (x * resid[:, None] ** 2).T @ x
    cov = xtx_inv @ meat @ xtx_inv * n / (n - k)
    return float(beta[1]), float(np.sqrt(cov[1, 1]))


def rivals(reals: list[tuple[pd.DataFrame, float, float]]) -> dict[str, Any]:
    """What an analyst without the kernel would report (T2 v1.1): reported, never gated."""
    naive_ate, naive_att, ols_ate, ols_att, ols_cov = [], [], [], [], []
    for frame, ate, att in reals:
        t = frame["t"].to_numpy(float)
        y = frame["y"].to_numpy(float)
        dim = float(y[t == 1].mean() - y[t == 0].mean())
        naive_ate.append(abs(dim - ate))
        naive_att.append(abs(dim - att))
        b, se = _ols_hc1(frame)
        ols_ate.append(abs(b - ate))
        ols_att.append(abs(b - att))
        ols_cov.append(b - 1.959964 * se <= ate <= b + 1.959964 * se)
    return {
        "naive_difference_in_means": {
            "ate_mean_abs_error": float(np.mean(naive_ate)),
            "att_mean_abs_error": float(np.mean(naive_att)),
        },
        "ols_with_covariates_hc1": {
            "ate_mean_abs_error": float(np.mean(ols_ate)),
            "att_mean_abs_error": float(np.mean(ols_att)),
            "ate_coverage": float(np.mean(ols_cov)),
        },
        "note": "reported, not gated; IHDP ranks estimators inconsistently across realizations",
    }


def run(
    cfg: dict[str, Any], reals: list[tuple[pd.DataFrame, float, float]], parallel: bool = True
) -> dict[str, Any]:
    jobs = []
    for estimand in ("ate", "att"):
        for config in CONFIGS:
            for r, (frame, ate, att) in enumerate(reals):
                jobs.append((r, frame, ate if estimand == "ate" else att, estimand, config))
    keys = [(j[3], j[4]) for j in jobs]
    if parallel:
        # One thread per worker: gradient boosting and BLAS are multithreaded, and N
        # workers x N threads oversubscribes the machine by an order of magnitude.
        # Spawned (not forked) workers read these variables before their libraries start.
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[var] = "1"
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=os.cpu_count() or 1, mp_context=ctx) as ex:
            recs = list(ex.map(_one, jobs, chunksize=4))
    else:
        recs = [_one(j) for j in jobs]
    g = cfg["gate"]
    summary: dict[str, Any] = {}
    failures: list[str] = []
    supported_misses = supported_total = 0
    for estimand in ("ate", "att"):
        for config in CONFIGS:
            rows = [rec for rec, k in zip(recs, keys, strict=True) if k == (estimand, config)]
            res = [x for x in rows if x["kind"] == "result"]
            cov = float(np.mean([x["covered"] for x in res])) if res else None
            sup = [x for x in res if x["status"] == ResultStatus.supported.value]
            supported_total += len(sup)
            supported_misses += sum(not x["covered"] for x in sup)
            summary[f"{estimand}/{config}"] = {
                "results": len(res),
                "refusals": Counter(x.get("reason") for x in rows if x["kind"] == "refusal"),
                "apparatus_failures": sum(x["kind"] == "apparatus_failure" for x in rows),
                "status": Counter(x["status"] for x in res),
                "coverage": cov,
                "mean_abs_error": float(np.mean([x["abs_error"] for x in res])) if res else None,
                "example_refusal": next(
                    (x["detail"] for x in rows if x["kind"] == "refusal"), None
                ),
            }
            if estimand == "att":
                share = len(res) / len(rows)
                if share < g["att_min_result_share"]:
                    failures.append(f"att/{config}: only {share:.2f} of realizations answered")
                if cov is not None and cov < g["att_min_coverage"]:
                    failures.append(f"att/{config}: coverage {cov:.3f} < {g['att_min_coverage']}")
            elif cov is not None and cov < g["ate_min_coverage_if_answered"]:
                failures.append(
                    f"ate/{config}: coverage {cov:.3f} of answered realizations "
                    f"< {g['ate_min_coverage_if_answered']}"
                )
            if any(x["kind"] == "apparatus_failure" for x in rows):
                failures.append(f"{estimand}/{config}: apparatus failures")
    miss_share = supported_misses / supported_total if supported_total else 0.0
    if miss_share > g["max_supported_miss_share"]:
        failures.append(f"supported results missed the truth {miss_share:.3f} of the time")
    return {
        "summary": summary,
        "supported_miss_share": miss_share,
        "supported_total": supported_total,
        "rivals": rivals(reals),
        "failures": failures,
        "passed": not failures,
        "records": recs,
    }


def main(argv: list[str] | None = None) -> int:
    from causal_core.bench.t0 import git_sha

    ap = argparse.ArgumentParser(description="Run the pre-registered IHDP benchmark")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--serial", action="store_true")
    args = ap.parse_args(argv)
    cfg, prereg_hash = _load_toml_block(prereg_path())
    result = run(cfg, realizations(fetch(cfg)), parallel=not args.serial)
    doc = {
        "suite": "T2-IHDP",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        **result,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

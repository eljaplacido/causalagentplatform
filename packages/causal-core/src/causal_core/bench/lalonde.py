"""
T3 LaLonde: real data with an experimental benchmark.

Thresholds come from ``benchmarks/t3-lalonde/PRE-REGISTRATION.md``. The data
have no stated redistribution licence, so they are downloaded from the
original source on demand, verified against pinned SHA-256s, and cached under
``$CAUSAL_CORE_CACHE`` (default ``~/.cache/causal-core``) -- never committed.

    python -m causal_core.bench.lalonde --out benchmarks/t3-lalonde/results/latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from causal_core.contract import AnalysisPolicy, CausalResult, ResultStatus
from causal_core.estimators._linalg import add_intercept, ols
from causal_core.pipeline import analyze
from causal_core.report import report

COVARIATES = (
    "age", "education", "black", "hispanic", "married", "nodegree", "re74", "re75", "u74", "u75",
)  # fmt: skip
FILES = ("nsw_dw", "psid_controls", "cps_controls")


def prereg_path() -> Path:
    env = os.environ.get("CAUSAL_LALONDE_PREREG")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "t3-lalonde" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/t3-lalonde/PRE-REGISTRATION.md not found")


def load_prereg(path: Path | None = None) -> tuple[dict[str, Any], str]:
    p = path or prereg_path()
    raw = p.read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError(f"{p} has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


def cache_dir() -> Path:
    base = os.environ.get("CAUSAL_CORE_CACHE")
    d = Path(base) if base else Path.home() / ".cache" / "causal-core"
    d = d / "lalonde"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(cfg: dict[str, Any]) -> dict[str, Path]:
    """Download (once) and verify every file against its pinned SHA-256."""
    out = {}
    for name in FILES:
        path = cache_dir() / f"{name}.dta"
        want = cfg["data"][name]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            url = cfg["data"]["base_url"] + f"{name}.dta"
            with urllib.request.urlopen(
                url, timeout=60
            ) as resp:  # pinned https URL, SHA-256 checked below
                blob = resp.read()
            got = hashlib.sha256(blob).hexdigest()
            if got != want:
                raise ValueError(f"{name}: sha256 {got} does not match the pinned {want}")
            path.write_bytes(blob)
        out[name] = path
    return out


def load(paths: dict[str, Path]) -> dict[str, pd.DataFrame]:
    frames = {}
    for name, path in paths.items():
        d = pd.read_stata(path)
        d = d.assign(u74=(d["re74"] == 0).astype(float), u75=(d["re75"] == 0).astype(float))
        frames[name] = d.drop(columns=["data_id"]).astype(float)
    return frames


def _graph(covariates: tuple[str, ...], outcome: str, randomized: bool) -> str:
    edges = [f"{c} -> {outcome}" for c in covariates]
    if not randomized:
        edges += [f"{c} -> treat" for c in covariates]
    edges.append(f"treat -> {outcome}")
    return "; ".join(edges)


def _summary(out: Any) -> dict[str, Any]:
    if isinstance(out, CausalResult):
        ci = out.primary.ci
        return {
            "kind": out.kind,
            "status": out.status.value,
            "estimator": out.primary.estimator,
            "point": out.primary.point,
            "se": out.primary.se,
            "ci": [ci.lo, ci.hi] if ci else None,
            "estimates": {e.estimator: [e.point, e.se] for e in out.estimates},
            "refutations": {r.name: r.passed for r in out.refutations},
            "triangulation_agree": out.triangulation.agree if out.triangulation else None,
            "propensity": out.diagnostics.propensity.model_dump()
            if out.diagnostics.propensity
            else None,
            "e_value": out.sensitivity.e_value if out.sensitivity else None,
            "warnings": list(out.warnings),
        }
    return {
        "kind": out.kind,
        "reason": getattr(out, "reason", None) and out.reason.value,
        "detail": out.detail,
        "report": report(out),
    }


def _naive(frame: pd.DataFrame) -> dict[str, float]:
    t = frame["treat"].to_numpy()
    y = frame["re78"].to_numpy()
    X = add_intercept(np.column_stack([t, frame[list(COVARIATES)].to_numpy()]))
    return {
        "difference_in_means": float(y[t == 1].mean() - y[t == 0].mean()),
        "ols_with_covariates": float(ols(y, X).beta[1]),
    }


def run(cfg: dict[str, Any], frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    nsw = frames["nsw_dw"]
    exp_out = analyze(
        nsw,
        _graph(COVARIATES, "re78", randomized=True),
        treatment="treat",
        outcome="re78",
        design="randomized",
        estimand="att",
    )
    assert isinstance(exp_out, CausalResult), report(exp_out)
    exp_point, exp_se = exp_out.primary.point, exp_out.primary.se or math.nan
    failures: list[str] = []
    if (
        abs(exp_point - cfg["experiment"]["published_point"])
        > cfg["experiment"]["max_abs_deviation"]
    ):
        failures.append(
            f"experimental benchmark {exp_point:.2f} does not reproduce the published "
            f"{cfg['experiment']['published_point']}: wrong data?"
        )

    configs = {"C1": AnalysisPolicy(), "C2": AnalysisPolicy(learners="flexible")}
    observational: dict[str, Any] = {}
    confident_wrong = []
    z = cfg["safety"]["z"]
    for comp in ("psid_controls", "cps_controls"):
        pooled = pd.concat([nsw[nsw["treat"] == 1], frames[comp]], ignore_index=True)
        design = f"O-{comp.split('_')[0].upper()}"
        g = _graph(COVARIATES, "re78", randomized=False)
        entry: dict[str, Any] = {
            "n_treated": int(pooled["treat"].sum()),
            "n_control": int((pooled["treat"] == 0).sum()),
            "naive": _naive(pooled),
        }
        for cname, pol in configs.items():
            out = analyze(pooled, g, treatment="treat", outcome="re78", estimand="att", policy=pol)
            s = _summary(out)
            if isinstance(out, CausalResult):
                se_obs = out.primary.se or math.nan
                gap = out.primary.point - exp_point
                comb = math.sqrt(se_obs**2 + exp_se**2)
                s["gap_to_experiment"] = gap
                s["gap_in_combined_se"] = gap / comb
                s["bias_in_rct_se"] = gap / exp_se
                ci = out.primary.ci
                s["covers_experimental_point"] = bool(ci and ci.lo <= exp_point <= ci.hi)
                if out.status is ResultStatus.supported and abs(gap) > z * comb:
                    confident_wrong.append(
                        f"{design}/{cname}: {out.primary.point:.0f} vs "
                        f"{exp_point:.0f} ({gap / comb:+.2f} combined SE)"
                    )
            entry[cname] = s
        placebo_cov = tuple(c for c in COVARIATES if c not in ("re75", "u75"))
        placebo = analyze(
            pooled.drop(columns=["re78"]),
            _graph(placebo_cov, "re75", randomized=False),
            treatment="treat",
            outcome="re75",
            estimand="att",
        )
        entry["placebo_re75"] = _summary(placebo)
        observational[design] = entry

    if len(confident_wrong) > cfg["safety"]["max_confident_wrong"]:
        failures.append(f"confident wrong answers: {confident_wrong}")
    return {
        "experiment": {**_summary(exp_out), "naive": _naive(nsw)},
        "observational": observational,
        "confident_wrong": confident_wrong,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    from causal_core.bench.t0 import git_sha

    ap = argparse.ArgumentParser(description="Run the pre-registered LaLonde benchmark")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    frames = load(fetch(cfg))
    result = run(cfg, frames)
    doc = {
        "suite": "T3-LaLonde",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        **result,
    }
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

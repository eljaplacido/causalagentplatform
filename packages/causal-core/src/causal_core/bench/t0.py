"""
T0: the analytic-SCM correctness gate.

Every threshold is read from ``benchmarks/t0/PRE-REGISTRATION.md`` (the TOML
block), whose SHA-256 is stamped on every report. The catalogs below are the
labelled ground truth; labels come from the identification literature, not
from running this code.

Run it:

    python -m causal_core.bench.t0 --out benchmarks/t0/results/latest.json
    pytest -m t0            # the same checks as tests
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from causal_core.contract import (
    AnalysisPolicy,
    CausalQuery,
    CausalResult,
)
from causal_core.graph import Graph, parse_graph
from causal_core.identification import IdentificationError, identify
from causal_core.identification.id_algorithm import ADMG, identify_id
from causal_core.pipeline import analyze
from causal_core.refute import holm
from causal_core.scm import LinearSCM

# ── pre-registration ──────────────────────────────────────────────────────────


def default_prereg_path() -> Path:
    env = os.environ.get("CAUSAL_T0_PREREG")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "benchmarks" / "t0" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError(
        "benchmarks/t0/PRE-REGISTRATION.md not found; set CAUSAL_T0_PREREG to its path"
    )


def load_prereg(path: Path | None = None) -> tuple[dict[str, Any], str]:
    p = path or default_prereg_path()
    raw = p.read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError(f"{p} has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


# ── identification catalog ────────────────────────────────────────────────────


@dataclass(frozen=True)
class IdCase:
    id: str
    graph: str
    status: str  # "identified" | "not_identified"
    strategy: str | None
    query: Mapping[str, Any] = field(default_factory=dict)
    ref: str = ""


IDENTIFICATION_CASES: tuple[IdCase, ...] = (
    IdCase("direct", "X -> Y", "identified", "backdoor", ref="trivial"),
    IdCase("confounder", "Z -> X; Z -> Y; X -> Y", "identified", "backdoor", ref="Pearl 2009 3.3"),
    IdCase(
        "two_confounders", "Z1 -> X; Z1 -> Y; Z2 -> X; Z2 -> Y; X -> Y", "identified", "backdoor"
    ),
    IdCase("mediator", "Z -> X; Z -> Y; X -> M -> Y", "identified", "backdoor"),
    IdCase(
        "m_bias",
        "A -> M; B -> M; A -> X; B -> Y; X -> Y",
        "identified",
        "backdoor",
        ref="Greenland 2003",
    ),
    IdCase(
        "butterfly",
        "A -> M; B -> M; A -> X; B -> Y; M -> X; M -> Y; X -> Y",
        "identified",
        "backdoor",
        ref="Ding & Miratrix 2015",
    ),
    IdCase("collider_after", "X -> Y; X -> C; Y -> C", "identified", "backdoor"),
    IdCase("precision", "X -> Y; W -> Y", "identified", "backdoor"),
    IdCase("latent_blocked", "latent U; U -> Z; Z -> X; U -> Y; X -> Y", "identified", "backdoor"),
    IdCase(
        "selection_collider", "latent U; X -> S; U -> S; U -> Y; X -> Y", "identified", "backdoor"
    ),
    IdCase("long_backdoor", "A -> B -> C -> X; A -> D -> Y; X -> Y", "identified", "backdoor"),
    IdCase("confounded_mediator_only", "X -> M -> Y; M <-> Y", "identified", "backdoor"),
    IdCase("confounded_mediator_direct", "X -> M -> Y; X -> Y; M <-> Y", "identified", "backdoor"),
    IdCase("descendant_of_treatment", "X -> D; Z -> X; Z -> Y; X -> Y", "identified", "backdoor"),
    IdCase("frontdoor", "X -> M -> Y; X <-> Y", "identified", "frontdoor", ref="Pearl 1995"),
    IdCase("frontdoor_chain", "X -> M1 -> M2 -> Y; X <-> Y", "identified", "frontdoor"),
    IdCase(
        "napkin",
        "W1 -> W2 -> X -> Y; W1 <-> X; W1 <-> Y",
        "identified",
        "id_general",
        ref="Pearl & Mackenzie 2018; Shpitser & Pearl 2006",
    ),
    IdCase("bow", "X -> Y; X <-> Y", "not_identified", None, ref="Tian & Pearl 2002"),
    IdCase("latent_confounder", "latent U; U -> X; U -> Y; X -> Y", "not_identified", None),
    IdCase("bow_on_mediator", "X -> M -> Y; X <-> M", "not_identified", None),
    IdCase("frontdoor_broken", "X -> M -> Y; X <-> Y; M <-> Y", "not_identified", None),
    IdCase("iv", "Z -> X -> Y; X <-> Y", "identified", "iv", ref="Angrist, Imbens & Rubin 1996"),
    IdCase("iv_two", "Z1 -> X; Z2 -> X; X -> Y; X <-> Y", "identified", "iv"),
    IdCase(
        "iv_conditional",
        "C -> Z -> X -> Y; C -> Y; X <-> Y",
        "identified",
        "iv",
        ref="Brito & Pearl 2002",
    ),
    IdCase("iv_exclusion_violated", "Z -> X -> Y; Z -> Y; X <-> Y", "not_identified", None),
    IdCase(
        "proximal",
        "latent U; U -> X; U -> Y; U -> Zp; U -> Wp; X -> Y",
        "identified",
        "proximal",
        {"treatment_proxies": ("Zp",), "outcome_proxies": ("Wp",)},
        ref="Miao, Geng & Tchetgen Tchetgen 2018",
    ),
    IdCase(
        "proximal_undeclared",
        "latent U; U -> X; U -> Y; U -> Zp; U -> Wp; X -> Y",
        "not_identified",
        None,
    ),
    IdCase("randomized", "X -> Y; W -> Y", "identified", "randomized", {"design": "randomized"}),
    IdCase(
        "randomized_contradicted",
        "Z -> X; Z -> Y; X -> Y",
        "identified",
        "backdoor",
        {"design": "randomized"},
    ),
)


@dataclass
class IdCheck:
    id: str
    expected: tuple[str, str | None]
    got: tuple[str, str | None]
    passed: bool
    detail: str = ""


def check_identification(cases: tuple[IdCase, ...] = IDENTIFICATION_CASES) -> list[IdCheck]:
    out = []
    for c in cases:
        g = Graph.from_spec(parse_graph(c.graph))
        try:
            cert = identify(g, CausalQuery(treatment="X", outcome="Y", **c.query))
            got = (cert.status.value, cert.strategy.value if cert.strategy else None)
            detail = cert.estimand
        except (IdentificationError, ValueError) as exc:
            got, detail = ("error", None), str(exc)
        exp = (c.status, c.strategy)
        out.append(IdCheck(c.id, exp, got, got == exp, detail))
    return out


def random_admg(
    rng: np.random.Generator, k: int
) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]]]:
    nodes = [f"V{i}" for i in range(k)]
    directed = [
        (nodes[i], nodes[j]) for i in range(k) for j in range(i + 1, k) if rng.random() < 0.35
    ]
    bidirected = [
        (nodes[i], nodes[j]) for i in range(k) for j in range(i + 1, k) if rng.random() < 0.2
    ]
    return nodes, directed, bidirected


@dataclass
class CrossCheck:
    ran: bool
    graphs: int
    agree: int
    disagreements: list[str]
    reason: str = ""
    identified: int = 0  # class balance: a generator yielding one class checks nothing

    @property
    def rate(self) -> float:
        return self.agree / self.graphs if self.graphs else 0.0


def check_against_y0(n_graphs: int, kmin: int, kmax: int, seed: int) -> CrossCheck:
    try:
        from y0.algorithm.identify import identify_outcomes
        from y0.dsl import Variable
        from y0.graph import NxMixedGraph
    except ImportError:
        return CrossCheck(
            False, 0, 0, [], "y0 not installed (pip install 'causal-core[crosscheck]')"
        )
    rng = np.random.default_rng(seed)
    agree, bad, identified = 0, [], 0
    for i in range(n_graphs):
        k = int(rng.integers(kmin, kmax + 1))
        nodes, directed, bidirected = random_admg(rng, k)
        xi = int(rng.integers(0, k - 1))
        yi = int(rng.integers(xi + 1, k))
        x, y = nodes[xi], nodes[yi]
        ours = identify_id(ADMG.from_edges(nodes, directed, bidirected), {x}, {y}).identified
        g0 = NxMixedGraph.from_str_edges(nodes=nodes, directed=directed, undirected=bidirected)
        theirs = identify_outcomes(g0, treatments={Variable(x)}, outcomes={Variable(y)}) is not None
        identified += int(ours)
        if ours == theirs:
            agree += 1
        else:
            bad.append(
                f"#{i}: {x}->{y} directed={directed} bidirected={bidirected} "
                f"ours={ours} y0={theirs}"
            )
    return CrossCheck(True, n_graphs, agree, bad, identified=identified)


# ── estimation catalog ────────────────────────────────────────────────────────


def _z_squared(frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(frame["Z"].to_numpy() ** 2, dtype=float)


@dataclass(frozen=True)
class EstCase:
    id: str
    sim_graph: str
    coef: Mapping[tuple[str, str], float]
    analyst_graph: str | None = None  # defaults to sim_graph
    binary: frozenset[str] = frozenset()
    strategy: str = "backdoor"
    query: Mapping[str, Any] = field(default_factory=dict)
    intercept: Mapping[str, float] = field(default_factory=dict)
    extra: Mapping[str, Callable[[pd.DataFrame], np.ndarray]] = field(default_factory=dict)
    #: A custom data-generating process, for designs a linear SCM cannot express
    #: (e.g. effect heterogeneity). ``truth_value`` must then be given.
    sampler: Callable[[int, np.random.Generator], pd.DataFrame] | None = None
    truth_value: float | None = None

    def sample(self, n: int, rng: np.random.Generator) -> pd.DataFrame:
        return self.sampler(n, rng) if self.sampler else self.scm().sample(n, rng)

    def scm(self) -> LinearSCM:
        return LinearSCM(
            graph=parse_graph(self.sim_graph),
            coef=dict(self.coef),
            binary=self.binary,
            intercept=dict(self.intercept),
            extra=dict(self.extra),
        )

    @property
    def truth(self) -> float:
        if self.truth_value is not None:
            return self.truth_value
        return self.scm().total_effect("X", "Y")

    @property
    def graph_for_analysis(self) -> str:
        return self.analyst_graph or self.sim_graph


def _heterogeneous(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Z ~ N(0,1); X ~ Bernoulli(logistic(Z)); Y = X + 0.8 X Z + Z + N(0,1).

    The effect varies with Z and treated units have higher Z, so ATE = 1 while
    ATT = 1 + 0.8 E[Z | X=1].
    """
    z = rng.normal(size=n)
    x = (z + rng.logistic(size=n) > 0).astype(float)
    y = x + 0.8 * x * z + z + rng.normal(size=n)
    return pd.DataFrame({"Z": z, "X": x, "Y": y})


def _heterogeneous_att() -> float:
    """1 + 0.8 E[Z | X=1] = 1 + 0.8 * 2 * integral z logistic(z) phi(z) dz (quadrature)."""
    from scipy import integrate, special

    # phi(z) < 1e-300 beyond |z| = 40, so the finite range loses nothing.
    val, _err = integrate.quad(
        lambda z: z * special.expit(z) * stats.norm.pdf(z), -40, 40, epsabs=1e-13, limit=200
    )
    return float(1.0 + 0.8 * 2.0 * val)


_BACKDOOR = "Z1 -> X; Z1 -> Y; Z2 -> X; Z2 -> Y; X -> Y; W -> Y"
_BACKDOOR_COEF = {
    ("Z1", "X"): 0.6,
    ("Z1", "Y"): 1.0,
    ("Z2", "X"): -0.5,
    ("Z2", "Y"): 0.7,
    ("X", "Y"): 1.5,
    ("W", "Y"): 0.8,
}

ESTIMATION_CASES: tuple[EstCase, ...] = (
    EstCase("backdoor_binary", _BACKDOOR, _BACKDOOR_COEF, binary=frozenset({"X"})),
    EstCase("backdoor_continuous", _BACKDOOR, _BACKDOOR_COEF),
    EstCase(
        "randomized_binary",
        "X -> Y; W -> Y",
        {("X", "Y"): 0.4, ("W", "Y"): 1.0},
        binary=frozenset({"X"}),
        strategy="randomized",
        query={"design": "randomized"},
    ),
    EstCase(
        "m_bias_binary",
        "A -> M; B -> M; A -> X; B -> Y; X -> Y",
        {("A", "M"): 1.0, ("B", "M"): 1.0, ("A", "X"): 0.8, ("B", "Y"): 1.0, ("X", "Y"): 1.0},
        binary=frozenset({"X"}),
    ),
    EstCase(
        "butterfly_continuous",
        "A -> M; B -> M; A -> X; B -> Y; M -> X; M -> Y; X -> Y",
        {
            ("A", "M"): 0.7,
            ("B", "M"): 0.7,
            ("A", "X"): 0.5,
            ("B", "Y"): 0.5,
            ("M", "X"): 0.5,
            ("M", "Y"): 0.5,
            ("X", "Y"): 1.2,
        },
    ),
    EstCase(
        "mediated_continuous",
        "Z -> X; Z -> Y; X -> M -> Y; X -> Y",
        {("Z", "X"): 0.8, ("Z", "Y"): 1.0, ("X", "M"): 0.7, ("M", "Y"): 0.9, ("X", "Y"): 0.5},
    ),
    EstCase(
        "long_backdoor_continuous",
        "A -> B -> C -> X; A -> D -> Y; X -> Y",
        {
            ("A", "B"): 0.8,
            ("B", "C"): 0.8,
            ("C", "X"): 0.8,
            ("A", "D"): 0.8,
            ("D", "Y"): 0.8,
            ("X", "Y"): 1.0,
        },
    ),
    EstCase(
        "null_effect_binary",
        "Z -> X; Z -> Y; X -> Y",
        {("Z", "X"): 0.8, ("Z", "Y"): 1.0, ("X", "Y"): 0.0},
        binary=frozenset({"X"}),
    ),
    EstCase(
        "frontdoor_continuous",
        "latent U; U -> X; U -> Y; X -> M; M -> Y",
        {("U", "X"): 1.0, ("U", "Y"): 1.0, ("X", "M"): 0.8, ("M", "Y"): 1.1},
        analyst_graph="X -> M -> Y; X <-> Y",
        strategy="frontdoor",
    ),
    EstCase(
        "iv_continuous",
        "latent U; U -> X; U -> Y; Z -> X; X -> Y",
        {("U", "X"): 1.0, ("U", "Y"): 1.0, ("Z", "X"): 1.0, ("X", "Y"): 0.7},
        analyst_graph="Z -> X -> Y; X <-> Y",
        strategy="iv",
    ),
    EstCase(
        "iv_conditional_continuous",
        "latent U; U -> X; U -> Y; C -> Z; C -> Y; Z -> X; X -> Y",
        {
            ("U", "X"): 0.8,
            ("U", "Y"): 0.8,
            ("C", "Z"): 0.8,
            ("C", "Y"): 0.8,
            ("Z", "X"): 1.0,
            ("X", "Y"): -0.6,
        },
        analyst_graph="C -> Z -> X -> Y; C -> Y; X <-> Y",
        strategy="iv",
    ),
    EstCase(
        "proximal_continuous",
        "latent U; U -> X; U -> Y; U -> Zp; U -> Wp; X -> Y",
        {("U", "X"): 1.0, ("U", "Y"): 1.0, ("U", "Zp"): 1.0, ("U", "Wp"): 1.0, ("X", "Y"): 0.5},
        strategy="proximal",
        query={"treatment_proxies": ("Zp",), "outcome_proxies": ("Wp",)},
    ),
    # v3: effect heterogeneity, where the ATT and the ATE differ.
    EstCase(
        "heterogeneous_ate_binary",
        "Z -> X; Z -> Y; X -> Y",
        {},
        sampler=_heterogeneous,
        truth_value=1.0,
    ),
    EstCase(
        "heterogeneous_att_binary",
        "Z -> X; Z -> Y; X -> Y",
        {},
        sampler=_heterogeneous,
        truth_value=_heterogeneous_att(),
        query={"estimand": "att"},
    ),
)

#: Valid designs used for refuter false alarms. backdoor_binary gains a genuine
#: negative-control outcome N (caused by a confounder, not by X).
REFUTER_VALID_CASES: tuple[EstCase, ...] = (
    EstCase(
        "backdoor_binary_nco",
        _BACKDOOR + "; Z1 -> N",
        {**_BACKDOOR_COEF, ("Z1", "N"): 1.0},
        binary=frozenset({"X"}),
        query={"negative_control_outcomes": ("N",)},
    ),
    *(c for c in ESTIMATION_CASES if c.strategy in ("backdoor", "randomized")),
)

REFUTER_POWER_CASES: tuple[tuple[EstCase, str], ...] = (
    (
        EstCase(
            "unmeasured_confounder_nco",
            "latent U; U -> X; U -> Y; U -> N; Z -> X; Z -> Y; X -> Y",
            {
                ("U", "X"): 0.8,
                ("U", "Y"): 0.8,
                ("U", "N"): 0.8,
                ("Z", "X"): 0.5,
                ("Z", "Y"): 0.5,
                ("X", "Y"): 1.0,
            },
            analyst_graph="Z -> X; Z -> Y; X -> Y; N",
            binary=frozenset({"X"}),
            query={"negative_control_outcomes": ("N",)},
        ),
        "negative_control_outcome:N",
    ),
    (
        EstCase(
            "omitted_edge",
            "A -> X -> Y; B -> Y; A -> Y",
            {("A", "X"): 0.8, ("X", "Y"): 1.0, ("B", "Y"): 0.7, ("A", "Y"): 0.6},
            analyst_graph="A -> X -> Y; B -> Y",
        ),
        "implied_independences",
    ),
)

TRIANGULATION_VALID_CASES: tuple[EstCase, ...] = tuple(
    c
    for c in ESTIMATION_CASES
    if c.id
    in (
        "backdoor_binary",
        "m_bias_binary",
        "null_effect_binary",
        "randomized_binary",
        "heterogeneous_ate_binary",
        "heterogeneous_att_binary",
    )
)

TRIANGULATION_POWER_CASE = EstCase(
    "nonlinear_outcome",
    "Z -> X; Z -> Y; X -> Y",
    {("Z", "X"): 1.0, ("Z", "Y"): 1.0, ("X", "Y"): 1.0},
    binary=frozenset({"X"}),
    intercept={"Z": 0.5},
    extra={"Y": _z_squared},
)


# ── runners ───────────────────────────────────────────────────────────────────

FAST_POLICY: dict[str, Any] = {"refuters": (), "sensitivity": False}


def _one_rep(
    case: EstCase, n: int, seed: int, case_idx: int, rep: int, policy_kwargs: Mapping[str, Any]
) -> CausalResult | Any:
    rng = np.random.default_rng([seed, case_idx, rep])
    frame = case.sample(n, rng)
    policy = AnalysisPolicy(seed=rep, **policy_kwargs)
    return analyze(
        frame, case.graph_for_analysis, treatment="X", outcome="Y", policy=policy, **case.query
    )


@dataclass
class EstimationCaseResult:
    id: str
    strategy_expected: str
    strategies_seen: list[str]
    estimator: str
    truth: float
    reps: int
    non_results: int
    mean: float
    mcse: float
    bias: float
    bias_in_mcse: float
    coverage: float
    coverage_p: float
    first_failure: str = ""


def run_estimation_case(args: tuple[EstCase, int, int, int, int]) -> EstimationCaseResult:
    case, idx, n, reps, seed = args
    truth = case.truth
    points, covered, strategies = [], [], set()
    non_results, first_failure, estimator = 0, "", ""
    for r in range(reps):
        out = _one_rep(case, n, seed, idx, r, FAST_POLICY)
        if not isinstance(out, CausalResult):
            non_results += 1
            if not first_failure:
                why = getattr(out, "reason", getattr(out, "error_type", ""))
                first_failure = f"{out.kind}: {why} {getattr(out, 'detail', '')}"[:300]
            continue
        strategies.add(out.certificate.strategy.value if out.certificate.strategy else "none")
        estimator = out.primary.estimator
        points.append(out.primary.point)
        ci = out.primary.ci
        covered.append(ci is not None and ci.lo <= truth <= ci.hi)
    k = len(points)
    mean = float(np.mean(points)) if k else math.nan
    mcse = float(np.std(points, ddof=1) / math.sqrt(k)) if k > 1 else math.nan
    bias = mean - truth
    cov = float(np.mean(covered)) if k else 0.0
    cov_p = float(stats.binomtest(int(sum(covered)), k, 0.95).pvalue) if k else 0.0
    return EstimationCaseResult(
        id=case.id,
        strategy_expected=case.strategy,
        strategies_seen=sorted(strategies),
        estimator=estimator,
        truth=truth,
        reps=reps,
        non_results=non_results,
        mean=mean,
        mcse=mcse,
        bias=bias,
        bias_in_mcse=abs(bias) / mcse if mcse and mcse > 0 else math.inf,
        coverage=cov,
        coverage_p=cov_p,
        first_failure=first_failure,
    )


def _map(fn: Callable[[Any], Any], items: list[Any], parallel: bool) -> list[Any]:
    if not parallel or len(items) == 1:
        return [fn(i) for i in items]
    workers = min(len(items), os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


@dataclass
class EstimationReport:
    cases: list[EstimationCaseResult]
    pooled_coverage: float
    holm_rejected: list[str]
    band: tuple[float, float]
    outside_band: list[str]
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def check_estimation(
    cfg: Mapping[str, Any],
    reps: int | None = None,
    parallel: bool = True,
    cases: tuple[EstCase, ...] = ESTIMATION_CASES,
) -> EstimationReport:
    e = cfg["estimation"]
    reps = reps or e["reps"]
    results = _map(
        run_estimation_case,
        [(c, i, e["n"], reps, e["seed"]) for i, c in enumerate(cases)],
        parallel,
    )
    failures: list[str] = []
    for r in results:
        if r.non_results:
            failures.append(
                f"{r.id}: {r.non_results}/{r.reps} replications returned no result "
                f"({r.first_failure})"
            )
        if r.strategy_expected not in r.strategies_seen or len(r.strategies_seen) != 1:
            failures.append(f"{r.id}: strategy {r.strategies_seen}, expected {r.strategy_expected}")
        if not (r.bias_in_mcse <= e["bias_max_mcse"]):
            failures.append(
                f"{r.id}: bias {r.bias:+.4f} = {r.bias_in_mcse:.1f} MCSE (max {e['bias_max_mcse']})"
            )
    rejected = holm([r.coverage_p for r in results], e["coverage_family_alpha"])
    holm_rej = [r.id for r, rej in zip(results, rejected, strict=True) if rej]
    for rid in holm_rej:
        r = next(x for x in results if x.id == rid)
        failures.append(
            f"{rid}: coverage {r.coverage:.3f} (binomial p={r.coverage_p:.2g}, Holm-rejected)"
        )
    total = sum(r.reps - r.non_results for r in results)
    pooled = sum(r.coverage * (r.reps - r.non_results) for r in results) / total if total else 0.0
    if not (e["pooled_coverage_min"] <= pooled <= e["pooled_coverage_max"]):
        failures.append(
            f"pooled coverage {pooled:.4f} outside "
            f"[{e['pooled_coverage_min']}, {e['pooled_coverage_max']}]"
        )
    band = tuple(e["reported_cell_band"])
    outside = [r.id for r in results if not (band[0] <= r.coverage <= band[1])]
    return EstimationReport(results, pooled, holm_rej, (band[0], band[1]), outside, failures)


# ── refuters and triangulation ───────────────────────────────────────────────


def _refuter_rates(args: tuple[EstCase, int, int, int, int, str]) -> dict[str, list[bool]]:
    case, idx, n, reps, seed, _ = args
    fired: dict[str, list[bool]] = {}
    for r in range(reps):
        out = _one_rep(case, n, seed, idx, r, {"sensitivity": False})
        if not isinstance(out, CausalResult):
            fired.setdefault("__no_result__", []).append(True)
            continue
        for ref in out.refutations:
            key = ref.name.split(":")[0] if ref.name.startswith("negative_control") else ref.name
            fired.setdefault(key, []).append(not ref.passed)
            if ref.name != key:
                fired.setdefault(ref.name, []).append(not ref.passed)
    return fired


@dataclass
class RateReport:
    false_alarms: dict[str, float]
    power: dict[str, float]
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def check_refuters(
    cfg: Mapping[str, Any], reps: int | None = None, parallel: bool = True
) -> RateReport:
    c = cfg["refuters"]
    reps = reps or c["reps"]
    jobs = [(case, i, c["n"], reps, c["seed"], "") for i, case in enumerate(REFUTER_VALID_CASES)]
    valid = _map(_refuter_rates, jobs, parallel)
    pooled: dict[str, list[bool]] = {}
    for d in valid:
        for k, v in d.items():
            pooled.setdefault(k, []).extend(v)
    failures = []
    fa = {k: float(np.mean(v)) for k, v in pooled.items() if k != "__no_result__"}
    if "__no_result__" in pooled:
        failures.append(
            f"{len(pooled['__no_result__'])} valid-design replications returned no result"
        )
    for k in ("placebo_treatment", "negative_control_outcome", "implied_independences"):
        if k not in fa:
            failures.append(f"refuter {k} never ran on the valid designs")
        elif fa[k] > c["false_alarm_max"]:
            failures.append(f"{k}: false-alarm rate {fa[k]:.3f} > {c['false_alarm_max']}")
    power = {}
    pjobs = [
        (case, 100 + i, c["n"], reps, c["seed"], key)
        for i, (case, key) in enumerate(REFUTER_POWER_CASES)
    ]
    for (case, key), d in zip(
        REFUTER_POWER_CASES, _map(_refuter_rates, pjobs, parallel), strict=True
    ):
        rate = float(np.mean(d.get(key, [False])))
        power[f"{case.id}:{key}"] = rate
        if rate < c["power_min"]:
            failures.append(f"{case.id}: {key} power {rate:.3f} < {c['power_min']}")
    return RateReport(fa, power, failures)


def _triangulation_rate(args: tuple[EstCase, int, int, int, int]) -> list[bool]:
    case, idx, n, reps, seed = args
    fired = []
    for r in range(reps):
        out = _one_rep(case, n, seed, idx, r, FAST_POLICY)
        if isinstance(out, CausalResult) and out.triangulation is not None:
            fired.append(not out.triangulation.agree)
    return fired


def check_triangulation(
    cfg: Mapping[str, Any], reps: int | None = None, parallel: bool = True
) -> RateReport:
    c = cfg["triangulation"]
    reps = reps or c["reps"]
    jobs = [
        (case, 200 + i, c["n"], reps, c["seed"]) for i, case in enumerate(TRIANGULATION_VALID_CASES)
    ]
    res = _map(_triangulation_rate, jobs, parallel)
    failures = []
    fa = {
        case.id: float(np.mean(v)) if v else math.nan
        for case, v in zip(TRIANGULATION_VALID_CASES, res, strict=True)
    }
    pooled = [x for v in res for x in v]
    rate = float(np.mean(pooled)) if pooled else math.nan
    fa["pooled"] = rate
    if not pooled:
        failures.append("triangulation never ran on the valid designs")
    elif rate > c["false_alarm_max"]:
        failures.append(f"triangulation false-alarm rate {rate:.3f} > {c['false_alarm_max']}")
    power_reps = reps if reps != c["reps"] else c["power_reps"]
    gated, reported = _map(
        _triangulation_rate,
        [
            (TRIANGULATION_POWER_CASE, 299, c["power_n"], power_reps, c["seed"]),
            (TRIANGULATION_POWER_CASE, 298, c["n"], reps, c["seed"]),
        ],
        parallel,
    )
    power_rate = float(np.mean(gated)) if gated else 0.0
    if power_rate < c["power_min"]:
        failures.append(
            f"triangulation power at n={c['power_n']}: {power_rate:.3f} < {c['power_min']}"
        )
    power = {
        f"{TRIANGULATION_POWER_CASE.id}@n={c['power_n']} (gated)": power_rate,
        f"{TRIANGULATION_POWER_CASE.id}@n={c['n']} (reported)": float(np.mean(reported))
        if reported
        else 0.0,
    }
    return RateReport(fa, power, failures)


# ── the gate ──────────────────────────────────────────────────────────────────


def git_sha() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def run_gate(prereg: Path | None = None, parallel: bool = True) -> dict[str, Any]:
    cfg, prereg_hash = load_prereg(prereg)
    ident = check_identification()
    idc = cfg["identification"]
    cross = check_against_y0(
        idc["random_admgs"], idc["random_nodes_min"], idc["random_nodes_max"], idc["random_seed"]
    )
    est = check_estimation(cfg, parallel=parallel)
    ref = check_refuters(cfg, parallel=parallel)
    tri = check_triangulation(cfg, parallel=parallel)
    id_match = sum(c.passed for c in ident) / len(ident)
    sections = {
        "identification": {
            "passed": id_match >= idc["catalog_match_required"]
            and (not cross.ran or cross.rate >= idc["agreement_required"])
            and (not cross.ran or 0 < cross.identified < cross.graphs),
            "catalog_match": id_match,
            "mismatches": [asdict(c) for c in ident if not c.passed],
            "y0_crosscheck": {
                "ran": cross.ran,
                "graphs": cross.graphs,
                "agreement": cross.rate,
                "identified": cross.identified,
                "not_identified": cross.graphs - cross.identified,
                "disagreements": cross.disagreements[:10],
                "reason": cross.reason,
            },
        },
        "estimation": {
            "passed": est.passed,
            "failures": est.failures,
            "pooled_coverage": est.pooled_coverage,
            "reported_band": est.band,
            "outside_reported_band": est.outside_band,
            "cases": [asdict(c) for c in est.cases],
        },
        "refuters": {
            "passed": ref.passed,
            "failures": ref.failures,
            "false_alarms": ref.false_alarms,
            "power": ref.power,
        },
        "triangulation": {
            "passed": tri.passed,
            "failures": tri.failures,
            "false_alarms": tri.false_alarms,
            "power": tri.power,
        },
    }
    return {
        "suite": "T0",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        "passed": all(s["passed"] for s in sections.values()),
        "sections": sections,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the T0 correctness gate")
    ap.add_argument("--out", type=Path, help="write the JSON report here")
    ap.add_argument("--serial", action="store_true", help="no process pool")
    args = ap.parse_args(argv)
    report = run_gate(parallel=not args.serial)
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    summary = {k: v["passed"] for k, v in report["sections"].items()}
    print(json.dumps({"passed": report["passed"], **summary}), file=sys.stderr)
    for k, v in report["sections"].items():
        for f in v.get("failures", []) + [str(m) for m in v.get("mismatches", [])]:
            print(f"  {k}: {f}", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

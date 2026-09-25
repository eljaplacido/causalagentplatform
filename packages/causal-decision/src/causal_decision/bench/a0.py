"""
A0: the assumption-impact gate. Bars come from benchmarks/a0-assumption-impact/PRE-REGISTRATION.md.

A revision to a causal model is taken as the better belief, and ``impact`` must say
whether the estimate made under the old graph is still an estimate of the effect it
claimed. The oracle below shares no code with the kernel's d-separation or adjustment
criterion: it draws a linear-Gaussian structural model on the NEW graph and compares,
in exact population algebra, the regression coefficient the OLD adjustment set
produces with the true total effect. No sampling, so one disagreement is a failure.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from causal_core.contract import CausalGraph, CausalQuery, Edge, IdentificationStatus, Strategy
from causal_core.graph import Graph
from causal_core.identification import identify

import causal_decision.assumptions as am


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "a0-assumption-impact" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/a0-assumption-impact/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


# ── graph pairs ───────────────────────────────────────────────────────────────

EDITS = ("add_directed", "remove_directed", "reverse_directed", "add_bidirected",
         "remove_bidirected")


@dataclass(frozen=True)
class Case:
    old: CausalGraph
    new: CausalGraph
    query: CausalQuery
    edit: str


def _random_graph(rng: np.random.Generator) -> tuple[list[str], list[tuple[str, str]],
                                                    list[tuple[str, str]]]:
    k = int(rng.integers(5, 9))
    names = [f"v{i}" for i in range(k)]
    p = float(rng.uniform(0.3, 0.5))
    directed = [(names[i], names[j]) for i in range(k) for j in range(i + 1, k)
                if rng.random() < p]
    bidirected: list[tuple[str, str]] = []
    for _ in range(int(rng.integers(0, 3))):
        a, b = sorted(rng.choice(names, size=2, replace=False).tolist())
        if (a, b) not in bidirected:
            bidirected.append((a, b))
    return names, directed, bidirected


def _spec(names: list[str], directed: list[tuple[str, str]],
          bidirected: list[tuple[str, str]]) -> CausalGraph:
    edges = [Edge(source=a, target=b) for a, b in directed]
    edges += [Edge(source=a, target=b, kind="bidirected") for a, b in bidirected]
    return am.canonical(CausalGraph(nodes=tuple(names), edges=tuple(edges)))


def _edit(rng: np.random.Generator, names: list[str], directed: list[tuple[str, str]],
          bidirected: list[tuple[str, str]], kind: str
          ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]] | None:
    d, b = list(directed), list(bidirected)
    if kind == "add_directed":
        a, c = rng.choice(names, size=2, replace=False).tolist()
        if (a, c) in d or (c, a) in d:
            return None
        d.append((a, c))
    elif kind == "remove_directed":
        if not d:
            return None
        d.pop(int(rng.integers(len(d))))
    elif kind == "reverse_directed":
        if not d:
            return None
        i = int(rng.integers(len(d)))
        a, c = d[i]
        d[i] = (c, a)
    elif kind == "add_bidirected":
        a, c = sorted(rng.choice(names, size=2, replace=False).tolist())
        if (a, c) in b:
            return None
        b.append((a, c))
    else:
        if not b:
            return None
        b.pop(int(rng.integers(len(b))))
    if not nx.is_directed_acyclic_graph(nx.DiGraph(d)):
        return None
    return d, b


def generate(seed: int, n_pairs: int, max_tries: int = 200_000) -> tuple[list[Case], Counter[str]]:
    """Back-door cases, and a count of the old graphs that were not back-door (reported)."""
    rng = np.random.default_rng(seed)
    kept: list[Case] = []
    other: Counter[str] = Counter()
    tries = 0
    while len(kept) < n_pairs:
        tries += 1
        if tries > max_tries:
            raise RuntimeError(f"generated only {len(kept)} back-door cases in {max_tries} tries")
        names, directed, bidirected = _random_graph(rng)
        dag = nx.DiGraph(directed)
        dag.add_nodes_from(names)
        if rng.random() < 0.5:
            pairs = [(x, y) for x in names for y in nx.descendants(dag, x)]
            if not pairs:
                continue
            x, y = pairs[int(rng.integers(len(pairs)))]
        else:
            x, y = rng.choice(names, size=2, replace=False).tolist()
        kind = EDITS[int(rng.integers(len(EDITS)))]
        edited = _edit(rng, names, directed, bidirected, kind)
        if edited is None:
            continue
        old = _spec(names, directed, bidirected)
        new = _spec(names, *edited)
        if old == new:
            continue
        query = CausalQuery(treatment=x, outcome=y)
        try:
            cert = identify(Graph.from_spec(old), query)
        except ValueError:
            continue
        backdoor = cert.status is IdentificationStatus.identified and (
            cert.strategy is Strategy.backdoor
        )
        if not backdoor:
            other[str(cert.strategy or "not_identified")] += 1
            continue
        kept.append(Case(old=old, new=new, query=query, edit=kind))
    return kept, other


# ── the oracle: linear-Gaussian population algebra on the NEW graph ───────────


def old_estimate_valid(case: Case, adjustment: tuple[str, ...], rng: np.random.Generator,
                       draws: int, tol: float) -> bool:
    """Whether regressing Y on X and the OLD set recovers X's total effect, if NEW is true."""
    nodes = list(case.new.nodes)
    parents: dict[str, list[str]] = {n: [] for n in nodes}
    for e in case.new.edges:
        if e.kind == "directed":
            parents[e.target].append(e.source)
        else:
            u = f"__L[{e.source},{e.target}]"
            nodes.append(u)
            parents[u] = []
            parents[e.source].append(u)
            parents[e.target].append(u)
    idx = {n: i for i, n in enumerate(nodes)}
    x, y = idx[case.query.treatment], idx[case.query.outcome]
    s = [x] + [idx[z] for z in adjustment]
    verdicts = []
    for _ in range(draws):
        b = np.zeros((len(nodes), len(nodes)))
        for child, ps in parents.items():
            for p in ps:
                b[idx[child], idx[p]] = rng.uniform(0.5, 1.5) * rng.choice([-1.0, 1.0])
        omega = np.diag([1.0 if n.startswith("__L") else rng.uniform(0.5, 1.5) for n in nodes])
        m = np.linalg.inv(np.eye(len(nodes)) - b)
        sigma = m @ omega @ m.T
        total = m[y, x]
        beta = np.linalg.solve(sigma[np.ix_(s, s)], sigma[s, y])
        verdicts.append(abs(beta[0] - total) < tol)
    if len(set(verdicts)) != 1:
        raise RuntimeError(f"oracle draws disagree on {case}: a measure-zero coincidence, or a bug")
    return bool(verdicts[0])


# ── checks ────────────────────────────────────────────────────────────────────


@dataclass
class Agreement:
    n: int
    matched: int
    by_oracle: dict[str, int]
    unverified: int
    mismatches: list[str]


def check_agreement(cases: list[Case], cfg: dict[str, Any]) -> Agreement:
    c = cfg["cases"]
    rng = np.random.default_rng(c["seed"] + 1)
    matched = unverified = 0
    by_oracle: Counter[str] = Counter()
    mismatches: list[str] = []
    for case in cases:
        got = am.impact(case.old, case.new, case.query)
        valid = old_estimate_valid(case, got.old_adjustment_set, rng, c["oracle_draws"],
                                   c["oracle_tolerance"])
        by_oracle["valid" if valid else "invalid"] += 1
        if got.status is am.ImpactStatus.unverified:
            unverified += 1
        claims_valid = got.status is am.ImpactStatus.holds
        if claims_valid == valid and got.status is not am.ImpactStatus.unverified:
            matched += 1
        elif len(mismatches) < 20:
            mismatches.append(
                f"{case.edit} {case.query.treatment}->{case.query.outcome} "
                f"Z={got.old_adjustment_set}: impact={got.status.value}, oracle="
                f"{'valid' if valid else 'invalid'}"
            )
    return Agreement(len(cases), matched, dict(by_oracle), unverified, mismatches)


def check_properties(cases: list[Case]) -> list[str]:
    bad: list[str] = []
    for i, case in enumerate(cases):
        d = am.diff(case.old, case.new)
        try:
            round_trip = am.canonical(am.apply_diff(case.old, d)) == am.canonical(case.new)
        except ValueError as exc:  # the diff rebuilt a graph that is not one (e.g. a cycle)
            round_trip = False
            bad.append(f"P1 diff rebuilds an invalid graph on case {i} ({case.edit}): {exc}")
        if not round_trip:
            bad.append(f"P1 diff does not round-trip on case {i} ({case.edit})")
        removed = {am.edge_key(e) for e in case.old.edges} - {
            am.edge_key(e) for e in case.new.edges
        }
        if removed - {am.edge_key(e) for e in d.removed_edges}:
            bad.append(f"P4 removed edges not reported on case {i}: {sorted(removed)}")
        if am.impact(case.old, case.old, case.query).status is not am.ImpactStatus.holds:
            bad.append(f"P2 self-impact is not `holds` on case {i}")
        if am.impact(case.old, case.new, case.query).digest != am.impact(
            case.old, case.new, case.query
        ).digest:
            bad.append(f"P3 impact not deterministic on case {i}")
    return bad


def _agreement_fails(cases: list[Case], cfg: dict[str, Any]) -> bool:
    a = check_agreement(cases, cfg)
    return bool(a.matched / a.n < cfg["agreement"]["must_match"])


def _property_fails(cases: list[Case], tags: tuple[str, ...]) -> bool:
    return any(v.startswith(tags) for v in check_properties(cases))


#: Which check the pre-registration says must catch each defect.
CATCHER: dict[str, Callable[[list[Case], dict[str, Any]], bool]] = {
    "strategy_only": _agreement_fails,
    "new_set_valid": _agreement_fails,
    "ignore_bidirected": _agreement_fails,
    "diff_drops_removed": lambda cases, cfg: _property_fails(cases, ("P1", "P4")),
}


def check_defects(cases: list[Case], cfg: dict[str, Any]) -> dict[str, bool]:
    from causal_decision.bench.defects_a0 import DEFECTS

    caught = {}
    for name, defect in sorted(DEFECTS.items()):
        with defect():
            caught[name] = CATCHER[name](cases, cfg)
    return caught


def run(cfg: dict[str, Any]) -> dict[str, Any]:
    c = cfg["cases"]
    cases, other = generate(c["seed"], c["n_pairs"])
    agreement = check_agreement(cases, cfg)
    violations = check_properties(cases)
    caught = check_defects(cases, cfg)
    edits = Counter(case.edit for case in cases)

    failures: list[str] = []
    rate = agreement.matched / agreement.n
    if rate < cfg["agreement"]["must_match"]:
        failures.append(f"agreement {rate:.4f} < {cfg['agreement']['must_match']}")
        failures += agreement.mismatches
    if agreement.unverified > cfg["agreement"]["unverified_allowed"]:
        failures.append(f"{agreement.unverified} back-door cases came back `unverified`")
    for cls in ("valid", "invalid"):
        if agreement.by_oracle.get(cls, 0) < c["min_per_class"]:
            failures.append(
                f"only {agreement.by_oracle.get(cls, 0)} {cls} cases (< min_per_class "
                f"{c['min_per_class']}): the gate did not look at both"
            )
    if len(violations) > cfg["properties"]["violations_allowed"]:
        failures += violations[:20]
    required = set(cfg["seeded_defects"]["required"])
    if required != set(caught):
        failures.append(f"registered defects {sorted(required)} != implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "cases": agreement.n,
        "by_edit": dict(edits),
        "by_oracle": agreement.by_oracle,
        "agreement": rate,
        "unverified": agreement.unverified,
        "mismatches": agreement.mismatches,
        "non_backdoor_old_graphs_skipped": dict(other),
        "property_violations": violations,
        "seeded_defects_caught": caught,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    from causal_core.bench.t0 import git_sha

    ap = argparse.ArgumentParser(description="Run the pre-registered A0 assumption-impact gate")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {
        "suite": "A0",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        **run(cfg),
    }
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"][:10],
                      "by_oracle": doc["by_oracle"]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

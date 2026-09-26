"""
C0: the calibration-report gate. Bars come from benchmarks/c0-calibration/PRE-REGISTRATION.md.

SIMULATED. Every stream here is drawn from structural models whose effects are set
by construction; nothing this module produces is evidence about real operations.
What it establishes is that the report computed from follow-ups is honest about
what they can say: intervals that cover the truth at their level, no rate from too
few, undecidable follow-ups counted apart and carried into bounds, and "could not
resolve" kept apart from "no difference".

Part 1 is exact (no sampling): interval coverage over an (n, p) grid, the refusal
rule, and the paired comparison's behaviour for every discordant split. Part 2 runs
decisions through the real path -- ``causal_core.analyze`` -> ``decide`` -> ``plan``
-> ``analyze`` on a later window -> ``compare`` -- into a pool per (type x rolled
out) cell, then assembles streams from that pool and checks each report against
the truth the type mixture fixes.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binom

import causal_decision.outcome as oc
from causal_decision.contract import DecisionRequest

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"
TYPES = ("works", "null", "harms")
#: Later-window benefit per type, in units of the pre-decision benefit.
TYPE_SIGN = {"works": 1.0, "null": 0.0, "harms": -1.0}
OBSERVE_FROM = "2026-09-26T00:00:00+00:00"
ESTIMANDS = ("acted_worthwhile", "acted_harmful", "all_acted_bounds")


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "c0-calibration" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/c0-calibration/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


def _kw(cfg: dict[str, Any]) -> dict[str, Any]:
    r = cfg["report"]
    return {"level": r["level"], "min_followups": r["min_followups"]}


# ── Part 1: exact ─────────────────────────────────────────────────────────────


@dataclass
class ExactResult:
    min_coverage: float
    worst: tuple[int, float]
    refusal_violations: list[str]
    paired_violations: list[str]
    max_h0_difference: float


def check_intervals(cfg: dict[str, Any]) -> tuple[float, tuple[int, float]]:
    """Exact coverage of the report's interval over the registered (n, p) grid."""
    kw = _kw(cfg)
    ex = cfg["exact"]
    ps = np.arange(1, ex["p_grid"] + 1) / (ex["p_grid"] + 1)
    worst = (1.0, (0, 0.0))
    for n in range(kw["min_followups"], ex["n_max"] + 1):
        ks = np.arange(n + 1)
        cis = [oc.rate_estimate(int(k), n, **kw).ci for k in ks]
        lo = np.array([c[0] if c else np.nan for c in cis])
        hi = np.array([c[1] if c else np.nan for c in cis])
        pmf = binom.pmf(ks[None, :], n, ps[:, None])  # (p, k)
        inside = (lo[None, :] <= ps[:, None]) & (ps[:, None] <= hi[None, :])
        cov = (pmf * inside).sum(axis=1)
        i = int(np.argmin(cov))
        if cov[i] < worst[0]:
            worst = (float(cov[i]), (n, float(ps[i])))
    return worst[0], worst[1]


def check_refusal_exact(cfg: dict[str, Any]) -> list[str]:
    kw = _kw(cfg)
    out = []
    for n in range(0, cfg["exact"]["n_max"] + 1):
        for k in sorted({0, n // 2, n}):
            e = oc.rate_estimate(k, n, **kw)
            stated = e.rate is not None and e.ci is not None and e.refused is None
            refused = e.rate is None and e.ci is None and bool(e.refused)
            want = n >= kw["min_followups"]
            if (want and not stated) or (not want and not refused):
                out.append(f"rate_estimate({k}, {n}) stated={stated} refused={refused}")
    return out


def check_paired_exact(cfg: dict[str, Any]) -> tuple[list[str], float]:
    r = cfg["report"]
    m = r["min_discordant"]
    out = []
    if oc.min_discordant(r["alpha"]) != m:
        out.append(f"min_discordant({r['alpha']}) = {oc.min_discordant(r['alpha'])} != {m}")
    worst = 0.0
    for d in range(0, cfg["exact"]["n_max"] + 1):
        n_pairs = max(d, r["min_followups"])
        p_diff = 0.0
        for b in range(d + 1):
            got = oc.paired_comparison(b, d - b, n_pairs, alpha=r["alpha"],
                                       min_followups=r["min_followups"])
            if (got.status == "could_not_resolve") != (d < m):
                out.append(f"b={b} c={d - b}: status {got.status} with {d} discordant (m={m})")
            if d < m and got.status == "no_difference_detected":
                out.append(f"b={b} c={d - b}: no_difference_detected below min_discordant")
            if got.status == "difference":
                p_diff += float(binom.pmf(b, d, 0.5))
        worst = max(worst, p_diff)
        if p_diff > r["alpha"] + cfg["exact"]["h0_tolerance"]:
            out.append(f"d={d}: P(difference | H0) = {p_diff:.4f} > alpha")
    return out, worst


def run_exact(cfg: dict[str, Any]) -> ExactResult:
    mn, worst = check_intervals(cfg)
    paired, h0 = check_paired_exact(cfg)
    return ExactResult(mn, worst, check_refusal_exact(cfg), paired, h0)


# ── Part 2: the pool, through the real path ───────────────────────────────────


def incidents(benefit: float, n: int, rng: np.random.Generator,
              rolled_out: bool = False) -> pd.DataFrame:
    """The PRE-REGISTRATION's structural model. `rolled_out`: every row treated."""
    load = rng.normal(0, 1, n)
    if rolled_out:
        playbook = np.ones(n)
    else:
        playbook = rng.binomial(1, 1 / (1 + np.exp(-0.9 * load))).astype(float)
    minutes = 60 + 8.0 * load - benefit * playbook + rng.normal(0, 10, n)
    return pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})


@dataclass(frozen=True)
class Member:
    type: str
    rolled: bool
    realization: oc.Realization


def build_pool(cfg: dict[str, Any]) -> tuple[dict[tuple[str, bool], list[Member]], list[str]]:
    """Run `per_cell` decisions through the whole path for each cell; verify each one's flags."""
    import causal_core as cc
    from causal_core.contract import CausalResult

    from causal_decision.policy import decide

    p = cfg["pool"]
    rng = np.random.default_rng(p["seed"])
    request = DecisionRequest(
        action="apply remediation playbook X",
        outcome="minutes",
        direction="decrease",
        minimum_worthwhile_effect=p["minimum_worthwhile_effect"],
        harm_threshold=p["harm_threshold"],
    )
    pool: dict[tuple[str, bool], list[Member]] = {}
    violations: list[str] = []
    for t in TYPES:
        for rolled in (False, True):
            cell: list[Member] = []
            for i in range(p["per_cell"]):
                pre = cc.analyze(incidents(p["benefit"], p["n_rows"], rng), GRAPH,
                                 treatment="playbook", outcome="minutes")
                if not isinstance(pre, CausalResult):
                    violations.append(f"{t}/{rolled}/{i}: pre-decision analysis did not estimate")
                    continue
                d = decide(pre, request)
                fu = oc.plan(d, pre, graph=GRAPH, observe_from=OBSERVE_FROM)
                later_df = incidents(TYPE_SIGN[t] * p["benefit"], p["n_rows"], rng, rolled)
                later = cc.analyze(later_df, GRAPH, query=fu.query)
                r = oc.compare(fu, later)
                why = _type_violation(t, rolled, r)
                if why:
                    violations.append(f"{t}/{'rolled' if rolled else 'mixed'}/{i}: {why}")
                cell.append(Member(t, rolled, r))
            pool[(t, rolled)] = cell
    return pool, violations


def _type_violation(t: str, rolled: bool, r: oc.Realization) -> str | None:
    """The gated flags must be a function of the cell; this is where that is checked."""
    if rolled:
        return None if not r.decidable else "a full rollout came back decidable"
    if not r.decidable:
        return f"undecidable without a rollout ({r.undecidable_reason})"
    want = {"works": (True, False), "null": (False, False), "harms": (False, True)}[t]
    got = (r.cleared_worthwhile, r.realized_harm)
    return None if got == want else f"flags (cleared, harm) = {got}, type implies {want}"


# ── Part 2: streams ───────────────────────────────────────────────────────────


def truths(conf: dict[str, Any]) -> dict[str, float]:
    share, roll = conf["share"], conf["rollout"]
    dec = sum(share[t] * (1 - roll[t]) for t in TYPES)
    return {
        "acted_worthwhile": share["works"] * (1 - roll["works"]) / dec,
        "acted_harmful": share["harms"] * (1 - roll["harms"]) / dec,
        "all_acted_bounds": share["works"],
    }


@dataclass
class ConfigResult:
    name: str
    reps: int
    truth: dict[str, float]
    stated: dict[str, int]
    covered: dict[str, int]
    refused: dict[str, int]
    paired_status: dict[str, int]
    accounting: list[str]
    refusal: list[str]
    paired: list[str]

    def coverage(self, est: str) -> float | None:
        return self.covered[est] / self.stated[est] if self.stated[est] else None


def _draw_streams(
    cfg: dict[str, Any], pool: dict[tuple[str, bool], list[Member]]
) -> Iterator[tuple[dict[str, Any], list[list[Member]]]]:
    s = cfg["streams"]
    rng = np.random.default_rng(s["seed"])
    per = cfg["pool"]["per_cell"]
    for conf in s["config"]:
        probs = np.array([conf["share"][t] for t in TYPES], dtype=float)
        roll = np.array([conf["rollout"][t] for t in TYPES], dtype=float)
        streams = []
        for _ in range(s["reps"]):
            ti = rng.choice(len(TYPES), size=conf["k"], p=probs / probs.sum())
            rolled = rng.random(conf["k"]) < roll[ti]
            pick = rng.integers(0, per, size=conf["k"])
            streams.append([
                pool[(TYPES[int(a)], bool(b))][int(c) % len(pool[(TYPES[int(a)], bool(b))])]
                for a, b, c in zip(ti, rolled, pick, strict=True)
            ])
        yield conf, streams


def _expected(members: list[Member]) -> dict[str, Any]:
    """What the report must say, from what the gate simulated -- not from the report."""
    acted = [m for m in members if m.realization.verdict in oc.ACTED]
    acted_dec = [m for m in acted if not m.rolled]
    dec = [m for m in members if not m.rolled]
    pairs = [m for m in dec if m.realization.claimed_worthwhile is not None]
    return {
        "n_decisions": len(members),
        "n_acted": len(acted),
        "n_decidable": len(dec),
        "n_undecidable": len(members) - len(dec),
        "by_reason": dict(Counter(m.realization.undecidable_reason or "UNKNOWN"
                                  for m in members if m.rolled)),
        "acted_worthwhile": (sum(m.type == "works" for m in acted_dec), len(acted_dec)),
        "acted_harmful": (sum(m.type == "harms" for m in acted_dec), len(acted_dec)),
        "bounds": (sum(m.type == "works" for m in acted_dec), len(acted) - len(acted_dec),
                   len(acted)),
        "point_in_interval": (
            sum(bool(m.realization.point_in_predicted_ci) for m in dec
                if m.realization.point_in_predicted_ci is not None),
            sum(m.realization.point_in_predicted_ci is not None for m in dec),
        ),
        "overlap": (
            sum(bool(m.realization.intervals_overlap) for m in dec
                if m.realization.intervals_overlap is not None),
            sum(m.realization.intervals_overlap is not None for m in dec),
        ),
        "paired": (
            sum(bool(m.realization.claimed_worthwhile) and m.type != "works" for m in pairs),
            sum((not m.realization.claimed_worthwhile) and m.type == "works" for m in pairs),
            len(pairs),
        ),
    }


def check_streams(cfg: dict[str, Any], pool: dict[tuple[str, bool], list[Member]]
                  ) -> list[ConfigResult]:
    kw = _kw(cfg)
    minimum = kw["min_followups"]
    m_disc = cfg["report"]["min_discordant"]
    results = []
    for conf, streams in _draw_streams(cfg, pool):
        name = conf["name"]
        truth = truths(conf)
        stated = dict.fromkeys(ESTIMANDS, 0)
        covered = dict.fromkeys(ESTIMANDS, 0)
        refused = dict.fromkeys(ESTIMANDS, 0)
        status: Counter[str] = Counter()
        accounting: list[str] = []
        refusal: list[str] = []
        paired: list[str] = []
        for rep, members in enumerate(streams):
            rs = [m.realization.model_copy(update={"decision_digest": f"c0:{name}:{rep}:{j}"})
                  for j, m in enumerate(members)]
            got = oc.report(rs, level=kw["level"], min_followups=minimum,
                            alpha=cfg["report"]["alpha"])
            want = _expected(members)
            tag = f"{name}#{rep}"

            # accounting
            for field in ("n_decisions", "n_acted", "n_decidable", "n_undecidable"):
                if getattr(got, field) != want[field]:
                    accounting.append(f"{tag}: {field} {getattr(got, field)} != {want[field]}")
            if got.undecidable_by_reason != want["by_reason"]:
                accounting.append(f"{tag}: by reason {got.undecidable_by_reason} != "
                                  f"{want['by_reason']}")
            rates = {
                "acted_worthwhile": got.acted_and_worthwhile_rate,
                "acted_harmful": got.acted_and_harmful_rate,
                "point_in_interval": got.point_in_interval_rate,
                "overlap": got.interval_overlap_rate,
            }
            for key, est in rates.items():
                if (est.successes, est.denominator) != want[key]:
                    accounting.append(f"{tag}: {key} {est.successes}/{est.denominator} != "
                                      f"{want[key][0]}/{want[key][1]}")
                ok = (est.rate is not None and est.ci is not None) == (est.denominator >= minimum)
                if not ok:
                    refusal.append(f"{tag}: {key} stated={est.rate is not None} at "
                                   f"n={est.denominator}")
            b = got.acted_worthwhile_bounds
            if (b.successes, b.unresolved, b.denominator) != want["bounds"]:
                accounting.append(f"{tag}: bounds {b.successes}+{b.unresolved}/{b.denominator} "
                                  f"!= {want['bounds']}")
            if (b.ci is not None) != (b.denominator >= minimum):
                refusal.append(f"{tag}: bounds stated={b.ci is not None} at n={b.denominator}")
            pc = got.worthwhile_claimed_vs_confirmed
            if (pc.claimed_not_confirmed, pc.confirmed_not_claimed, pc.n_pairs) != want["paired"]:
                accounting.append(f"{tag}: paired {pc.claimed_not_confirmed}/"
                                  f"{pc.confirmed_not_claimed}/{pc.n_pairs} != {want['paired']}")

            # coverage, over streams where the figure was stated
            for key, est_key in (("acted_worthwhile", "acted_worthwhile"),
                                 ("acted_harmful", "acted_harmful")):
                est = rates[est_key]
                if est.ci is None:
                    refused[key] += 1
                    continue
                stated[key] += 1
                covered[key] += est.ci[0] <= truth[key] <= est.ci[1]
            if b.ci is None:
                refused["all_acted_bounds"] += 1
            else:
                stated["all_acted_bounds"] += 1
                covered["all_acted_bounds"] += b.ci[0] <= truth["all_acted_bounds"] <= b.ci[1]

            # paired: unresolvable iff too few pairs or discordant; never "no difference"
            status[pc.status] += 1
            d = pc.claimed_not_confirmed + pc.confirmed_not_claimed
            should_skip = pc.n_pairs < minimum or d < m_disc
            if (pc.status == "could_not_resolve") != should_skip:
                paired.append(f"{tag}: {pc.status} with {pc.n_pairs} pairs, {d} discordant")
            if pc.status == "no_difference_detected":
                paired.append(f"{tag}: no_difference_detected where the truth is over-claiming")
        results.append(ConfigResult(name, len(streams), truth, stated, covered, refused,
                                    dict(status), accounting, refusal, paired))
    return results


# ── the gate ──────────────────────────────────────────────────────────────────
#
# A failure is (kind, message). The kinds are the checks the pre-registration names,
# so a seeded defect's catcher is the set of kinds that must appear under it.


Failure = tuple[str, str]


def stream_failures(cfg: dict[str, Any], results: list[ConfigResult]) -> list[Failure]:
    s = cfg["streams"]
    out: list[Failure] = []
    by = {r.name: r for r in results}
    for name in s["coverage_configs"]:
        r = by[name]
        for est in ESTIMANDS:
            if r.stated[est] < s["min_stated_streams"]:
                out.append(("floor", f"{name}: {est} stated in only {r.stated[est]} streams "
                                     f"(< {s['min_stated_streams']})"))
                continue
            cov = r.coverage(est)
            if cov is not None and cov < s["min_coverage"]:
                out.append((f"coverage:{est}", f"{name}: {est} coverage {cov:.4f} < "
                                               f"{s['min_coverage']} (truth {r.truth[est]:.4f})"))
    small = by["small"]
    for branch, count in (("refused", small.refused["acted_worthwhile"]),
                          ("stated", small.stated["acted_worthwhile"])):
        if count < s["min_refused_streams"]:
            out.append(("floor", f"small: acted_worthwhile {branch} in only {count} streams "
                                 f"(< {s['min_refused_streams']}): a branch went unlooked-at"))
    for kind, allowed in (("accounting", cfg["accounting"]["violations_allowed"]),
                          ("refusal", cfg["refusal"]["violations_allowed"]),
                          ("paired", cfg["paired"]["violations_allowed"])):
        found = [v for r in results for v in getattr(r, kind)]
        if len(found) > allowed:
            out.append((kind, f"{kind}: {len(found)} violation(s), e.g. {found[0]}"))
    return out


def exact_failures(cfg: dict[str, Any], ex: ExactResult) -> list[Failure]:
    out: list[Failure] = []
    level, tol = cfg["report"]["level"], cfg["exact"]["coverage_tolerance"]
    if ex.min_coverage < level - tol:
        out.append(("exact_coverage", f"exact interval coverage {ex.min_coverage:.4f} < {level} "
                                      f"at (n, p) = {ex.worst}"))
    if ex.refusal_violations:
        out.append(("exact_refusal", f"exact refusal: {len(ex.refusal_violations)} "
                                     f"violation(s), e.g. {ex.refusal_violations[0]}"))
    if ex.paired_violations:
        out.append(("exact_paired", f"exact paired: {len(ex.paired_violations)} violation(s), "
                                    f"e.g. {ex.paired_violations[0]}"))
    return out


def all_failures(cfg: dict[str, Any], pool: dict[tuple[str, bool], list[Member]]
                 ) -> list[Failure]:
    return exact_failures(cfg, run_exact(cfg)) + stream_failures(cfg, check_streams(cfg, pool))


#: The checks the pre-registration's defect table says must catch each seeded defect:
#: every kind listed must appear among the gate's failures under that defect.
CATCHER: dict[str, frozenset[str]] = {
    "wald_interval": frozenset({"exact_coverage", "coverage:acted_worthwhile"}),
    "undecidable_as_agreement": frozenset({"accounting", "coverage:acted_worthwhile"}),
    "drop_undecidable": frozenset({"accounting", "coverage:all_acted_bounds"}),
    "no_minimum": frozenset({"exact_refusal", "refusal"}),
    "unresolved_reads_as_no_difference": frozenset({"exact_paired", "paired"}),
}


def check_defects(cfg: dict[str, Any], pool: dict[tuple[str, bool], list[Member]]
                  ) -> dict[str, list[str]]:
    """Per defect, the kinds of failure the gate reported under it."""
    from causal_decision.bench.defects_c0 import DEFECTS

    out: dict[str, list[str]] = {}
    for name, defect in sorted(DEFECTS.items()):
        with defect():
            out[name] = sorted({kind for kind, _ in all_failures(cfg, pool)})
    return out


def caught(name: str, tripped: list[str]) -> bool:
    return CATCHER[name] <= set(tripped)


def run(cfg: dict[str, Any]) -> dict[str, Any]:
    ex = run_exact(cfg)
    pool, violations = build_pool(cfg)
    results = check_streams(cfg, pool)
    defects = check_defects(cfg, pool)

    failures = [msg for _, msg in exact_failures(cfg, ex) + stream_failures(cfg, results)]
    if len(violations) > cfg["pool"]["type_violations_allowed"]:
        failures.append(f"pool: {len(violations)} type violation(s), e.g. {violations[0]}")
    required = set(cfg["seeded_defects"]["required"])
    if not required == set(defects) == set(CATCHER):
        failures.append(f"registered defects {sorted(required)} != implemented {sorted(defects)}")
    failures += [f"seeded defect {n} went undetected (needs {sorted(CATCHER[n])}, tripped "
                 f"{tripped})" for n, tripped in defects.items() if not caught(n, tripped)]
    verdicts = Counter(str(m.realization.verdict) for cell in pool.values() for m in cell)
    return {
        "simulated": True,
        "what_this_is_not": "a calibration figure about real operations -- every stream is "
                            "simulated from structural models with effects set by construction",
        "exact": {
            "min_interval_coverage": ex.min_coverage,
            "worst_at_n_p": list(ex.worst),
            "refusal_violations": ex.refusal_violations[:20],
            "paired_violations": ex.paired_violations[:20],
            "max_h0_difference_rate": ex.max_h0_difference,
        },
        "pool": {
            "cells": {f"{t}/{'rolled' if r else 'mixed'}": len(v) for (t, r), v in pool.items()},
            "verdicts": dict(sorted(verdicts.items())),
            "undecidable_reasons": dict(Counter(
                m.realization.undecidable_reason for (_, r), v in pool.items() if r for m in v)),
            "type_violations": violations,
        },
        "configs": {
            r.name: {
                "reps": r.reps,
                "truth": r.truth,
                "stated": r.stated,
                "refused": r.refused,
                "coverage": {e: r.coverage(e) for e in ESTIMANDS},
                "paired_status": r.paired_status,
                "accounting_violations": r.accounting[:10],
                "refusal_violations": r.refusal[:10],
                "paired_violations": r.paired[:10],
            }
            for r in results
        },
        "seeded_defects_caught": {n: caught(n, t) for n, t in defects.items()},
        "seeded_defects_tripped": defects,
        "failures": failures,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    from causal_core.bench.t0 import git_sha

    ap = argparse.ArgumentParser(description="Run the pre-registered C0 calibration-report gate")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {
        "suite": "C0",
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
                      "defects": doc["seeded_defects_caught"]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

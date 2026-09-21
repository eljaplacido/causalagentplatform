"""
D0: the decision-policy gate. Bars come from benchmarks/d0-decision-policy/PRE-REGISTRATION.md.

A scenario fixes an evidence state and a request and names the verdict the policy must
return. A property must hold over a seeded sweep of requests and policies. Nothing here is
estimated, so one counterexample is a failure.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import causal_core as cc
import numpy as np
import pandas as pd
from causal_core.contract import (
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    ApparatusFailure,
    Provenance,
)

from causal_decision.contract import (
    SEVERITY,
    CausalOutcome,
    Decision,
    DecisionPolicy,
    DecisionRequest,
    RiskClass,
    Verdict,
)
from causal_decision.policy import decide
from causal_decision.render import explain


def prereg_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "benchmarks" / "d0-decision-policy" / "PRE-REGISTRATION.md"
        if cand.exists():
            return cand
    raise FileNotFoundError("benchmarks/d0-decision-policy/PRE-REGISTRATION.md not found")


def load_prereg() -> tuple[dict[str, Any], str]:
    raw = prereg_path().read_bytes()
    m = re.search(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if not m:
        raise ValueError("pre-registration has no ```toml block")
    return tomllib.loads(m.group(1)), "sha256:" + hashlib.sha256(raw).hexdigest()


# ── evidence states ───────────────────────────────────────────────────────────

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"
FAST = AnalysisPolicy(seed=0)


def _ops_data(
    n: int = 4000, effect: float = -9.0, seed: int = 0, noise: float = 8.0
) -> pd.DataFrame:
    """An operations case: a remediation playbook, confounded by system load."""
    rng = np.random.default_rng(seed)
    load = rng.normal(size=n)
    playbook = (0.9 * load + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 + effect * playbook + 12 * load + rng.normal(0, noise, size=n)
    return pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})


def strong_effect() -> CausalOutcome:
    return cc.analyze(_ops_data(), GRAPH, treatment="playbook", outcome="minutes",
                      estimand="att", policy=FAST)


def weak_effect() -> CausalOutcome:
    """A real but small effect, estimated imprecisely: the uncertain middle."""
    return cc.analyze(_ops_data(n=600, effect=-6.0, noise=30.0), GRAPH, treatment="playbook",
                      outcome="minutes", estimand="att", policy=FAST)


def provisional_effect() -> CausalOutcome:
    graph = "load -> playbook; load -> minutes [origin=llm_prior]; playbook -> minutes"
    return cc.analyze(_ops_data(), graph, treatment="playbook", outcome="minutes",
                      estimand="att", policy=FAST)


def contested_effect() -> CausalOutcome:
    """An unmeasured confounder also drives a declared negative control, so a refuter fires."""
    rng = np.random.default_rng(3)
    n = 3000
    hidden = rng.normal(size=n)
    load = rng.normal(size=n)
    playbook = (0.9 * load + 0.8 * hidden + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 - 9.0 * playbook + 12 * load + 10 * hidden + rng.normal(0, 8, size=n)
    backlog = 5 * hidden + rng.normal(size=n)  # cannot be caused by the playbook
    frame = pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes,
                          "backlog": backlog})
    return cc.analyze(frame, GRAPH + "; load -> backlog", treatment="playbook", outcome="minutes",
                      estimand="att", negative_control_outcomes=("backlog",), policy=FAST)


def not_identified() -> CausalOutcome:
    frame = _ops_data()
    return cc.analyze(frame, "playbook -> minutes; playbook <-> minutes", treatment="playbook",
                      outcome="minutes", policy=FAST)


def positivity_refusal() -> CausalOutcome:
    rng = np.random.default_rng(1)
    n = 2000
    load = rng.normal(size=n)
    playbook = (load > 0).astype(float)  # load decides it outright
    minutes = 60 - 9 * playbook + 12 * load + rng.normal(0, 8, size=n)
    return cc.analyze(pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes}),
                      GRAPH, treatment="playbook", outcome="minutes", policy=FAST)


def invalid_question() -> CausalOutcome:
    return cc.analyze(_ops_data(), GRAPH, treatment="playbook", outcome="minutes",
                      rung="counterfactual", policy=FAST)


def iv_effect() -> CausalOutcome:
    """Identified only through an instrument: tier B, parametric assumptions."""
    rng = np.random.default_rng(5)
    n = 4000
    hidden = rng.normal(size=n)
    nudge = rng.normal(size=n)
    playbook = (1.2 * nudge + hidden + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 - 9.0 * playbook + 10 * hidden + rng.normal(0, 8, size=n)
    frame = pd.DataFrame({"nudge": nudge, "playbook": playbook, "minutes": minutes})
    return cc.analyze(frame, "nudge -> playbook -> minutes; playbook <-> minutes",
                      treatment="playbook", outcome="minutes", policy=FAST)


def apparatus_failure() -> CausalOutcome:
    return ApparatusFailure(stage="estimate", error_type="NumericalInstabilityError",
                            detail="constructed for the gate",
                            provenance=Provenance(contract_version=cc.CONTRACT_VERSION,
                                                  core_version=cc.__version__))


def unapproved_refusal() -> CausalOutcome:
    return Abstention(reason=AbstentionReason.UNAPPROVED_ASSUMPTION,
                      detail="the graph contains edges nobody has reviewed",
                      provenance=Provenance(contract_version=cc.CONTRACT_VERSION,
                                            core_version=cc.__version__))


BASE = DecisionRequest(
    action="apply remediation playbook X",
    outcome="minutes",
    direction="decrease",
    risk_class=RiskClass.medium,
    reversible=True,
    minimum_worthwhile_effect=5.0,
    randomization_unit="incident",
    units_per_day=40,
)


# ── scenario table ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    id: str
    evidence: Callable[[], CausalOutcome]
    request: DecisionRequest
    expect: Verdict
    why: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("strong_effect_low_risk", strong_effect, BASE.model_copy(
        update={"risk_class": RiskClass.low}), Verdict.act,
        "a precise, supported, robust effect well past the bar, on a reversible low-risk action"),
    Scenario("strong_effect_medium_risk", strong_effect, BASE, Verdict.act,
        "the same evidence clears the stricter medium-risk rule"),
    Scenario("strong_effect_high_risk", strong_effect, BASE.model_copy(
        update={"risk_class": RiskClass.high}), Verdict.act_with_approval,
        "high risk always goes to a person, however good the evidence"),
    Scenario("strong_effect_irreversible", strong_effect, BASE.model_copy(
        update={"reversible": False}), Verdict.act_with_approval,
        "an irreversible action is never automated"),
    Scenario("effect_below_bar", strong_effect, BASE.model_copy(
        update={"minimum_worthwhile_effect": 20.0}), Verdict.refuse,
        "the whole interval lies below what would make acting worthwhile: do nothing"),
    Scenario("uncertain_worth", weak_effect, BASE, Verdict.experiment,
        "the interval straddles the bar: the evidence cannot say whether it is worth acting"),
    Scenario("contested_result", contested_effect, BASE, Verdict.experiment,
        "a negative control fired: the design is contested, so test it rather than trust it"),
    Scenario("provisional_graph", provisional_effect, BASE, Verdict.act_with_approval,
        "an unreviewed LLM-proposed edge means a person reviews before acting"),
    Scenario("identified_only_by_iv", iv_effect, BASE, Verdict.act_with_approval,
        "tier B: identification rests on assumptions the graph cannot state"),
    Scenario("not_identified_can_test", not_identified, BASE, Verdict.experiment,
        "nothing in the data identifies the effect, but the action can be randomized"),
    Scenario("not_identified_cannot_test", not_identified, BASE.model_copy(
        update={"can_randomize": False}), Verdict.refuse,
        "no identification and no experiment: there is nothing to act on"),
    Scenario("no_overlap_can_test", positivity_refusal, BASE, Verdict.experiment,
        "load decides the playbook outright, so only an experiment separates them"),
    Scenario("broken_question", invalid_question, BASE, Verdict.refuse,
        "a counterfactual question this kernel does not answer: fix the question"),
    Scenario("unreviewed_assumptions", unapproved_refusal, BASE, Verdict.refuse,
        "the analysis refused for a reason no experiment resolves"),
    Scenario("analysis_failed", apparatus_failure, BASE, Verdict.refuse,
        "the apparatus failed, so there is no evidence either way"),
)


@dataclass
class TableResult:
    id: str
    expected: str
    got: str
    passed: bool
    why: str


def check_table(scenarios: tuple[Scenario, ...] = SCENARIOS) -> list[TableResult]:
    out = []
    for s in scenarios:
        d = decide(s.evidence(), s.request)
        out.append(TableResult(s.id, s.expect.value, d.verdict.value,
                               d.verdict is s.expect, s.why))
    return out


# ── properties ────────────────────────────────────────────────────────────────


def _sweep(seed: int, n: int) -> list[tuple[CausalOutcome, DecisionRequest, DecisionPolicy]]:
    rng = np.random.default_rng(seed)
    states = [strong_effect(), weak_effect(), contested_effect(), provisional_effect(),
              iv_effect(), not_identified(), positivity_refusal(), apparatus_failure()]
    cases = []
    for _ in range(n):
        out = states[int(rng.integers(len(states)))]
        req = BASE.model_copy(update={
            "risk_class": [RiskClass.low, RiskClass.medium, RiskClass.high][int(rng.integers(3))],
            "reversible": bool(rng.integers(2)),
            "minimum_worthwhile_effect": float(rng.choice([0.0, 2.0, 5.0, 9.0, 20.0])),
            "can_randomize": bool(rng.integers(2)),
            "direction": "decrease",
        })
        pol = DecisionPolicy(plausible_confounding_rr=float(rng.choice([1.0, 1.5, 2.5])),
                             experiments_available=bool(rng.integers(2)))
        cases.append((out, req, pol))
    return cases


def _severity(d: Decision) -> int:
    return SEVERITY[d.verdict]


def check_properties(sweep: int, seed: int) -> list[str]:
    """Returns a list of violations; empty means every property held."""
    bad: list[str] = []
    cases = _sweep(seed, sweep)
    for out, req, pol in cases:
        d = decide(out, req, pol)

        if not req.reversible and d.verdict is Verdict.act:
            bad.append(f"P1 irreversible automated: {req.risk_class.value}/{d.evidence.kind}")

        higher = {RiskClass.low: RiskClass.medium, RiskClass.medium: RiskClass.high,
                  RiskClass.high: RiskClass.high}[req.risk_class]
        raised = decide(out, req.model_copy(update={"risk_class": higher}), pol)
        if _severity(raised) < _severity(d):
            bad.append(f"P2 raising risk raised autonomy: {req.risk_class.value} -> {higher.value}")

        stricter = req.model_copy(
            update={"minimum_worthwhile_effect": req.minimum_worthwhile_effect + 3.0})
        if _severity(decide(out, stricter, pol)) < _severity(d):
            bad.append("P3 raising the worthwhile bar raised autonomy")

        if d.evidence.kind != "result" and d.verdict in (Verdict.act, Verdict.act_with_approval):
            bad.append(f"P5 acted without an estimate: {d.evidence.kind} -> {d.verdict.value}")

        if d.evidence.status == "contested" and d.verdict is Verdict.act:
            bad.append("P6 contested evidence automated")

        if decide(out, req, pol).decision_digest != d.decision_digest:
            bad.append("P7 not deterministic")

        try:
            explain(d, out)
        except Exception as exc:
            bad.append(f"P8 unexplainable decision: {type(exc).__name__}: {exc}")
    return bad


def check_evidence_monotonicity() -> list[str]:
    """P4: narrowing the interval around the same estimate never lowers autonomy.

    Four times the data at the same effect size gives a tighter interval on the same
    design, so the verdict may only stay the same or become more autonomous.
    """
    bad = []
    for mwe in (0.0, 5.0, 8.0, 9.0, 20.0):
        req = BASE.model_copy(update={"minimum_worthwhile_effect": mwe})
        wide = cc.analyze(_ops_data(n=800, noise=25.0), GRAPH, treatment="playbook",
                          outcome="minutes", estimand="att", policy=FAST)
        narrow = cc.analyze(_ops_data(n=6400, noise=25.0), GRAPH, treatment="playbook",
                            outcome="minutes", estimand="att", policy=FAST)
        a, b = decide(wide, req), decide(narrow, req)
        if _severity(b) > _severity(a):
            bad.append(f"P4 more data lowered autonomy at bar {mwe}: "
                       f"{a.verdict.value} -> {b.verdict.value}")
    return bad


# ── seeded defects ────────────────────────────────────────────────────────────


def _table_fails(cfg: dict[str, Any]) -> bool:
    return not all(r.passed for r in check_table())


def _scenario_fails(sid: str) -> bool:
    scenario = next(s for s in SCENARIOS if s.id == sid)
    return not check_table((scenario,))[0].passed


def _property_violated(tag: str, cfg: dict[str, Any]) -> bool:
    c = cfg["properties"]
    return any(v.startswith(tag) for v in check_properties(c["sweep"], c["seed"]))


#: Which check the pre-registration says must catch each defect. Where it names two, the
#: gate may catch it by either; both are tried.
CATCHER: dict[str, Callable[[dict[str, Any]], bool]] = {
    "ignore_reversibility": lambda cfg: _property_violated("P1", cfg),
    "ignore_risk_class": lambda cfg: _property_violated("P2", cfg)
    or _scenario_fails("strong_effect_high_risk"),
    "best_check_wins": _table_fails,
    "ignore_worthwhile_effect": lambda cfg: _scenario_fails("effect_below_bar"),
    "refusal_becomes_approval": lambda cfg: _property_violated("P5", cfg),
    "ignore_contested": lambda cfg: _property_violated("P6", cfg)
    or _scenario_fails("contested_result"),
}


def check_defects(cfg: dict[str, Any]) -> dict[str, bool]:
    """Run every seeded defect against its designated check. True means the gate saw it."""
    from causal_decision.bench.defects import DEFECTS

    caught = {}
    for name, defect in sorted(DEFECTS.items()):
        with defect():
            caught[name] = CATCHER[name](cfg)
    return caught


# ── runner ────────────────────────────────────────────────────────────────────


def run(cfg: dict[str, Any]) -> dict[str, Any]:
    table = [vars(r) for r in check_table()]
    violations = check_properties(cfg["properties"]["sweep"], cfg["properties"]["seed"])
    violations += check_evidence_monotonicity()
    caught = check_defects(cfg)

    failures: list[str] = []
    matched = sum(bool(r["passed"]) for r in table) / len(table)
    if matched < cfg["table"]["scenarios_must_match"]:
        failures += [f"scenario {r['id']}: expected {r['expected']}, got {r['got']}"
                     for r in table if not r["passed"]]
    if len(violations) > cfg["properties"]["violations_allowed"]:
        failures += violations[:20]
    required = set(cfg["seeded_defects"]["required"])
    if required != set(caught):
        failures.append(f"registered defects {sorted(required)} != implemented {sorted(caught)}")
    failures += [f"seeded defect {n} went undetected" for n, ok in caught.items() if not ok]
    return {
        "table": table,
        "scenarios_matched": matched,
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

    ap = argparse.ArgumentParser(description="Run the pre-registered D0 decision-policy gate")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    cfg, prereg_hash = load_prereg()
    doc = {
        "suite": "D0",
        "prereg_sha256": prereg_hash,
        "prereg_version": cfg["meta"]["version"],
        "git": git_sha(),
        **run(cfg),
    }
    text = json.dumps(doc, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(json.dumps({"passed": doc["passed"], "failures": doc["failures"]}), file=sys.stderr)
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
The decision tool: one call from data to verdict.

The point of doing the analysis and the ruling in a single tool is that an agent cannot
edit the evidence on its way to the verdict. These tests pin that the tool rules on what
it computed, that a refusal never becomes an action, and that the server exposes the tool
only when the optional layer is installed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from causal_mcp import server, tools

pytestmark = pytest.mark.skipif(not tools.HAS_DECISION, reason="causal-decision not installed")

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def ops(n: int = 4000, effect: float = -9.0, noise: float = 8.0) -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    load = rng.normal(size=n)
    playbook = (0.9 * load + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 + effect * playbook + 12 * load + rng.normal(0, noise, size=n)
    frame = pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})
    return {c: frame[c].tolist() for c in frame}


def test_a_strong_effect_on_a_reversible_action_is_acted_on() -> None:
    resp = tools.causal_decide(
        graph=GRAPH,
        treatment="playbook",
        outcome="minutes",
        action="apply remediation playbook X",
        direction="decrease",
        data=ops(),
        risk_class="low",
        minimum_worthwhile_effect=5.0,
        randomization_unit="incident",
        units_per_day=40,
    )
    assert resp.decision.verdict == "act"
    assert resp.outcome.kind == "result"
    assert resp.decision.evidence.result_digest == resp.outcome.result_digest
    assert resp.explanation.startswith("ACT:")
    assert resp.decision.decision_digest and resp.decision.policy_digest


def test_high_risk_always_reaches_a_person_on_the_same_evidence() -> None:
    kw: dict[str, Any] = {
        "graph": GRAPH,
        "treatment": "playbook",
        "outcome": "minutes",
        "action": "apply remediation playbook X",
        "direction": "decrease",
        "data": ops(),
        "minimum_worthwhile_effect": 5.0,
    }
    assert tools.causal_decide(risk_class="medium", **kw).decision.verdict == "act"
    high = tools.causal_decide(risk_class="high", **kw)
    assert high.decision.verdict == "act_with_approval"


def test_an_unidentified_effect_yields_the_experiment_that_would_settle_it() -> None:
    resp = tools.causal_decide(
        graph="playbook -> minutes; playbook <-> minutes",
        treatment="playbook",
        outcome="minutes",
        action="apply remediation playbook X",
        direction="decrease",
        data=ops(),
        minimum_worthwhile_effect=5.0,
        randomization_unit="incident",
        units_per_day=40,
    )
    assert resp.decision.verdict == "experiment"
    spec = resp.decision.experiment
    assert spec is not None and spec.unit == "incident" and spec.units_per_arm
    assert "Experiment that would settle it:" in resp.explanation


def test_bad_input_is_a_refusal_and_never_an_action() -> None:
    resp = tools.causal_decide(
        graph=GRAPH,
        treatment="playbook",
        outcome="minutes",
        action="apply remediation playbook X",
        direction="decrease",
    )
    assert resp.outcome.kind == "refusal"
    assert resp.decision.verdict == "refuse"
    assert resp.decision.evidence.benefit is None


def test_the_server_registers_the_tool_and_annotates_it_read_only() -> None:
    assert "causal_decide" in server.TOOLS
    fn, title = server.TOOLS["causal_decide"]
    assert fn is tools.causal_decide and "act" in title.lower()
    assert server.READ_ONLY.read_only_hint is True
    # Deciding is not acting: the tool returns a verdict, it does not execute anything.
    assert server.READ_ONLY.destructive_hint is False


def ops_by_severity(n: int = 4000) -> dict[str, list[Any]]:
    """The same operation, helping P1 incidents and harming P3 ones."""
    rng = np.random.default_rng(11)
    severity = rng.choice(["P1", "P3"], size=n, p=[0.65, 0.35])
    load = rng.normal(size=n)
    playbook = (0.9 * load + rng.logistic(size=n) > 0).astype(float)
    effect = np.where(severity == "P1", -20.0, 6.0)
    minutes = 60 + effect * playbook + 8 * load + rng.normal(0, 10, size=n)
    frame = pd.DataFrame(
        {"load": load, "playbook": playbook, "minutes": minutes, "severity": severity}
    )
    return {c: frame[c].tolist() for c in frame}


def test_declared_segments_reach_the_verdict() -> None:
    """An agent that declares subgroups gets a verdict that accounts for them.

    Pooled, this action is a large and precise saving and the tool automates it.
    It also adds six minutes to every P3 incident, which only the declared
    subgroups can show.
    """
    kwargs: dict[str, Any] = dict(
        graph=GRAPH,
        treatment="playbook",
        outcome="minutes",
        action="apply remediation playbook X",
        direction="decrease",
        data=ops_by_severity(),
        minimum_worthwhile_effect=2.0,
    )

    blind = tools.causal_decide(**kwargs)
    assert blind.decision.verdict == "act", (
        f"the fixture must look good pooled for this test to mean anything, got "
        f"{blind.decision.verdict}"
    )

    seeing = tools.causal_decide(**kwargs, segment_by=["severity"])
    assert seeing.decision.verdict != "act"
    assert {s.level for s in seeing.decision.evidence.segments} == {"P1", "P3"}
    harmed = next(
        c for c in seeing.decision.checks if c.name == "no declared segment is materially harmed"
    )
    assert not harmed.passed and "P3" in harmed.detail
    assert "P3" in seeing.explanation


def test_impact_says_a_new_confounder_breaks_the_old_estimate() -> None:
    new = GRAPH + "; backlog -> playbook; backlog -> minutes"
    resp = tools.causal_impact(GRAPH, new, "playbook", "minutes", adjust_for=["load"])
    assert resp.impact is not None and resp.impact.status.value == "invalidated"
    assert resp.changes is not None
    assert resp.changes.added_edges == ["backlog -> minutes", "backlog -> playbook"]


def test_impact_holds_for_an_irrelevant_revision_and_refuses_a_broken_graph() -> None:
    held = tools.causal_impact(GRAPH, GRAPH + "; season -> load", "playbook", "minutes")
    assert held.impact is not None and held.impact.status.value == "holds"
    bad = tools.causal_impact(GRAPH, "a -> b; b -> a", "playbook", "minutes")
    assert bad.impact is None and bad.refusal


def test_the_server_registers_the_impact_tool_with_the_decision_layer() -> None:
    assert "causal_impact" in server.TOOLS


def test_the_tool_listing_stays_small_enough_for_line_limited_clients() -> None:
    """The stdio client reads one JSON line per message with a 64 KiB limit; a listing
    over it fails every call. Adding causal_impact first pushed it over (62.9 KB before)."""
    import asyncio

    listed = asyncio.run(server.build_server().list_tools())
    size = sum(len(t.model_dump_json(exclude_none=True, by_alias=True)) for t in listed)
    assert size < 55_000, f"tools/list is {size} bytes; the stdio line limit is 65,536"
    assert all(t.input_schema for t in listed)
    decide = next(t for t in listed if t.name == "causal_decide")
    assert "segment_by" in (decide.description or ""), "the tool's own prose is untouched"
    assert "title" in str(decide.input_schema), "input schemas are not slimmed"


def test_a_staggered_rollout_can_be_analysed_and_decided_over_mcp() -> None:
    from causal_core.bench import t5

    frame = t5.simulate(t5.DESIGNS["staggered_constant"], 8, 0.5, np.random.default_rng(5))
    data = {c: frame[c].tolist() for c in frame}
    common = {"graph": t5.GRAPH, "treatment": "rollout", "outcome": "latency", "data": data,
              "design": "difference_in_differences", "unit": "unit", "time": "period",
              "estimand": "att"}
    analysed = tools.causal_analyze(**common)
    assert analysed.outcome.kind == "result", analysed.report
    assert analysed.outcome.certificate.tier.value == "C"
    assert analysed.outcome.event_study

    decided = tools.causal_decide(**common, action="keep the rollout", direction="increase",
                                  minimum_worthwhile_effect=0.5)
    assert decided.decision.verdict.value == "act_with_approval", decided.explanation

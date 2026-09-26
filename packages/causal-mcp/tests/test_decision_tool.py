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


def test_a_rollout_with_covariates_and_no_never_treated_units_is_analysed_over_mcp() -> None:
    """The two panel fields of contract 0.10.0 reach the kernel over MCP, and the response
    validates against the output schema as listed (after its type unions were merged)."""
    import asyncio

    import jsonschema
    from causal_core.bench import t5

    frame = t5.simulate(t5.DESIGNS["covariates_not_yet_treated"], 8, 0.5,
                        np.random.default_rng(6))
    out = tools.causal_analyze(graph=t5.GRAPH, treatment="rollout", outcome="latency",
                               data={c: frame[c].tolist() for c in frame},
                               design="difference_in_differences", unit="unit", time="period",
                               estimand="att", covariates=["x"], control_group="not_yet_treated")
    assert out.outcome.kind == "result", out.report
    assert out.outcome.primary.estimator == "did_group_time_dr"
    assert out.outcome.primary.details["control_group"] == "not_yet_treated"
    listed = asyncio.run(server.build_server().list_tools())
    schema = next(t for t in listed if t.name == "causal_analyze").output_schema
    jsonschema.validate(out.model_dump(mode="json"), schema)


def test_merged_type_unions_accept_exactly_what_the_long_form_accepts() -> None:
    from jsonschema import Draft202012Validator

    long_form = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    short = server._merge_type_unions(long_form)
    assert short == {"type": ["number", "null"]}
    for value in (1.5, 0, None, "x", [1], {"a": 1}, True):
        assert Draft202012Validator(long_form).is_valid(value) == Draft202012Validator(
            short
        ).is_valid(value), value
    constrained = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    assert server._merge_type_unions(constrained) == constrained, "only bare types merge"


def test_a_watched_canary_is_monitored_and_decided_over_mcp() -> None:
    from causal_core.bench import t6

    frame = t6.simulate(t6.STREAMS["harm"], 3000, np.random.default_rng(8))
    common = {"graph": t6.GRAPH, "treatment": "canary", "outcome": "latency",
              "data": {c: frame[c].tolist() for c in frame}, "design": "randomized",
              "sequence": "arrival", "expected_treated_share": 0.5}
    analysed = tools.causal_analyze(**common)
    assert analysed.outcome.kind == "result", analysed.report
    assert analysed.outcome.sequential is not None
    assert analysed.outcome.sequential.first_below_zero is not None
    decided = tools.causal_decide(**common, action="continue the canary", direction="increase",
                                  minimum_worthwhile_effect=0.0)
    assert decided.decision.verdict.value == "refuse", decided.explanation


def test_a_cluster_randomized_rollout_is_analysed_over_mcp() -> None:
    from causal_core.bench import t7

    frame, _ = t7.simulate(t7.DESIGNS["moderate"], np.random.default_rng(10))
    out = tools.causal_analyze(graph=t7.GRAPH, treatment="canary", outcome="latency",
                               data={c: frame[c].tolist() for c in frame},
                               design="randomized", cluster="service")
    assert out.outcome.kind == "result", out.report
    assert out.outcome.diagnostics.n_clusters == 40


def test_a_switchback_is_analysed_over_mcp() -> None:
    from causal_core.bench import t8

    cfg = t8.load_prereg()[0]
    frame = t8.simulate(t8.DESIGNS["carryover"], cfg, np.random.default_rng(11))
    out = tools.causal_analyze(graph=t8.GRAPH, treatment="policy", outcome="latency",
                               data={c: frame[c].tolist() for c in frame}, design="randomized",
                               cluster="block", time="minute", washout=20)
    assert out.outcome.kind == "result", out.report


def test_impact_recertifies_the_strategy_the_earlier_query_used() -> None:
    """causal_impact once took only treatment, outcome and adjust_for, so it judged every
    earlier answer as an observational one. The graph truths here are set by hand."""
    # A randomized answer is broken by learning that W drives the treatment: the
    # arms were not exchangeable. As an observational query the same revision holds.
    old, new = "X -> Y; W -> Y", "X -> Y; W -> Y; W -> X"
    as_randomized = tools.causal_impact(old, new, "X", "Y", design="randomized")
    assert as_randomized.impact is not None
    assert as_randomized.impact.old_strategy == "randomized"
    assert as_randomized.impact.status.value == "invalidated"
    # A proximal answer exists only with its proxies; without them the old graph
    # identifies nothing, and the tool could not say the revision breaks it.
    proximal = "X -> Y; P -> X; Q -> Y; U -> X; U -> Y; U -> P; U -> Q; latent U"
    broken = proximal + "; P -> Y"
    blind = tools.causal_impact(proximal, broken, "X", "Y")
    assert blind.impact is not None and blind.impact.status.value == "still_unidentified"
    seen = tools.causal_impact(
        proximal, broken, "X", "Y", treatment_proxies=["P"], outcome_proxies=["Q"]
    )
    assert seen.impact is not None and seen.impact.old_strategy == "proximal"
    assert seen.impact.status.value == "invalidated"


def test_decide_takes_treatment_levels() -> None:
    """A dose of 0 vs 2 is worth twice the per-unit effect; causal_decide used to drop the
    levels (it passed None), so a continuous treatment was always ruled on per unit."""
    rng = np.random.default_rng(3)
    n = 3000
    load = rng.normal(size=n)
    dose = np.clip(1 + 0.5 * load + rng.normal(size=n), 0, None)
    minutes = 60 - 3.0 * dose + 5 * load + rng.normal(0, 4, size=n)
    data = {"load": load.tolist(), "playbook": dose.tolist(), "minutes": minutes.tolist()}
    common: dict[str, Any] = {"graph": GRAPH, "treatment": "playbook", "outcome": "minutes",
                              "data": data, "action": "double the dose",
                              "direction": "decrease", "minimum_worthwhile_effect": 4.0}
    per_unit = tools.causal_decide(**common)
    doubled = tools.causal_decide(**common, treatment_levels=[0.0, 2.0])
    assert per_unit.outcome.kind == doubled.outcome.kind == "result", doubled.explanation
    assert abs(per_unit.outcome.primary.point - -3.0) < 0.3
    assert abs(doubled.outcome.primary.point - -6.0) < 0.6
    assert per_unit.decision.verdict.value != "act"  # 3 < 4: not worth it per unit
    assert doubled.decision.verdict.value in ("act", "act_with_approval")

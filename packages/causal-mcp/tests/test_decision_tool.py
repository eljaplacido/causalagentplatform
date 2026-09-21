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

"""The shadow-run context pack (shadow/) says only what the code does.

devbrain and agentcenter design shadow studies from shadow/capability-card.json and
shadow/study-template.json. A card that names a query field the contract does not have, or
misses a refusal reason the kernel can return, would have them propose studies the platform
cannot run, or plant cases for a refusal that never comes. So the card is pinned to the
contract here, in both directions, and to the contract version it was written against: a
contract bump fails this test until someone re-reads the card and bumps `contract_version`.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from causal_core.contract import (
    CONTRACT_VERSION,
    AbstentionReason,
    AnalysisPolicy,
    CausalQuery,
    Design,
)
from causal_core.graph import parse_graph

from causal_decision import DecisionRequest, Verdict

REPO = Path(__file__).resolve().parents[3]
SHADOW = REPO / "shadow"

# The Oracle's hypothesis spec (agentcenter :8767/api/hypotheses); backend/src/connectors/
# agentcenter.py reads these keys when it turns a hypothesis into a prefilled analysis.
ORACLE_SPEC_KEYS = {"id", "title", "statement", "arms", "outcome_label", "graph", "design",
                    "estimand", "decision"}


@pytest.fixture(scope="module")
def card() -> dict:
    return json.loads((SHADOW / "capability-card.json").read_text())


@pytest.fixture(scope="module")
def study() -> dict:
    return json.loads((SHADOW / "study-template.json").read_text())


def test_card_was_read_against_this_contract(card: dict) -> None:
    assert card["contract_version"] == CONTRACT_VERSION, (
        f"the contract is {CONTRACT_VERSION}; re-read shadow/capability-card.json against it, "
        "then set contract_version"
    )


def test_every_design_is_on_the_card(card: dict) -> None:
    on_card = {d["query"]["design"] for d in card["designs"]}
    assert on_card == {d.value for d in Design}


def test_card_query_fields_exist(card: dict) -> None:
    fields = set(CausalQuery.model_fields)
    for d in card["designs"]:
        named = set(d["query"]["required"]) | set(d["query"]["optional"])
        assert named <= fields, f"{d['id']}: {sorted(named - fields)} are not CausalQuery fields"
        assert {"treatment", "outcome"} <= set(d["query"]["required"]), d["id"]
    for c in card["checks"]:
        assert set(c["query_fields"]) <= fields, c["id"]


def test_refusals_match_the_kernel(card: dict) -> None:
    reasons = {r.value for r in AbstentionReason}
    assert set(card["refusals"]) == reasons
    for d in card["designs"]:
        assert set(d["expect_refusals"]) <= reasons, d["id"]


def test_verdicts_and_decision_fields_match(card: dict) -> None:
    assert set(card["verdicts"]) == {v.value for v in Verdict}
    assert set(card["decision_fields"]) == set(DecisionRequest.model_fields)


def test_card_minimums_are_the_policy_defaults(card: dict) -> None:
    policy = AnalysisPolicy()
    text = card["inputs"]["minimums"]
    assert f"at least {policy.min_n} rows" in text
    assert f"{policy.min_per_arm} per arm" in text


def test_every_gate_named_is_preregistered(card: dict) -> None:
    named = {g for d in card["designs"] for g in d["gates"]}
    named |= {g for c in card["checks"] for g in c["gates"]}
    for gate in named:
        assert (REPO / "benchmarks" / gate / "PRE-REGISTRATION.md").is_file(), gate


def test_every_example_named_is_served(card: dict) -> None:
    served = set(re.findall(r'\bid="([a-z0-9-]+)"', (REPO / "backend/src/analysis_examples.py").read_text()))
    assert set(card["examples"]) <= served


def test_study_template_is_an_oracle_spec_the_platform_accepts(study: dict) -> None:
    assert ORACLE_SPEC_KEYS <= set(study)
    query = CausalQuery(**study["query"])
    assert query.design.value == study["design"]
    assert query.estimand == study["estimand"]
    DecisionRequest(outcome=query.outcome, **study["decision"])
    graph = parse_graph(study["graph"])
    assert {query.treatment, query.outcome, *query.segment_by} <= set(graph.nodes)
    assert set(study["arms"]) == {"0", "1"}
    assert study["shadow"]["kind"] == "causalplatform"


def test_preregistration_template_bars_parse() -> None:
    text = (SHADOW / "PRE-REGISTRATION.template.md").read_text()
    blocks = re.findall(r"```toml\n(.*?)```", text, re.S)
    assert len(blocks) == 1
    bars = tomllib.loads(blocks[0])
    assert bars["bars"]["max_confident_wrong"] == 0
    assert 0 < bars["round"]["min_planted_share"] < 1

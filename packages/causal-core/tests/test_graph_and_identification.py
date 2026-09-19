"""Graph syntax, latent projection and identification behaviour."""

from __future__ import annotations

from unittest import mock

import networkx as nx
import pytest

import causal_core.identification as ident
from causal_core import CausalQuery, Graph, parse_graph
from causal_core.contract import EdgeOrigin, EdgeStatus, Strategy, Tier
from causal_core.graph import GraphSyntaxError, as_causal_graph
from causal_core.identification import (
    IdentificationInconsistency,
    InvalidAdjustmentSet,
    identify,
)
from causal_core.identification.id_algorithm import IDResult


def g(text: str) -> Graph:
    return Graph.from_spec(parse_graph(text))


def test_syntax_chains_reverse_bidirected_latent_attrs_and_dot_wrapper() -> None:
    spec = parse_graph(
        """digraph {
          A -> B -> C   # chain
          D <- C; C <-> E
          latent U
          U -> A
          A -> E [origin=llm_prior]
          F
        }"""
    )
    edges = {(e.source, e.target, e.kind) for e in spec.edges}
    assert ("A", "B", "directed") in edges and ("B", "C", "directed") in edges
    assert ("C", "D", "directed") in edges
    assert ("C", "E", "bidirected") in edges
    assert spec.latent == ("U",)
    assert "F" in spec.nodes
    llm = next(e for e in spec.edges if e.origin is EdgeOrigin.llm_prior)
    assert llm.status is EdgeStatus.proposed


@pytest.mark.parametrize("bad", ["A -> A", "A -> B; B -> A", "A -> 1bad", "A -> B [origin]"])
def test_syntax_errors_and_cycles_are_rejected(bad: str) -> None:
    with pytest.raises((GraphSyntaxError, ValueError)):
        Graph.from_spec(parse_graph(bad))


def test_other_graph_inputs() -> None:
    dg = nx.DiGraph([("Z", "X"), ("X", "Y")])
    assert {(e.source, e.target) for e in as_causal_graph(dg).edges} == {("Z", "X"), ("X", "Y")}
    assert len(as_causal_graph([("Z", "X"), ("X", "Y")]).edges) == 2


def test_latent_projection() -> None:
    graph = g("latent U, L; U -> X; U -> Y; X -> L -> Y")
    directed, bidirected = graph.admg
    assert set(directed.edges) == {("X", "Y")}  # X -> L -> Y through a latent mediator
    assert bidirected == {frozenset({"X", "Y"})}


def test_refuted_edges_are_dropped() -> None:
    graph = g("Z -> X; Z -> Y [status=refuted]; X -> Y")
    cert = identify(graph, CausalQuery(treatment="X", outcome="Y"))
    assert cert.adjustment_set == ()


def test_o_set_includes_precision_variables_and_excludes_mediators() -> None:
    cert = identify(
        g("Z -> X; Z -> Y; X -> M -> Y; W -> Y"), CausalQuery(treatment="X", outcome="Y")
    )
    assert cert.strategy is Strategy.backdoor
    assert cert.adjustment_set == ("W", "Z")


def test_user_adjustment_set_is_validated_with_the_open_path() -> None:
    graph = g("A -> M; B -> M; A -> X; B -> Y; X -> Y")
    with pytest.raises(InvalidAdjustmentSet) as err:
        identify(graph, CausalQuery(treatment="X", outcome="Y", adjust_for=("M",)))
    assert "X <- A -> M <- B -> Y" in err.value.detail
    assert err.value.suggestion == ("B",)
    ok = identify(graph, CausalQuery(treatment="X", outcome="Y", adjust_for=("A", "M")))
    assert ok.adjustment_set == ("A", "M")


def test_mediator_in_adjustment_set_is_refused() -> None:
    with pytest.raises(InvalidAdjustmentSet, match="mediators"):
        identify(
            g("Z -> X; Z -> Y; X -> M -> Y"),
            CausalQuery(treatment="X", outcome="Y", adjust_for=("Z", "M")),
        )


def test_not_identified_carries_hedge_and_tier_d() -> None:
    cert = identify(g("X -> Y; X <-> Y"), CausalQuery(treatment="X", outcome="Y"))
    assert cert.status.value == "not_identified" and cert.tier is Tier.D
    assert cert.general_id and cert.general_id.startswith("FAIL: hedge")
    hints = ident.missing_information(g("X -> Y; X <-> Y"), "X", "Y")
    assert any("instrument" in h for h in hints)


def test_parametric_strategies_are_tier_b() -> None:
    assert (
        identify(g("Z -> X -> Y; X <-> Y"), CausalQuery(treatment="X", outcome="Y")).tier is Tier.B
    )


def test_strategy_disagreeing_with_id_is_an_internal_error() -> None:
    with (
        mock.patch.object(ident, "identify_id", lambda *_: IDResult(False, "FAIL: forced")),
        pytest.raises(IdentificationInconsistency),
    ):
        identify(g("Z -> X; Z -> Y; X -> Y"), CausalQuery(treatment="X", outcome="Y"))


def test_randomized_design_contradicted_by_graph_is_reported() -> None:
    cert = identify(
        g("Z -> X; Z -> Y; X -> Y"), CausalQuery(treatment="X", outcome="Y", design="randomized")
    )
    assert cert.strategy is Strategy.backdoor
    check = next(c for c in cert.conditions if "randomized" in c.name)
    assert not check.holds and "Z" in check.detail

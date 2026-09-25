"""A causal model is a history of versions, and a revision says which verdicts it touches.

The cases that matter most are the two plausible wrong answers about a revision
-- "the kernel still picks back-door" and "the kernel's new set is valid" --
and the rule that nobody approves an edge they introduced.
"""

from __future__ import annotations

import pytest
from causal_core.contract import CausalQuery, EdgeStatus

from causal_decision.assumptions import (
    Assumption,
    AssumptionKind,
    EdgeReview,
    ImpactStatus,
    ModelError,
    SelfReviewError,
    apply_diff,
    canonical,
    create,
    diff,
    edge_key,
    impact,
    review,
    revise,
    verify_history,
)

NOW = "2026-09-25T12:00:00+00:00"
Q = CausalQuery(treatment="playbook", outcome="minutes")


def ops(**over: str) -> str:
    return over.get("graph", "load -> playbook; load -> minutes; playbook -> minutes")


def v1(graph: str | None = None, author: str = "alice"):
    return create("incident-remediation", graph or ops(), author_id=author, created_at=NOW,
                  why="initial model from the incident review")


# ── versions ──────────────────────────────────────────────────────────────────


def test_equal_graphs_have_equal_digests_however_they_are_written() -> None:
    a = create("m", "load -> playbook; load -> minutes; playbook -> minutes", author_id="a",
               created_at=NOW, why="first write")
    b = create("m", "playbook -> minutes\nload -> minutes\nload -> playbook", author_id="a",
               created_at=NOW, why="first write")
    assert a.digest == b.digest
    assert canonical("Y <-> X; X -> Y") == canonical("X <-> Y; X -> Y")


def test_a_graph_the_kernel_cannot_analyse_is_not_stored() -> None:
    with pytest.raises(ValueError, match="cycle"):
        v1("a -> b; b -> a")
    with pytest.raises(ModelError, match="more than once"):
        v1("a -> b; a -> b")
    with pytest.raises(ModelError, match="model name"):
        create("Has Spaces", ops(), author_id="a", created_at=NOW, why="bad name")


def test_a_revision_links_to_its_parent_and_the_history_verifies() -> None:
    m1 = v1()
    m2 = revise(m1, graph=ops() + "; backlog -> minutes", author_id="bob", created_at=NOW,
                why="backlog found to drive resolution time")
    assert m2.version == 2 and m2.parent_digest == m1.digest
    assert m2.edge_authors["backlog -> minutes"] == "bob"
    assert m2.edge_authors["load -> playbook"] == "alice", "a persisting edge keeps its author"
    assert verify_history([m1, m2]).ok


def test_tampering_with_a_past_version_is_detected() -> None:
    m1 = v1()
    m2 = revise(m1, graph=ops() + "; backlog -> minutes", author_id="bob", created_at=NOW,
                why="backlog found to drive resolution time")
    forged = m1.model_copy(update={"why": "something else"})
    rep = verify_history([forged, m2])
    assert not rep.ok and rep.broken_at == 0
    resealed = forged.sealed()
    rep = verify_history([resealed, m2])
    assert not rep.ok and rep.broken_at == 1 and "parent link" in rep.reason


def test_a_revision_that_changes_nothing_is_refused() -> None:
    m1 = v1()
    with pytest.raises(ModelError, match="must change"):
        revise(m1, author_id="bob", created_at=NOW, why="no-op")


def test_assumptions_outside_the_graph_are_part_of_the_version() -> None:
    m1 = v1()
    sutva = Assumption(kind=AssumptionKind.no_interference,
                       statement="one incident's playbook does not change another's duration",
                       about=("playbook", "minutes"))
    m2 = revise(m1, assumptions=[sutva], author_id="bob", created_at=NOW, why="state SUTVA")
    assert m2.digest != m1.digest
    assert diff(m1, m2).assumptions_added == (sutva,)


# ── reviews ───────────────────────────────────────────────────────────────────


def test_nobody_reviews_an_edge_they_introduced() -> None:
    m1 = v1("load -> playbook; load -> minutes; playbook -> minutes [origin=llm_prior]",
            author="agent-7")
    ruling = EdgeReview(edge="playbook -> minutes", status="approved", reason="seen in the RCA")
    with pytest.raises(SelfReviewError):
        review(m1, [ruling], reviewer_id="agent-7", created_at=NOW)

    m2 = review(m1, [ruling], reviewer_id="carol", created_at=NOW)
    assert m2.unreviewed_edges == []
    assert m2.author_id == "carol" and m2.reviews == (ruling,)


def test_the_rule_holds_across_versions() -> None:
    """An edge added in v2 by bob stays bob's in v5."""
    m1 = v1()
    m2 = revise(m1, graph=ops() + "; backlog -> minutes [origin=llm_prior]", author_id="bob",
                created_at=NOW, why="an LLM-proposed confounder")
    m3 = revise(m2, assumptions=[Assumption(kind=AssumptionKind.timing,
                statement="durations are recorded after the playbook runs")],
                author_id="alice", created_at=NOW, why="timing")
    with pytest.raises(SelfReviewError):
        review(m3, [EdgeReview(edge="backlog -> minutes", status="approved", reason="looks fine")],
               reviewer_id="bob", created_at=NOW)


def test_a_revision_cannot_approve_an_edge_by_resubmitting_the_graph() -> None:
    m1 = v1("load -> playbook; load -> minutes; playbook -> minutes [origin=llm_prior]")
    with pytest.raises(ModelError, match="rulings are made by review"):
        revise(m1, graph="load -> playbook; load -> minutes; playbook -> minutes "
               "[origin=llm_prior, status=approved]", author_id="alice", created_at=NOW,
               why="approve it myself")
    with pytest.raises(ModelError, match="arrive already"):
        create("m", "a -> b [status=approved]", author_id="a", created_at=NOW, why="pre-approved")


def test_a_refuted_edge_stays_in_the_record() -> None:
    m1 = v1(ops() + "; backlog -> minutes [origin=llm_prior]")
    m2 = review(m1, [EdgeReview(edge="backlog -> minutes", status="refuted",
                reason="backlog is measured after resolution")], reviewer_id="carol",
                created_at=NOW)
    statuses = {f"{e.source} -> {e.target}": e.status for e in m2.graph.edges}
    assert statuses["backlog -> minutes"] is EdgeStatus.refuted


# ── diff ──────────────────────────────────────────────────────────────────────


def test_a_removed_edge_is_reported_and_the_diff_round_trips() -> None:
    a = canonical("z -> x; z -> y; x -> y; w -> y; x <-> w")
    b = canonical("z -> x; x -> y; w -> y; x -> m; m -> y")
    d = diff(a, b)
    assert {edge_key(e) for e in d.removed_edges} == {"z -> y", "w <-> x"}
    assert {edge_key(e) for e in d.added_edges} == {"x -> m", "m -> y"}
    assert canonical(apply_diff(a, d)) == b
    assert diff(b, b).empty


# ── impact: the question a revision raises ────────────────────────────────────


def test_self_impact_holds() -> None:
    m = v1()
    assert impact(m, m, Q).status is ImpactStatus.holds


def test_a_new_confounder_of_treatment_and_outcome_invalidates_the_old_set() -> None:
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; backlog -> playbook; backlog -> minutes"
    got = impact(old, new, Q)
    assert got.status is ImpactStatus.invalidated
    assert got.old_adjustment_set == ("load",)
    assert "backlog" in got.new_adjustment_set


def test_an_irrelevant_new_edge_leaves_the_estimate_standing() -> None:
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; season -> load"
    assert impact(old, new, Q).status is ImpactStatus.holds


def test_still_back_door_is_not_the_same_as_still_valid() -> None:
    """The first plausible wrong answer: the new graph is identified by back-door too."""
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; backlog -> playbook; backlog -> minutes"
    got = impact(old, new, Q)
    assert got.new_strategy is not None and got.new_strategy.value == "backdoor"
    assert got.status is ImpactStatus.invalidated


def test_the_old_set_can_stay_valid_when_the_kernel_would_now_choose_another() -> None:
    """The mirror image: a different preferred set does not invalidate the one that was used."""
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; noise -> minutes"  # a precision variable: the O-set now includes it
    got = impact(old, new, Q)
    assert got.status is ImpactStatus.holds
    assert got.new_adjustment_set != got.old_adjustment_set


def test_a_declared_hidden_confounder_invalidates_and_removes_identification() -> None:
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; playbook <-> minutes"
    got = impact(old, new, Q)
    assert got.status is ImpactStatus.invalidated
    assert got.new_strategy is None


def test_an_adjustment_variable_that_becomes_a_mediator_invalidates() -> None:
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = "playbook -> load; load -> minutes; playbook -> minutes"
    assert impact(old, new, Q).status is ImpactStatus.invalidated


def test_a_graph_that_newly_identifies_the_effect_is_reported_as_gained() -> None:
    old = "playbook -> minutes; playbook <-> minutes"
    new = "playbook -> mech -> minutes; playbook <-> minutes"
    got = impact(old, new, Q)
    assert got.status is ImpactStatus.gained


def test_impact_is_deterministic() -> None:
    old = "load -> playbook; load -> minutes; playbook -> minutes"
    new = old + "; backlog -> playbook; backlog -> minutes"
    assert impact(old, new, Q).digest == impact(old, new, Q).digest


def test_a_review_changes_what_is_unreviewed_not_what_is_identified() -> None:
    m1 = v1(ops() + "; backlog -> minutes [origin=llm_prior]")
    m2 = review(m1, [EdgeReview(edge="backlog -> minutes", status="approved",
                reason="confirmed by the RCA")], reviewer_id="carol", created_at=NOW)
    got = impact(m1, m2, Q)
    assert got.status is ImpactStatus.holds
    assert got.unreviewed_before == ("backlog -> minutes",) and got.unreviewed_after == ()

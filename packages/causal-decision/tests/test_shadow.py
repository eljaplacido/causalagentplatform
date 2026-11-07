"""Shadow decisions at runtime: sealed, kept apart, never emitted (septa-95).

Preconditions: a decision and the outcome it rested on (the D0 fixtures, whose effect
is set by construction).

Postconditions (causal_decision.shadow and DecisionRecord.shadow's docstrings,
shadow/README.md step 3):
  - a record sealed with a shadow tag carries it, and `emittable` refuses it, whatever
    else the record says; a real record passes through unchanged;
  - the tag is sealed: stripping it from, or adding it to, a recorded entry breaks the
    chain at that entry;
  - a record without a tag digests exactly as before the field existed (the pinned
    pre-0.6 chain, tests/fixtures/ledger_chain_before_model_ref.json, keeps verifying:
    test_ledger.py);
  - listings select real, shadow, one tag, or all; the default is real;
  - a shadow decision on an action does not retire a real one in `past_review`, nor
    the reverse;
  - a tag is an identifier, not prose.

Undefined here: the wording of the refusal beyond naming the tag.
"""

from __future__ import annotations

import pytest
from causal_core.contract import CausalResult
from pydantic import ValidationError

from causal_decision import decide
from causal_decision.bench.d0 import BASE, strong_effect
from causal_decision.ledger import DecisionRecord, append, past_review, verify_chain
from causal_decision.shadow import (
    ShadowEmissionError,
    emittable,
    in_scope,
    is_shadow,
    select,
)

NOW = "2026-10-01T09:00:00+00:00"
REVIEW = "2026-10-31T09:00:00+00:00"


@pytest.fixture(scope="module")
def outcome() -> CausalResult:
    out = strong_effect()
    assert isinstance(out, CausalResult)
    return out


def _mixed(outcome: CausalResult) -> list[DecisionRecord]:
    """real, shadow-round-1, real, shadow-round-2 -- on the same action."""
    decision = decide(outcome, BASE)
    records: list[DecisionRecord] = []
    tail = None
    for tag in (None, "shadow-round-1", None, "shadow-round-2"):
        tail = append(tail, decision, outcome, recorded_at=NOW, review_by=REVIEW, shadow=tag)
        records.append(tail)
    return records


def test_a_shadow_decision_cannot_be_emitted(outcome: CausalResult) -> None:
    records = _mixed(outcome)
    real, shadow = records[0], records[1]
    assert emittable(real) is real and not is_shadow(real)
    assert is_shadow(shadow)
    with pytest.raises(ShadowEmissionError, match="shadow-round-1"):
        emittable(shadow)


def test_the_tag_is_sealed_into_the_entry(outcome: CausalResult) -> None:
    records = _mixed(outcome)
    assert verify_chain(records).ok
    assert records[1].entry_digest != records[0].model_copy(
        update={"shadow": "x", "entry_digest": ""}
    ).sealed().entry_digest

    # Pass a shadow verdict off as a real one: the chain breaks at that entry.
    stripped = records.copy()
    stripped[1] = records[1].model_copy(update={"shadow": None})
    report = verify_chain(stripped)
    assert not report.ok and report.broken_at == 1

    # File a real verdict away as shadow: the same.
    hidden = records.copy()
    hidden[2] = records[2].model_copy(update={"shadow": "shadow-round-1"})
    report = verify_chain(hidden)
    assert not report.ok and report.broken_at == 2


def test_an_untagged_record_digests_without_the_field(outcome: CausalResult) -> None:
    record = _mixed(outcome)[0]
    assert "shadow" not in record.content()
    assert "shadow" in _mixed(outcome)[1].content()


def test_listings_keep_shadow_apart(outcome: CausalResult) -> None:
    records = _mixed(outcome)
    seqs = lambda rs: [r.sequence for r in rs]  # noqa: E731
    assert seqs(select(records)) == [0, 2]
    assert seqs(select(records, "real")) == [0, 2]
    assert seqs(select(records, "shadow")) == [1, 3]
    assert seqs(select(records, "all")) == [0, 1, 2, 3]
    assert seqs(select(records, "real", tag="shadow-round-2")) == [3]
    assert not in_scope(records[0], "all", tag="shadow-round-1")


def test_a_shadow_decision_does_not_retire_a_real_one(outcome: CausalResult) -> None:
    records = _mixed(outcome)
    due = past_review(records, measured=set(), as_of="2026-12-01T00:00:00+00:00")
    # Per tag, the newest on the action stands: real #2, shadow-round-1 #1, shadow-round-2 #3.
    # Before the tag was considered, #3 (shadow) retired real #2 and the real decision
    # dropped out of the list.
    assert sorted(d.sequence for d in due) == [1, 2, 3]
    real_due = past_review(select(records), measured=set(), as_of="2026-12-01T00:00:00+00:00")
    assert [d.sequence for d in real_due] == [2]


@pytest.mark.parametrize("tag", ["", "shadow round 1", "-leading", "x" * 101, "a\nb"])
def test_a_tag_is_an_identifier(outcome: CausalResult, tag: str) -> None:
    with pytest.raises(ValidationError):
        append(None, decide(outcome, BASE), outcome, recorded_at=NOW, shadow=tag)

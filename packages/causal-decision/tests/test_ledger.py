"""
The decision record holds, and says where it stops holding.

A hash chain is only worth having if it actually catches the three things that go
wrong -- an entry edited, an entry removed, entries reordered -- and if it reports
*which* entry, because "something, somewhere, was tampered with" is not an audit
trail. Each of those gets a test. So does the limit: a chain rewritten from the
altered entry onward verifies, which is why the module says so rather than
implying otherwise.
"""

from __future__ import annotations

import pytest
from causal_core.contract import CausalResult
from pydantic import ValidationError

from causal_decision import Verdict, decide
from causal_decision.bench.d0 import BASE, not_identified, strong_effect, weak_effect
from causal_decision.ledger import (
    GENESIS,
    Approval,
    DecisionRecord,
    append,
    replays,
    verify_chain,
)

NOW = "2026-09-21T09:00:00+00:00"


@pytest.fixture(scope="module")
def outcome() -> CausalResult:
    out = strong_effect()
    assert isinstance(out, CausalResult)
    return out


#: Requests chosen so the chain holds three *different* verdicts. A chain of
#: identical entries cannot show that tampering was detected -- editing a field to
#: the value it already had changes nothing, and the test would pass on a broken
#: implementation. (It did, on the first run of this file.)
_REQUESTS = (
    BASE,                                                  # act
    BASE.model_copy(update={"minimum_worthwhile_effect": 40.0}),  # refuse
    BASE.model_copy(update={"reversible": False}),         # act_with_approval
)


def _chain(outcome: CausalResult, n: int = 3) -> list[DecisionRecord]:
    records: list[DecisionRecord] = []
    tail = None
    for request in _REQUESTS[:n]:
        tail = append(tail, decide(outcome, request), outcome, recorded_at=NOW)
        records.append(tail)
    assert len({r.decision.verdict for r in records}) == n, "the fixture lost its variety"
    return records


def test_a_fresh_chain_starts_at_genesis_and_holds(outcome: CausalResult) -> None:
    records = _chain(outcome)
    assert records[0].prev_digest == GENESIS
    report = verify_chain(records)
    assert report.ok, report.reason
    assert report.length == 3
    assert report.head_digest == records[-1].entry_digest


def test_an_empty_chain_is_not_a_failure() -> None:
    report = verify_chain([])
    assert report.ok and report.length == 0


def test_each_entry_names_the_evidence_it_rested_on(outcome: CausalResult) -> None:
    record = append(None, decide(outcome, BASE), outcome, recorded_at=NOW)
    assert record.evidence.outcome_kind == "result"
    assert record.evidence.result_digest == outcome.result_digest
    assert record.evidence.graph_digest and record.evidence.data_digest
    assert record.evidence.core_version


def test_a_refusal_is_recorded_as_readily_as_a_result() -> None:
    """Deciding not to act is a decision, and the reason belongs in the record."""
    out = not_identified()
    record = append(None, decide(out, BASE), out, recorded_at=NOW)
    assert record.evidence.outcome_kind == "refusal"
    assert record.evidence.result_digest is None
    assert record.decision.verdict in ("experiment", "refuse")
    assert verify_chain([record]).ok


def test_editing_an_entry_is_caught_and_located(outcome: CausalResult) -> None:
    records = _chain(outcome)
    tampered = list(records)
    # The most valuable thing to forge: a refusal becoming permission.
    assert records[1].decision.verdict == "refuse"
    tampered[1] = records[1].model_copy(
        update={"decision": records[1].decision.model_copy(update={"verdict": Verdict.act})}
    )

    report = verify_chain(tampered)
    assert not report.ok
    assert report.broken_at == 1
    assert "altered" in report.reason


def test_removing_an_entry_is_caught(outcome: CausalResult) -> None:
    records = _chain(outcome)
    report = verify_chain([records[0], records[2]])
    assert not report.ok
    assert report.broken_at == 1


def test_reordering_is_caught(outcome: CausalResult) -> None:
    records = _chain(outcome)
    report = verify_chain([records[1], records[0], records[2]])
    assert not report.ok
    assert report.broken_at == 0


def test_appending_an_entry_that_points_at_nothing_is_caught(outcome: CausalResult) -> None:
    records = _chain(outcome, n=2)
    orphan = records[1].model_copy(update={"prev_digest": GENESIS}).sealed()
    report = verify_chain([records[0], orphan])
    assert not report.ok
    assert report.broken_at == 1
    assert "broken here" in report.reason


def test_a_chain_rewritten_from_the_edit_onward_verifies(outcome: CausalResult) -> None:
    """The limit, stated as a test rather than as a caveat nobody reads.

    Whoever can rewrite the store can rewrite the chain. Detection becomes
    prevention only when a head digest is anchored somewhere the writer does not
    control -- so what a verified chain proves is that it is *internally*
    consistent, and the head digest is the thing worth publishing.
    """
    records = _chain(outcome)
    forged = [records[0]]
    for rec in records[1:]:
        forged.append(
            rec.model_copy(
                update={
                    "prev_digest": forged[-1].entry_digest,
                    "decision": rec.decision.model_copy(update={"verdict": Verdict.act}),
                }
            ).sealed()
        )

    assert verify_chain(forged).ok, "a fully rewritten chain is self-consistent"
    assert forged[-1].entry_digest != records[-1].entry_digest, (
        "...but its head differs, which is why the head is what gets anchored"
    )


def test_an_approval_is_part_of_the_record(outcome: CausalResult) -> None:
    decision = decide(outcome, BASE.model_copy(update={"reversible": False}))
    assert decision.verdict == "act_with_approval"

    approval = Approval(
        approver_id="governance-officer-1",
        approved_at=NOW,
        authorisation_id="a2f1c6de-0000-4000-8000-000000000001",
        notes="Checked against the March rollout.",
    )
    record = append(None, decision, outcome, recorded_at=NOW, approval=approval, executed=True)
    assert record.approval is not None
    assert record.approval.approver_id == "governance-officer-1"
    assert record.executed is True
    assert verify_chain([record]).ok

    # Forging the approver breaks the entry, which is the point of recording it here.
    forged = record.model_copy(
        update={"approval": approval.model_copy(update={"approver_id": "someone-else"})}
    )
    assert not verify_chain([forged]).ok


def test_deciding_and_acting_are_separate_facts(outcome: CausalResult) -> None:
    """`act` is what the policy said; `executed` is what happened. Not the same."""
    record = append(None, decide(outcome, BASE), outcome, recorded_at=NOW)
    assert record.decision.verdict == "act"
    assert record.executed is False, "a ledger must not report an action nobody took"


def test_a_record_can_be_replayed(outcome: CausalResult) -> None:
    """Re-deciding from the same inputs must reproduce the recorded verdict exactly."""
    record = append(None, decide(outcome, BASE), outcome, recorded_at=NOW)
    assert replays(record, decide(outcome, BASE))
    assert not replays(record, decide(weak_effect(), BASE))


def test_the_record_is_immutable_in_memory_too(outcome: CausalResult) -> None:
    record = append(None, decide(outcome, BASE), outcome, recorded_at=NOW)
    with pytest.raises(ValidationError):
        record.executed = True  # type: ignore[misc]


def test_records_sealed_before_the_model_field_existed_still_verify() -> None:
    """A contract may grow; the records already in a ledger must keep verifying."""
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "ledger_chain_before_model_ref.json").read_text()
    )
    records = [DecisionRecord.model_validate(r) for r in raw["records"]]
    assert all(r.evidence.model is None for r in records)
    report = verify_chain(records)
    assert report.ok, report.reason


def test_the_model_a_decision_rested_on_is_part_of_what_the_digest_covers(
    outcome: CausalResult,
) -> None:
    from causal_decision.assumptions import ModelRef

    ref = ModelRef(name="incident-remediation", version=3, digest="sha256:" + "a" * 64)
    with_model = append(None, decide(outcome, BASE), outcome, recorded_at=NOW, model=ref)
    without = append(None, decide(outcome, BASE), outcome, recorded_at=NOW)
    assert with_model.evidence.model == ref
    assert with_model.entry_digest != without.entry_digest
    forged = with_model.model_copy(
        update={"evidence": with_model.evidence.model_copy(update={"model": None})}
    )
    assert not verify_chain([forged]).ok, "dropping the model reference must break the seal"

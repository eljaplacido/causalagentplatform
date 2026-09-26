"""
A human decision is merged beside the Guardian's verdict, never over it.

GET /api/v1/incidents/ and /{id} join intervention decisions from audit_log
(INTERVENTION_APPROVED / INTERVENTION_REJECTED) onto the stored
proposed_interventions. _merge_decisions used to write the human decision into
guardianVerdict, so after a reload the screen showed a person's decision as if
the Guardian had issued it.

Preconditions: an IncidentRecord whose proposed_interventions carry the
Guardian's verdict as session.py _persist_incident stores it (guardianVerdict),
and a {intervention_id: "APPROVED" | "REJECTED"} map of decisions of record.

Postconditions:
  - a decided intervention gains humanDecision equal to the decision;
  - its guardianVerdict is exactly what was stored (or still absent);
  - an undecided intervention is unchanged and has no humanDecision;
  - the input record is not mutated.

Undefined, not asserted: the order of keys within an intervention dict.
"""

from __future__ import annotations

from src.api.incidents import IncidentRecord, _merge_decisions


def _record(interventions: list[dict[str, object]]) -> IncidentRecord:
    return IncidentRecord(
        id="INC-1a2b3c4d",
        title="Checkout latency spike after deploy",
        severity="P1",
        status="ACTIVE",
        proposed_interventions=interventions,
        submitted_by="dev-user-001",
        created_at="2026-09-18T10:00:00+00:00",
        updated_at="2026-09-18T10:00:00+00:00",
    )


def test_a_human_decision_does_not_overwrite_the_guardian_verdict() -> None:
    record = _record([
        {"id": "int-1-s", "title": "Roll back", "guardianVerdict": "REQUIRES_ESCALATION"},
        {"id": "int-2-s", "title": "Scale out", "guardianVerdict": "REQUIRES_ESCALATION"},
    ])

    merged = _merge_decisions(record, {"int-1-s": "APPROVED"})

    decided, undecided = merged.proposed_interventions
    assert decided["guardianVerdict"] == "REQUIRES_ESCALATION"
    assert decided["humanDecision"] == "APPROVED"
    assert undecided == {"id": "int-2-s", "title": "Scale out", "guardianVerdict": "REQUIRES_ESCALATION"}
    # The stored record is left as it was.
    assert "humanDecision" not in record.proposed_interventions[0]


def test_a_rejection_without_a_stored_verdict_supplies_no_verdict() -> None:
    record = _record([{"id": "iv-3"}])

    merged = _merge_decisions(record, {"iv-3": "REJECTED"})

    (iv,) = merged.proposed_interventions
    assert iv["humanDecision"] == "REJECTED"
    assert "guardianVerdict" not in iv

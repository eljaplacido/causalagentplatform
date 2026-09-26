"""
A Guardian verdict authorises exactly one write.

This is the test the register's `guardian-verdict-binding` entry named. Before it,
a verdict was a row id and `_assert_verdict_approved` asked two questions: does the
row exist, and does it say APPROVED. Three things followed, and each has a test here:

  * **Replay** -- one APPROVED row authorised unlimited unrelated writes.
  * **Forgery** -- anything that could INSERT into audit_log could mint authority,
    including the interventions API, which writes APPROVED rows under no role
    requirement at all.
  * **No binding** -- nothing tied a verdict to the object it was issued for.

Each of these is a way in for anything that reaches the database or that endpoint,
so each gets an explicit "is refused" test rather than a comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.types import TEXT_ARRAY, TIMESTAMP, json_value, typed_text
from src.exceptions import GuardianBlockedError, InvalidTransitionError
from src.semantic_layer.models import CynefinDomain, EpistemicState, UserRef
from src.semantic_layer.verdict import claim as verdict_claim
from src.semantic_layer.verdict import sign as verdict_sign

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.agents.governance import GovernanceAgent
    from src.semantic_layer.registry import SemanticLayerRegistry
from tests.conftest import (
    VerdictFactory,
    insert_raw_audit_entry,
    make_causal_entity,
    make_graph_fragment,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]


# ── Binding: one verdict, one object ─────────────────────────────────────────


async def test_verdict_issued_for_one_object_is_refused_for_another(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    """A verdict for object A must not authorise a write to object B."""
    intended = make_causal_entity()
    other = make_causal_entity()
    verdict = await verdict_for(intended.id)

    with pytest.raises(GuardianBlockedError, match="issued for object"):
        await registry.propose_draft(other, verdict)

    # ...and the verdict still works for what it was issued for: the refusal above
    # was about binding, not about having spent it.
    assert await registry.propose_draft(intended, verdict) == intended.id


async def test_verdict_naming_no_subject_authorises_nothing(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
) -> None:
    """A signed verdict with no subject is a record, not an authorisation.

    The Guardian issues these whenever a checked payload names no object -- they
    belong in the audit trail, but there is no write they could authorise.
    """
    entity = make_causal_entity()
    created_at = "2026-09-21T00:00:00+00:00"
    verdict_id = uuid4()
    signature = verdict_sign(
        verdict_claim(
            verdict_id=verdict_id,
            subject_id=None,
            payload_hash="deadbeef",
            verdict="APPROVED",
            action_type="TEST",
            agent_id="test-agent",
            created_at=created_at,
        )
    )
    await db_session.execute(
        typed_text(
            "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
            " guardian_verdict, policy_rules_cited, session_id, created_at, "
            " subject_id, signature) "
            "VALUES (:id, 'test-agent', 'TEST', 'deadbeef', 'APPROVED', :rules, NULL, "
            " :ts, NULL, :sig)",
            rules=TEXT_ARRAY,
            ts=TIMESTAMP,
        ),
        {"id": str(verdict_id), "rules": [], "ts": created_at, "sig": signature},
    )
    await db_session.commit()

    with pytest.raises(GuardianBlockedError, match="names no subject"):
        await registry.propose_draft(entity, verdict_id)


# ── Single use ───────────────────────────────────────────────────────────────


async def test_verdict_is_refused_on_second_use(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    """Two writes need two verdicts, even to the same object.

    The second write is the same draft again: a promotion would now be refused
    earlier, on the action the verdict was issued for (septa-60,
    tests/guardian/test_approve_action_binding.py pins single use for approvals).
    """
    entity = make_causal_entity()
    verdict = await verdict_for(entity.id)

    await registry.propose_draft(entity, verdict)

    with pytest.raises(GuardianBlockedError, match="already been used"):
        await registry.propose_draft(entity, verdict)


async def test_a_write_that_fails_does_not_burn_the_verdict(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    """Consumption is in the write's own transaction, so a failed write rolls it back.

    Otherwise a transient database error would cost the caller their verdict and
    force them back through the Guardian for a write that never happened.
    """
    entity = make_causal_entity()
    verdict = await verdict_for(entity.id)

    with pytest.raises(ValueError, match="unknown object_type"):
        await registry.promote_to_approved(
            object_id=entity.id,
            object_type="NotAType",
            verdict_id=verdict,
            approver=_approver(),
        )

    assert await registry.propose_draft(entity, verdict) == entity.id


# ── Forgery ──────────────────────────────────────────────────────────────────


async def test_an_unsigned_audit_row_cannot_authorise_a_write(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
) -> None:
    """The defect exactly: INSERT an APPROVED row, try to spend it."""
    entity = make_causal_entity()
    forged = await insert_raw_audit_entry(db_session, subject_id=entity.id)

    with pytest.raises(GuardianBlockedError, match="not signed by the issuer"):
        await registry.propose_draft(entity, forged)


async def test_a_row_signed_with_the_wrong_key_cannot_authorise_a_write(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
) -> None:
    """Knowing the format is not enough; the signature must be the issuer's."""
    entity = make_causal_entity()
    created_at = "2026-09-21T00:00:00+00:00"
    verdict_id = uuid4()
    statement = verdict_claim(
        verdict_id=verdict_id,
        subject_id=entity.id,
        payload_hash="deadbeef",
        verdict="APPROVED",
        action_type="TEST",
        agent_id="attacker",
        created_at=created_at,
    )
    forged = await insert_raw_audit_entry(
        db_session,
        subject_id=entity.id,
        signature=verdict_sign(statement, key=b"not-the-issuers-key"),
    )

    with pytest.raises(GuardianBlockedError, match="not signed by the issuer"):
        await registry.propose_draft(entity, forged)


async def test_an_intervention_style_approval_cannot_authorise_a_semantic_write(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
) -> None:
    """A human approving an intervention must not thereby authorise graph writes.

    `/api/v1/interventions/{id}/approve` hand-writes an APPROVED audit_log row and
    requires no governance role. Under the old gate that row was indistinguishable
    from a Guardian verdict and would have authorised any semantic write.
    """
    entity = make_causal_entity()
    minted = await insert_raw_audit_entry(
        db_session,
        verdict="APPROVED",
        agent_id="api-user-anyone",
        action_type="INTERVENTION_APPROVED",
        subject_id=entity.id,
    )

    with pytest.raises(GuardianBlockedError):
        await registry.propose_draft(entity, minted)


# ── The verdict the Guardian itself issues ───────────────────────────────────


async def test_a_verdict_the_guardian_issued_is_accepted(
    db_engine: object,
    registry: SemanticLayerRegistry,
    governance: GovernanceAgent,
    db_session: AsyncSession,
) -> None:
    """End to end through the real Guardian: no test-only signing path involved.

    Guards against the binding tests above passing only because the fixture and the
    verifier agree with each other while the Guardian does something else.
    """
    entity = make_causal_entity()
    state = EpistemicState(
        session_id="binding-test",
        agent_id="test-agent",
        cynefin_domain=CynefinDomain.COMPLICATED,
    )
    result = await governance.check(
        {"action_type": "PROPOSE_DRAFT", "subject_id": str(entity.id)}, state
    )
    assert str(result.verdict) == "APPROVED", result.reason

    assert await registry.propose_draft(entity, result.audit_entry_id) == entity.id


async def test_consumption_is_recorded_against_the_object(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    """Spending a verdict leaves a row saying which object spent it."""
    entity = make_causal_entity()
    verdict = await verdict_for(entity.id)
    await registry.propose_draft(entity, verdict)

    row = await db_session.execute(
        text("SELECT subject_id, context FROM verdict_consumption WHERE verdict_id = :v"),
        {"v": str(verdict)},
    )
    record = row.mappings().one_or_none()
    assert record is not None, "a spent verdict must be recorded as spent"
    assert str(record["subject_id"]) == str(entity.id)
    assert "propose_draft" in record["context"]



# ── A valid verdict does not make an invalid transition valid ────────────────


async def test_a_rejected_object_cannot_be_promoted_by_a_fresh_verdict(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    """DEPRECATED is terminal. promote_to_approved used to match on id alone."""
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    await registry.reject_draft(
        entity.id,
        "CausalEntity",
        await verdict_for(entity.id, "REJECTED"),
        _approver(),
        "not supported by the evidence",
    )

    fresh = await verdict_for(entity.id, action_type="GOVERNANCE_APPROVE")
    with pytest.raises(InvalidTransitionError, match="is DEPRECATED"):
        await registry.promote_to_approved(entity.id, "CausalEntity", fresh, _approver())

    status = await db_session.execute(
        text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": str(entity.id)}
    )
    assert status.scalar_one() == "DEPRECATED"
    # The refusal rolled back the spend: the verdict was never used for anything.
    spent = await db_session.execute(
        text("SELECT COUNT(*) FROM verdict_consumption WHERE verdict_id = :v"), {"v": str(fresh)}
    )
    assert spent.scalar_one() == 0


async def test_an_approved_object_cannot_be_rejected_after_the_fact(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    """reject_draft's guarded UPDATE matched nothing here, yet reported success."""
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    await registry.promote_to_approved(
        entity.id,
        "CausalEntity",
        await verdict_for(entity.id, action_type="GOVERNANCE_APPROVE"),
        _approver(),
    )

    with pytest.raises(InvalidTransitionError, match="is APPROVED"):
        await registry.reject_draft(
            entity.id,
            "CausalEntity",
            await verdict_for(entity.id, "REJECTED"),
            _approver(),
            "changed my mind",
        )
    status = await db_session.execute(
        text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": str(entity.id)}
    )
    assert status.scalar_one() == "APPROVED"

def _approver() -> UserRef:
    return UserRef(id="governance-officer", email="go@test.com", name="Officer")


async def test_a_graph_fragment_can_be_approved_and_its_lineage_is_kept(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    """graph_fragments had no lineage column, so this path raised on its first SELECT."""
    fragment = make_graph_fragment()
    await registry.propose_draft(fragment, await verdict_for(fragment.id))
    await registry.promote_to_approved(
        fragment.id,
        "GraphFragment",
        await verdict_for(fragment.id, action_type="GOVERNANCE_APPROVE"),
        _approver(),
    )
    row = await db_session.execute(
        text("SELECT approval_status, lineage FROM graph_fragments WHERE id = :i"),
        {"i": str(fragment.id)},
    )
    status, lineage = row.one()
    assert status == "APPROVED"
    assert [step["step_type"] for step in json_value(lineage)] == ["APPROVED"]


async def test_a_graph_fragment_can_be_rejected(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    fragment = make_graph_fragment()
    await registry.propose_draft(fragment, await verdict_for(fragment.id))
    await registry.reject_draft(
        fragment.id, "GraphFragment", await verdict_for(fragment.id, "REJECTED"), _approver(),
        "the fragment omits the load confounder",
    )
    row = await db_session.execute(
        text("SELECT approval_status FROM graph_fragments WHERE id = :i"), {"i": str(fragment.id)}
    )
    assert row.scalar_one() == "DEPRECATED"

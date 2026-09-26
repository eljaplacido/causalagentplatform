"""
/reject validates the verdict it is handed.

The register's `governance-reject-verdict-unchecked` entry: `RejectRequest` has
always *required* `verdict_id`, described as "ID of the Guardian REJECTED
audit_log entry", and `reject_object` never read it. Any UUID was accepted.

What the endpoint did instead was run a *fresh* Guardian check to manufacture an
audit row, with a comment saying that check "will return APPROVED since there's
nothing blocking a rejection audit". It did not: the state was unclassified, so
PR-006 rejected it, and the row said REJECTED. The comment was wrong and the
result was right, which is the least useful combination to inherit.

What authorises a rejection (septa-53). The fix above checked the verdict against
``expected=REJECTED``: a human could reject only while the Guardian *declined* the
reject request -- in practice only while /check read it as DISORDER (PR-006), since
the page sent no domain. A classified reject request came back APPROVED and was
refused. The authority to reject depended on the Guardian refusing to classify.

Now rejecting is authorised the way approving is: an APPROVED verdict, signed,
bound to this object, single-use -- and issued for the action ``GOVERNANCE_REJECT``.
The action is in the signed claim, so an approval's verdict cannot be spent on a
rejection, nor a rejection's on an approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.db.types import json_value
from tests.conftest import insert_raw_audit_entry, issue_verdict_for, make_causal_entity

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.models import CausalEntity

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {
    "uid": "governance-test-001",
    "email": "governance@test.com",
    "name": "Governance Test User",
    "roles": ["governance_officer", "platform_engineer"],
}


@pytest_asyncio.fixture
async def gov_client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer governance-test-token"},
        ) as ac:
            yield ac


async def _seed_draft(session: AsyncSession, entity: CausalEntity) -> None:
    await session.execute(
        text(
            "INSERT INTO causal_entities (id, name, entity_type, domain, description, "
            " attributes, ontology_version, approval_status, created_by, created_at) "
            "VALUES (:id, :name, 'SERVICE', :domain, '', '{}', '1.0.0', 'DRAFT', "
            " '{\"id\": \"test-user\"}', '2026-09-21T00:00:00+00:00')"
        ),
        {"id": str(entity.id), "name": entity.name, "domain": entity.domain},
    )
    await session.commit()


async def _reject(
    client: AsyncClient, object_id: UUID, verdict_id: UUID, reason: str = "not supported by data"
) -> Any:
    return await client.post(
        f"/api/governance/reject/{object_id}",
        json={
            "object_type": "CausalEntity",
            "reason": reason,
            "verdict_id": str(verdict_id),
        },
    )


async def test_an_unknown_verdict_is_refused(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The defect exactly: a UUID nobody issued used to be accepted."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)

    resp = await _reject(gov_client, entity.id, uuid4())
    assert resp.status_code == 403
    assert "not found" in resp.json()["detail"]

    row = await db_session.execute(
        text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": str(entity.id)}
    )
    assert row.scalar() == "DRAFT", "a refused rejection must not change the object"


async def _status(session: AsyncSession, object_id: UUID) -> str:
    row = await session.execute(
        text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": str(object_id)}
    )
    return str(row.scalar())


async def test_an_approve_verdict_cannot_be_used_to_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """A Guardian ruling on *approving* this object is not a ruling on rejecting it."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    approved = await issue_verdict_for(
        db_session, entity.id, verdict="APPROVED", action_type="GOVERNANCE_APPROVE"
    )

    resp = await _reject(gov_client, entity.id, approved)
    assert resp.status_code == 403
    assert "GOVERNANCE_REJECT" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"


async def test_a_verdict_for_some_other_action_cannot_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Any action other than GOVERNANCE_REJECT is refused, not only the approval one."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    other = await issue_verdict_for(db_session, entity.id, verdict="APPROVED")  # TEST_FIXTURE

    resp = await _reject(gov_client, entity.id, other)
    assert resp.status_code == 403
    assert "GOVERNANCE_REJECT" in resp.json()["detail"]


async def test_a_rejected_verdict_can_no_longer_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The old authority, retired: a Guardian that *declined* the request authorises nothing."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    refused = await issue_verdict_for(
        db_session, entity.id, verdict="REJECTED", action_type="GOVERNANCE_REJECT"
    )

    resp = await _reject(gov_client, entity.id, refused)
    assert resp.status_code == 403
    assert "not APPROVED" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"


async def test_a_verdict_for_another_object_cannot_reject_this_one(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    other = make_causal_entity()
    await _seed_draft(db_session, entity)
    elsewhere = await issue_verdict_for(
        db_session, other.id, verdict="APPROVED", action_type="GOVERNANCE_REJECT"
    )

    resp = await _reject(gov_client, entity.id, elsewhere)
    assert resp.status_code == 403
    assert "issued for object" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"


async def test_a_reject_verdict_cannot_be_used_to_approve(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The other direction: a ruling that this object may be rejected does not approve it."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    reject_verdict = await issue_verdict_for(
        db_session, entity.id, verdict="APPROVED", action_type="GOVERNANCE_REJECT"
    )

    resp = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": str(reject_verdict)},
    )
    assert resp.status_code == 403
    assert "GOVERNANCE_REJECT" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"

    # Refused before it was spent: the same verdict still rejects, once.
    ok = await _reject(gov_client, entity.id, reject_verdict)
    assert ok.status_code == 200, ok.text


async def test_an_unsigned_rejection_row_cannot_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """A row inserted directly is history, not a ruling -- same as approval."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    forged = await insert_raw_audit_entry(
        db_session, verdict="APPROVED", subject_id=entity.id, action_type="GOVERNANCE_REJECT"
    )

    resp = await _reject(gov_client, entity.id, forged)
    assert resp.status_code == 403
    assert "not signed by the issuer" in resp.json()["detail"]


async def test_a_classified_reject_verdict_rejects_the_object_once(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The flow working, and refusing to work twice on the same verdict."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = await issue_verdict_for(
        db_session, entity.id, verdict="APPROVED", action_type="GOVERNANCE_REJECT"
    )

    resp = await _reject(gov_client, entity.id, verdict)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    row = await db_session.execute(
        text("SELECT approval_status, lineage FROM causal_entities WHERE id = :i"),
        {"i": str(entity.id)},
    )
    record = row.mappings().one()
    # ApprovalStatus has no REJECTED member and the column's CHECK constraint does
    # not allow one, so a rejected draft is DEPRECATED. The lineage says why.
    assert record["approval_status"] == "DEPRECATED"
    lineage = json_value(record["lineage"])
    assert lineage[-1]["step_type"] == "REJECTED"
    assert "not supported by data" in lineage[-1]["description"]

    again = await _reject(gov_client, entity.id, verdict)
    assert again.status_code == 403
    assert "already been used" in again.json()["detail"]


async def test_the_governance_pages_own_requests_reject_an_object(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The exact bodies frontend/src/app/governance/page.tsx sends, end to end.

    The page's tests mock /check. This sends what the page sends -- the reviewer's
    classification included -- through the real /check and the real /reject.
    """
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    reason = "The edge contradicts the load test; confounded by deploy time."

    check = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "GOVERNANCE_REJECT",
            "payload": {
                "object_id": str(entity.id),
                "object_type": "CausalEntity",
                "reason": reason,
            },
            "subject_id": str(entity.id),
            "cynefin_domain": "COMPLICATED",
        },
    )
    assert check.status_code == 200, check.text
    assert check.json()["verdict"] == "APPROVED", check.json()["reason"]

    resp = await gov_client.post(
        f"/api/governance/reject/{entity.id}",
        json={
            "object_type": "CausalEntity",
            "reason": reason,
            "verdict_id": check.json()["audit_entry_id"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert await _status(db_session, entity.id) == "DEPRECATED"


async def test_the_pages_old_unclassified_reject_request_no_longer_rejects(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """What the page sent before septa-53 -- no domain -- used to be the only way that worked.

    /check reads it as DISORDER and PR-006 refuses it. That REJECTED verdict was
    what /reject demanded; now it authorises nothing.
    """
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    reason = "The edge contradicts the load test; confounded by deploy time."

    check = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "GOVERNANCE_REJECT",
            "payload": {
                "object_id": str(entity.id),
                "object_type": "CausalEntity",
                "reason": reason,
            },
            "subject_id": str(entity.id),
        },
    )
    assert check.status_code == 200, check.text
    assert check.json()["verdict"] == "REJECTED"
    assert check.json()["policy_rules_cited"] == ["PR-006"]

    resp = await gov_client.post(
        f"/api/governance/reject/{entity.id}",
        json={
            "object_type": "CausalEntity",
            "reason": reason,
            "verdict_id": check.json()["audit_entry_id"],
        },
    )
    assert resp.status_code == 403
    assert "not APPROVED" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"

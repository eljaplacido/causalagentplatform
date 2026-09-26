"""
What a person said when they decided is readable afterwards.

The register's `governance-reasons-not-stored` entry: `/approve` accepted `notes`
and dropped them, and `/reject` required a `reason` of at least ten characters
and put it only inside a SHA-256 payload hash -- from which no one can read it
back. The endpoint's own docstring said "The reason is stored in the audit trail".

An audit trail that records that a decision happened, but not why, cannot answer
the question anyone actually asks of it later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from tests.conftest import issue_verdict_for, make_causal_entity

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

APPROVAL_NOTES = "Reviewed against the incident postmortem; the edge matches what we saw."
REJECTION_REASON = "The proposed edge contradicts the randomised rollout from March."


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


async def _entry(client: AsyncClient, entry_id: str) -> dict[str, Any]:
    resp = await client.get("/api/governance/audit-log", params={"page_size": 200})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    match = [e for e in entries if e.get("id") == entry_id]
    assert match, f"audit entry {entry_id} not returned by /audit-log"
    return match[0]


async def test_approval_notes_are_readable_from_the_audit_log(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = await issue_verdict_for(db_session, entity.id, action_type="GOVERNANCE_APPROVE")

    resp = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={
            "object_type": "CausalEntity",
            "verdict_id": str(verdict),
            "notes": APPROVAL_NOTES,
        },
    )
    assert resp.status_code == 200, resp.text

    entry = await _entry(gov_client, resp.json()["audit_entry_id"])
    assert entry["notes"] == APPROVAL_NOTES
    assert entry["actionType"] == "HUMAN_APPROVAL"
    assert entry["subjectId"] == str(entity.id)


async def test_the_rejection_reason_is_readable_from_the_audit_log(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = await issue_verdict_for(db_session, entity.id, verdict="REJECTED")

    resp = await gov_client.post(
        f"/api/governance/reject/{entity.id}",
        json={
            "object_type": "CausalEntity",
            "reason": REJECTION_REASON,
            "verdict_id": str(verdict),
        },
    )
    assert resp.status_code == 200, resp.text

    entry = await _entry(gov_client, resp.json()["audit_entry_id"])
    assert entry["notes"] == REJECTION_REASON
    assert entry["actionType"] == "HUMAN_REJECTION"
    assert entry["guardianVerdict"] == "REJECTED"


async def test_a_decision_record_cannot_be_spent_as_authority(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The record of an approval must not itself authorise the next write.

    It carries `guardian_verdict = APPROVED` and names the object, so the only
    thing standing between it and a forged write is that it is written unsigned.
    """
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = await issue_verdict_for(db_session, entity.id, action_type="GOVERNANCE_APPROVE")
    approve = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": str(verdict), "notes": APPROVAL_NOTES},
    )
    record_id = approve.json()["audit_entry_id"]

    row = await db_session.execute(
        text("SELECT signature FROM audit_log WHERE id = :i"), {"i": record_id}
    )
    assert row.scalar() is None, "a decision record must be unsigned"

    from src.exceptions import GuardianBlockedError
    from src.semantic_layer.registry import SemanticLayerRegistry

    with pytest.raises(GuardianBlockedError, match="not signed by the issuer"):
        await SemanticLayerRegistry().promote_to_approved(
            object_id=entity.id,
            object_type="CausalEntity",
            verdict_id=UUID(record_id),
            approver=_approver(),
        )


def _approver() -> Any:
    from src.semantic_layer.models import UserRef

    return UserRef(id="governance-test-001", email="governance@test.com", name="Officer")

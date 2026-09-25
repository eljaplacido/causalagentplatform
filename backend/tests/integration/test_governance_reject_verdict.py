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


async def test_an_approved_verdict_cannot_be_used_to_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Rejecting on the strength of an approval is not a rejection."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    approved = await issue_verdict_for(db_session, entity.id, verdict="APPROVED")

    resp = await _reject(gov_client, entity.id, approved)
    assert resp.status_code == 403
    assert "not REJECTED" in resp.json()["detail"]


async def test_a_verdict_for_another_object_cannot_reject_this_one(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    other = make_causal_entity()
    await _seed_draft(db_session, entity)
    elsewhere = await issue_verdict_for(db_session, other.id, verdict="REJECTED")

    resp = await _reject(gov_client, entity.id, elsewhere)
    assert resp.status_code == 403
    assert "issued for object" in resp.json()["detail"]


async def test_an_unsigned_rejection_row_cannot_reject(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """A REJECTED row inserted directly is history, not a ruling -- same as approval."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    forged = await insert_raw_audit_entry(db_session, verdict="REJECTED", subject_id=entity.id)

    resp = await _reject(gov_client, entity.id, forged)
    assert resp.status_code == 403
    assert "not signed by the issuer" in resp.json()["detail"]


async def test_a_real_rejected_verdict_rejects_the_object_once(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The flow working, and refusing to work twice on the same verdict."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = await issue_verdict_for(db_session, entity.id, verdict="REJECTED")

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

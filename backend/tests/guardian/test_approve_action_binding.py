"""
Approving an object needs a verdict issued for that decision: GOVERNANCE_APPROVE.

septa-60. `_assert_verdict_approved` checked that a verdict exists, says APPROVED,
is signed by the Guardian, names this object and is unspent -- but not which action
the Guardian was asked about. The action is in the signed claim
(`verdict.claim(action_type=...)`), so it cannot be relabelled; it simply went
unread. Any APPROVED verdict issued for the object -- a check for PROMOTE_OBJECT,
DRIFT_CHECK, or whatever a caller chose to name -- could therefore be spent on
promoting it to APPROVED.

Preconditions: a DRAFT object in its table, and a verdict signed by the Guardian's
key naming that object as its subject.

Postconditions (from the approve endpoint's contract and the register):
  * a verdict issued for any action other than GOVERNANCE_APPROVE does not approve
    the object; the refusal is a GuardianBlockedError (403 through the API), the
    object stays DRAFT, and the verdict is not spent;
  * a verdict issued for GOVERNANCE_APPROVE approves it;
  * that verdict is still single-use.

Undefined here: what a GOVERNANCE_APPROVE verdict may authorise besides a promotion
(new drafts, rejections) -- that is septa-53's ground and not asserted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.exceptions import GuardianBlockedError
from src.semantic_layer.models import UserRef
from tests.conftest import VerdictFactory, make_causal_entity

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.registry import SemanticLayerRegistry

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]

#: The action a /check is made for when the decision is an approval -- the value the
#: governance page sends (frontend/src/app/governance/page.tsx). Spelled out here
#: rather than imported, so the test pins the wire value, not a constant's name.
GOVERNANCE_APPROVE = "GOVERNANCE_APPROVE"


def _approver() -> UserRef:
    return UserRef(id="governance-officer", email="go@test.com", name="Officer")


async def _status(session: AsyncSession, object_id: UUID) -> str:
    row = await session.execute(
        text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": str(object_id)}
    )
    return str(row.scalar_one())


async def _spent(session: AsyncSession, verdict_id: UUID) -> int:
    row = await session.execute(
        text("SELECT COUNT(*) FROM verdict_consumption WHERE verdict_id = :v"),
        {"v": str(verdict_id)},
    )
    return int(row.scalar_one())


@pytest.mark.parametrize("other_action", ["PROMOTE_OBJECT", "DRIFT_CHECK", "TEST_FIXTURE"])
async def test_an_approved_verdict_for_another_action_cannot_approve(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    other_action: str,
) -> None:
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    verdict = await verdict_for(entity.id, action_type=other_action)

    with pytest.raises(GuardianBlockedError, match=f"issued for action {other_action}"):
        await registry.promote_to_approved(entity.id, "CausalEntity", verdict, _approver())

    assert await _status(db_session, entity.id) == "DRAFT"
    assert await _spent(db_session, verdict) == 0


async def test_a_governance_approve_verdict_approves_once(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    verdict = await verdict_for(entity.id, action_type=GOVERNANCE_APPROVE)

    await registry.promote_to_approved(entity.id, "CausalEntity", verdict, _approver())

    assert await _status(db_session, entity.id) == "APPROVED"
    assert await _spent(db_session, verdict) == 1

    # Single use survives the action binding: the same verdict is refused again.
    with pytest.raises(GuardianBlockedError, match="already been used"):
        await registry.promote_to_approved(entity.id, "CausalEntity", verdict, _approver())


async def test_a_spent_governance_approve_verdict_is_refused(
    db_engine: object,
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
) -> None:
    """The spend is what refuses a second use, not the object's new status."""
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    verdict = await verdict_for(entity.id, action_type=GOVERNANCE_APPROVE)
    await db_session.execute(
        text(
            "INSERT INTO verdict_consumption (verdict_id, subject_id, context, consumed_at) "
            "VALUES (:v, :s, 'earlier use', '2026-09-26T00:00:00+00:00')"
        ),
        {"v": str(verdict), "s": str(entity.id)},
    )
    await db_session.commit()

    with pytest.raises(GuardianBlockedError, match="already been used"):
        await registry.promote_to_approved(entity.id, "CausalEntity", verdict, _approver())
    assert await _status(db_session, entity.id) == "DRAFT"


# ── Through the API: /check for another action, then /approve ────────────────

_GOVERNANCE_USER: dict[str, Any] = {
    "uid": "governance-test-001",
    "email": "governance@test.com",
    "name": "Governance Test User",
    "roles": ["governance_officer", "platform_engineer"],
}


@pytest_asyncio.fixture
async def gov_client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    from src.api.main import app

    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_GOVERNANCE_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer governance-test-token"},
        ) as ac:
            yield ac


async def _seed_draft(session: AsyncSession, entity_id: UUID, name: str, domain: str) -> None:
    await session.execute(
        text(
            "INSERT INTO causal_entities (id, name, entity_type, domain, description, "
            " attributes, ontology_version, approval_status, created_by, created_at) "
            "VALUES (:id, :name, 'SERVICE', :domain, '', '{}', '1.0.0', 'DRAFT', "
            " '{\"id\": \"test-user\"}', '2026-09-21T00:00:00+00:00')"
        ),
        {"id": str(entity_id), "name": name, "domain": domain},
    )
    await session.commit()


async def _check(client: AsyncClient, subject_id: UUID, action_type: str) -> dict[str, Any]:
    resp = await client.post(
        "/api/governance/check",
        json={
            "action_type": action_type,
            "payload": {"object_id": str(subject_id), "object_type": "CausalEntity"},
            "subject_id": str(subject_id),
            "cynefin_domain": "COMPLICATED",
        },
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["verdict"] == "APPROVED", body["reason"]
    return body


async def test_a_check_for_another_action_cannot_approve_through_the_api(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    await _seed_draft(db_session, entity.id, entity.name, entity.domain)
    verdict = (await _check(gov_client, entity.id, "PROMOTE_OBJECT"))["audit_entry_id"]

    resp = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert resp.status_code == 403, resp.text
    assert "issued for action PROMOTE_OBJECT" in resp.json()["detail"]
    assert await _status(db_session, entity.id) == "DRAFT"


async def test_a_governance_approve_check_approves_through_the_api(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    entity = make_causal_entity()
    await _seed_draft(db_session, entity.id, entity.name, entity.domain)
    verdict = (await _check(gov_client, entity.id, GOVERNANCE_APPROVE))["audit_entry_id"]

    resp = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert resp.status_code == 200, resp.text
    assert await _status(db_session, entity.id) == "APPROVED"

"""
The approval flow, end to end through the API.

This is the test the register's `guardian-check-always-rejects` entry named.
`/check` built its `EpistemicState` with no Cynefin domain, the field defaults to
DISORDER, and rule PR-006 rejects everything in DISORDER -- so `/check` returned
REJECTED for every input it was ever given, and `/approve`, which needs an APPROVED
verdict from `/check`, could never be satisfied through the API at all.

The existing `/check` tests all passed anyway, because each asserted the verdict was
*one of* the three values, or that a "high risk" payload was refused -- which it was,
for the wrong reason. A test that cannot tell the endpoint working from the endpoint
being broken is why this one asserts the specific verdict and the specific rule.
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
from tests.conftest import make_causal_entity

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.models import CausalEntity

pytestmark = pytest.mark.asyncio

_MOCK_GOVERNANCE_USER: dict[str, Any] = {
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
        return_value=_MOCK_GOVERNANCE_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer governance-test-token"},
        ) as ac:
            yield ac


async def _check(
    client: AsyncClient,
    subject_id: UUID,
    cynefin_domain: str = "COMPLICATED",
    hallucination_risk_score: float = 0.0,
    cycle_count: int = 0,
) -> dict[str, Any]:
    resp = await client.post(
        "/api/governance/check",
        json={
            "action_type": "PROMOTE_OBJECT",
            "payload": {"object_type": "CausalEntity"},
            "subject_id": str(subject_id),
            "cynefin_domain": cynefin_domain,
            "hallucination_risk_score": hallucination_risk_score,
            "cycle_count": cycle_count,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _seed_draft(session: AsyncSession, entity: CausalEntity) -> None:
    """Put a DRAFT entity in the table directly: this test is about the approval path."""
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


# ── /check can now reach APPROVED ────────────────────────────────────────────


async def test_a_classified_compliant_payload_is_approved(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """The verdict is APPROVED, and no rule fired. Both halves matter."""
    body = await _check(gov_client, uuid4())
    assert body["verdict"] == "APPROVED", body["reason"]
    assert body["policy_rules_cited"] == []
    assert body["audit_entry_id"]


async def test_an_unclassified_situation_is_still_refused(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Omitting the domain still means DISORDER, and PR-006 still rejects it.

    The fix was to let a caller state the domain, not to stop requiring one.
    """
    resp = await gov_client.post(
        "/api/governance/check",
        json={"action_type": "PROMOTE_OBJECT", "payload": {}, "subject_id": str(uuid4())},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "REJECTED"
    assert body["policy_rules_cited"] == ["PR-006"]


async def test_the_rules_now_see_what_the_caller_reports(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """A classified situation with a high self-reported hallucination risk hits PR-002.

    Previously every one of these was rejected by PR-006 before any other rule was
    consulted, so no test could tell whether PR-002 worked at all.
    """
    body = await _check(gov_client, uuid4(), hallucination_risk_score=0.95)
    assert body["verdict"] == "REJECTED"
    assert body["policy_rules_cited"] == ["PR-002"]

    escalated = await _check(gov_client, uuid4(), cycle_count=5)
    assert escalated["verdict"] == "REQUIRES_ESCALATION"
    assert escalated["policy_rules_cited"] == ["PR-005"]

    chaotic = await _check(gov_client, uuid4(), cynefin_domain="CHAOTIC")
    assert chaotic["verdict"] == "REQUIRES_ESCALATION"
    assert chaotic["policy_rules_cited"] == ["PR-003"]


# ── /check → /approve, the whole way ─────────────────────────────────────────


async def test_check_then_approve_promotes_the_object(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The flow the API documents, working: check, then approve with that verdict."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)

    verdict = (await _check(gov_client, entity.id))["audit_entry_id"]

    resp = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    row = await db_session.execute(
        text("SELECT approval_status, approved_by FROM causal_entities WHERE id = :i"),
        {"i": str(entity.id)},
    )
    record = row.mappings().one()
    assert record["approval_status"] == "APPROVED"
    assert "governance-test-001" in str(record["approved_by"])


async def test_the_same_verdict_cannot_approve_twice(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Replay through the API, refused with a 403 rather than a second promotion."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    verdict = (await _check(gov_client, entity.id))["audit_entry_id"]

    first = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert first.status_code == 200

    second = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert second.status_code == 403
    assert "already been used" in second.json()["detail"]


async def test_a_verdict_for_another_object_cannot_approve_this_one(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The check was done for something else, so it authorises nothing here."""
    target = make_causal_entity()
    elsewhere = make_causal_entity()
    await _seed_draft(db_session, target)
    verdict = (await _check(gov_client, elsewhere.id))["audit_entry_id"]

    resp = await gov_client.post(
        f"/api/governance/approve/{target.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert resp.status_code == 403
    assert "issued for object" in resp.json()["detail"]


async def test_a_rejected_check_cannot_approve(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """An unclassified check yields a REJECTED verdict, which /approve must refuse."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "PROMOTE_OBJECT",
            "payload": {},
            "subject_id": str(entity.id),
        },
    )
    verdict = resp.json()["audit_entry_id"]

    approve = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": verdict},
    )
    assert approve.status_code == 403
    assert "not APPROVED" in approve.json()["detail"]


async def test_a_check_that_named_no_subject_cannot_approve_anything(
    gov_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Approving needs a check done *for that object*, not merely a passing check."""
    entity = make_causal_entity()
    await _seed_draft(db_session, entity)
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "PROMOTE_OBJECT",
            "payload": {},
            "cynefin_domain": "COMPLICATED",
        },
    )
    assert resp.json()["verdict"] == "APPROVED"

    approve = await gov_client.post(
        f"/api/governance/approve/{entity.id}",
        json={"object_type": "CausalEntity", "verdict_id": resp.json()["audit_entry_id"]},
    )
    assert approve.status_code == 403
    assert "names no subject" in approve.json()["detail"]

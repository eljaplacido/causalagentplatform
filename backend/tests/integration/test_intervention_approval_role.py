"""
Deciding an intervention needs a governance role.

The register's `intervention-approval-no-role` entry, found while fixing verdict
binding. `POST /api/v1/interventions/{id}/approve` depended on `get_current_user`
and nothing else, and writes a row into audit_log saying `guardian_verdict =
APPROVED`. Any authenticated user could therefore record that an intervention had
been approved -- and, before verdict binding, mint a row the semantic-layer
registry would have accepted as authority for an unrelated write.

`docs/CYBERSECURITY.md` says "RBAC enforced at API layer, database layer, and
semantic layer". These tests are the API layer part, for this route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_NO_ROLE: dict[str, Any] = {
    "uid": "anyone-at-all",
    "email": "anyone@test.com",
    "name": "Any Authenticated User",
}
_SRE: dict[str, Any] = {
    "uid": "sre-001",
    "email": "sre@test.com",
    "name": "On-call SRE",
    "roles": ["sre"],
}


def _client_as(user: dict[str, Any]) -> Callable[..., Any]:
    """A pytest fixture that authenticates as `user`. One per role under test."""

    @pytest_asyncio.fixture
    async def _fixture(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
        with patch(
            "src.middleware.auth._verify_firebase_token",
            new_callable=AsyncMock,
            return_value=user,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test-token"},
            ) as ac:
                yield ac

    return _fixture


plain_client = _client_as(_NO_ROLE)
sre_client = _client_as(_SRE)


async def test_an_authenticated_user_without_a_role_cannot_approve(
    plain_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    resp = await plain_client.post(
        "/api/v1/interventions/intervention-1/approve",
        json={"reason": "looks fine to me", "incident_id": "inc-1"},
    )
    assert resp.status_code == 403

    count = await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))
    assert count.scalar() == 0, "a refused decision must not reach the audit log"


async def test_an_authenticated_user_without_a_role_cannot_reject(
    plain_client: AsyncClient, db_engine: object
) -> None:
    resp = await plain_client.post(
        "/api/v1/interventions/intervention-1/reject",
        json={"reason": "no", "incident_id": "inc-1"},
    )
    assert resp.status_code == 403


async def test_an_sre_can_decide(
    sre_client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The role exists to name who may decide, not to stop the work happening."""
    resp = await sre_client.post(
        "/api/v1/interventions/intervention-1/approve",
        json={"reason": "matches the runbook", "incident_id": "inc-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"] == "APPROVED"

    row = await db_session.execute(
        text("SELECT guardian_verdict, signature FROM audit_log WHERE id = :i"),
        {"i": resp.json()["audit_log_id"]},
    )
    record = row.mappings().one()
    assert record["guardian_verdict"] == "APPROVED"
    # And it is still not a Guardian ruling: unsigned, so it authorises no write.
    assert record["signature"] is None


async def test_reading_interventions_needs_a_platform_role(
    plain_client: AsyncClient, db_engine: object
) -> None:
    """Deciding needs a decision role; looking needs any platform role, a viewer's
    included. A token holding none read the decisions (reason, decided_by) until
    the reads took matrix rows."""
    resp = await plain_client.get("/api/v1/interventions/pending")
    assert resp.status_code == 403
    viewer = {**_NO_ROLE, "roles": ["viewer"]}
    with patch("src.middleware.auth._verify_firebase_token", new=AsyncMock(return_value=viewer)):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as client:
            resp = await client.get("/api/v1/interventions/pending")
    assert resp.status_code == 200

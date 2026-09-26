"""
POST /api/v1/session/run: the Cynefin domain is the platform's, not the caller's.

RunSessionRequest offered ``cynefin_domain``, "Override Cynefin domain. Omit to let
the agent classify." It overrode nothing: the graph's CynefinClassify node always
replaces the domain with its own classification, so a caller that sent CHAOTIC, or
COMPLICATED, got the same run as one that sent nothing -- and was told otherwise.

The parameter is removed rather than honoured. docs/AGENTS.md makes classification
the platform's ("Agents self-classify the domain complexity of a query before
selecting a reasoning strategy"), and the Guardian's design keeps the judged domain
out of the caller's hands: POST /api/governance/check takes a caller's domain only
when no session names one, and a session's own classification wins when it does. A
session run always has the platform's classification, so a caller's domain there
would always lose; accepting it would only say something false. As /check does for
the scores it retired, the field is refused loudly, so a client still sending it
learns that it does not count.

Preconditions: an authenticated caller with a platform role.

Postconditions:
  * a body carrying ``cynefin_domain`` (any value, null included) is refused with 422,
    naming the field, and no session runs (nothing reaches audit_log);
  * without it the run proceeds and the domain in the response is the classifier's:
    a description the keyword classifier cannot place is DISORDER, whatever the
    caller would have liked, and the Guardian's PR-006 judges that.

Undefined here: the classifier's quality (register cynefin-classifier-keyword-stub).
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
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "domain-caller", "email": "d@test.com", "roles": ["analyst"]}

#: No chaotic, complex or complicated keyword: the classifier says DISORDER.
_UNPLACEABLE = "Something about the checkout flow feels off this week."


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new=AsyncMock(return_value=_USER),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer domain-token"},
        ) as ac:
            yield ac


async def _audit_rows(db_session: AsyncSession) -> int:
    return int((await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar() or 0)


@pytest.mark.parametrize("declared", ["COMPLICATED", "CHAOTIC", "CLEAR", None])
async def test_a_caller_declared_domain_is_refused(
    client: AsyncClient, db_session: AsyncSession, declared: str | None
) -> None:
    before = await _audit_rows(db_session)
    resp = await client.post(
        "/api/v1/session/run",
        json={"description": _UNPLACEABLE, "cynefin_domain": declared},
    )
    assert resp.status_code == 422, resp.text
    assert "cynefin_domain" in resp.text
    assert await _audit_rows(db_session) == before, "a refused request ran a session"


async def test_the_domain_judged_is_the_classifiers(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/session/run", json={"description": _UNPLACEABLE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cynefin_domain"] == "DISORDER"
    assert body["guardian_verdict"] != "APPROVED"

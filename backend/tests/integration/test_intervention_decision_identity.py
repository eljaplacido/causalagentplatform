"""
An intervention's decision is found by its exact id, and only by it.

Before: the "already decided" lookup was ``payload_hash LIKE '%"intervention_id":
"<id>"%'`` with the path parameter pasted in unescaped. ``_`` and ``%`` are LIKE
wildcards and SQLite's LIKE ignores ASCII case, so approving ``iv_1`` after
``iv-1`` was rejected returned iv-1's REJECTED decision with
``already_decided=True`` and wrote nothing: the approval was lost.

Preconditions: an sre token (a decision role); the SQLite test engine, and the
PostgreSQL job runs the same file against the migrations.

Postconditions
  1. A decision on one id is never reported as another id's, whatever LIKE
     wildcards, case or JSON-special characters the ids hold.
  2. Deciding the same id twice still returns the first decision, and writes
     one audit row.

Undefined here: concurrent decisions on one id (a unique index is owed; ledgered).
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

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio

_SRE: dict[str, Any] = {"uid": "sre-1", "roles": ["sre"]}


@pytest_asyncio.fixture
async def sre(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch("src.middleware.auth._verify_firebase_token", new=AsyncMock(return_value=_SRE)):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as client:
            yield client


async def _decide(client: AsyncClient, iid: str, verb: str) -> dict[str, Any]:
    resp = await client.post(f"/api/v1/interventions/{iid}/{verb}", json={"reason": verb})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("iv-1", "iv_1"),  # _ matches any one character
        ("iv-1", "iv%"),  # % matches anything
        ("iv-1", "IV-1"),  # SQLite LIKE ignores ASCII case
        ('iv"1', "iv_1"),  # a JSON-escaped quote in the stored payload
    ],
)
async def test_a_decision_on_one_id_is_not_another_ids(
    sre: AsyncClient, first: str, second: str
) -> None:
    rejected = await _decide(sre, first, "reject")
    assert rejected["decision"] == "REJECTED" and not rejected["already_decided"]

    approved = await _decide(sre, second, "approve")
    assert approved["intervention_id"] == second
    assert approved["decision"] == "APPROVED", approved
    assert approved["already_decided"] is False
    assert approved["audit_log_id"] != rejected["audit_log_id"]


async def test_deciding_twice_returns_the_first_decision(
    sre: AsyncClient, db_engine: AsyncEngine
) -> None:
    first = await _decide(sre, "iv-twice", "reject")
    again = await _decide(sre, "iv-twice", "approve")
    assert again["already_decided"] is True
    assert again["decision"] == "REJECTED"
    assert again["audit_log_id"] == first["audit_log_id"]
    async with db_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit_log WHERE action_type IN "
                    "('INTERVENTION_APPROVED', 'INTERVENTION_REJECTED') "
                    "AND session_id = 'intervention-iv-twice'"
                )
            )
        ).scalar()
    assert n == 1

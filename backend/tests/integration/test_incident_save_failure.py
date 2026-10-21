"""
A triaged incident that could not be stored says so (septa-86).

Before: ``POST /api/v1/session/incident`` caught every exception from
``save_incident``, logged a warning and answered 200 with the triage result.
The dashboard showed the incident; after a reload it was gone, and nobody had
been told.

Preconditions: Firebase verification patched per test; the SQLite test engine,
and the PostgreSQL job runs the same file against the migrations.

Postconditions
  1. A stored incident answers ``stored: true`` and no ``store_error``.
  2. A save that raises answers 200 (the triage and its Guardian verdict are
     recorded either way) with ``stored: false`` and the reason, and no row.
  3. A save that ``save_incident`` refuses (another submitter's id, taken
     between the route's 409 check and the save) is ``stored: false`` too.

Undefined here: the exact wording of ``store_error`` beyond naming the cause.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def as_user(uid: str) -> AsyncIterator[AsyncClient]:
    user = {"uid": uid, "roles": ["sre"]}
    with patch("src.middleware.auth._verify_firebase_token", new=AsyncMock(return_value=user)):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as client:
            yield client


def _body(incident_id: str) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "title": "deployment cpu memory timeout latency degradation",
        "severity": "P2",
        "affected_services": ["payments-service"],
    }


async def _rows(engine: AsyncEngine, incident_id: str) -> int:
    async with engine.connect() as conn:
        count = await conn.execute(
            text("SELECT COUNT(*) FROM incidents WHERE id = :i"), {"i": incident_id}
        )
    return int(count.scalar() or 0)


async def test_a_stored_incident_says_it_was_stored(db_engine: AsyncEngine) -> None:
    iid = f"INC-{uuid4().hex[:8]}"
    async with as_user("alice") as client:
        resp = await client.post("/api/v1/session/incident", json=_body(iid))
    assert resp.status_code == 200, resp.text
    assert resp.json()["stored"] is True
    assert resp.json()["store_error"] is None
    assert await _rows(db_engine, iid) == 1


async def test_a_failed_save_is_said_not_swallowed(db_engine: AsyncEngine) -> None:
    iid = f"INC-{uuid4().hex[:8]}"
    failing = AsyncMock(side_effect=RuntimeError("database is locked"))
    with patch("src.api.incidents.save_incident", failing):
        async with as_user("alice") as client:
            resp = await client.post("/api/v1/session/incident", json=_body(iid))
    assert failing.await_count == 1
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["stored"] is False
    assert "database is locked" in payload["store_error"]
    assert payload["guardian_verdict"], "the triage result is still returned"
    assert await _rows(db_engine, iid) == 0


async def test_a_refused_save_is_not_reported_as_stored(db_engine: AsyncEngine) -> None:
    iid = f"INC-{uuid4().hex[:8]}"
    refused = AsyncMock(return_value=False)
    with patch("src.api.incidents.save_incident", refused):
        async with as_user("alice") as client:
            resp = await client.post("/api/v1/session/incident", json=_body(iid))
    assert resp.status_code == 200, resp.text
    assert resp.json()["stored"] is False
    assert "another submitter" in resp.json()["store_error"]

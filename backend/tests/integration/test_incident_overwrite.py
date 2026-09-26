"""
An incident is overwritten only by the user who submitted it.

Before: ``save_incident`` upserted on the id -- "latest payload wins" -- and
``POST /api/v1/session/incident`` is open to every role. The UI numbers
incidents ``INC-<count+1>``, so a failed list load, a list longer than its page,
or two users submitting at once sent an id already taken, and the second
submission replaced the first's title, severity, hypotheses, interventions and
``submitted_by`` without a word.

Preconditions: Firebase verification patched per test; the SQLite test engine,
and the PostgreSQL job runs the same file against the migrations.

Postconditions
  1. A submission under an id another user submitted is 409, and the stored
     incident is unchanged.
  2. The submitter re-submitting their own id updates it (upstream ids, e.g.
     PagerDuty's, are re-sent as an incident develops).
  3. ``save_incident`` itself refuses the overwrite (returns False, changes
     nothing), for callers that do not go through the route.

Undefined here: which of two simultaneous first submissions of one id wins.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.incidents import save_incident
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


def _body(incident_id: str, title: str) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "title": title,
        "severity": "P2",
        "affected_services": ["payments-service"],
    }


async def _stored(engine: AsyncEngine, incident_id: str) -> tuple[str, str]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT title, submitted_by FROM incidents WHERE id = :i"),
                {"i": incident_id},
            )
        ).one()
    return row.title, row.submitted_by


async def test_another_users_submission_under_a_taken_id_is_refused(
    db_engine: AsyncEngine,
) -> None:
    iid = f"INC-{uuid4().hex[:8]}"
    title = "deployment cpu memory timeout latency degradation"
    async with as_user("alice") as client:
        resp = await client.post("/api/v1/session/incident", json=_body(iid, title))
    assert resp.status_code == 200, resp.text
    before = await _stored(db_engine, iid)
    assert before == (title, "alice")

    async with as_user("bob") as client:
        resp = await client.post("/api/v1/session/incident", json=_body(iid, "bob's incident"))
    assert resp.status_code == 409, resp.text
    assert iid in resp.text
    assert await _stored(db_engine, iid) == before

    async with as_user("alice") as client:
        resp = await client.post(
            "/api/v1/session/incident", json=_body(iid, f"{title} (update)")
        )
    assert resp.status_code == 200, resp.text
    assert await _stored(db_engine, iid) == (f"{title} (update)", "alice")


async def test_save_incident_refuses_another_submitters_overwrite(
    db_engine: AsyncEngine,
) -> None:
    iid = f"INC-{uuid4().hex[:8]}"
    fields: dict[str, Any] = {
        "incident_id": iid,
        "title": "first",
        "severity": "P2",
        "affected_services": [],
        "session_id": "s-1",
        "cynefin_domain": None,
        "domain_confidence": None,
        "overall_confidence": None,
        "guardian_verdict": None,
        "draft_explanation": "",
        "causal_hypotheses": [],
        "proposed_interventions": [],
        "submitted_by": "alice",
        "requires_human_review": False,
    }
    assert await save_incident(**fields) is True
    assert await save_incident(**{**fields, "title": "second", "submitted_by": "bob"}) is False
    assert await _stored(db_engine, iid) == ("first", "alice")
    assert await save_incident(**{**fields, "title": "third"}) is True
    assert await _stored(db_engine, iid) == ("third", "alice")

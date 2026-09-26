"""
A session belongs to the user who started it.

Before: any authenticated user read another user's session through
``GET /api/v1/session/{id}/state``, resumed it through ``POST /run`` with its
``session_id`` -- and the resume's upsert then wrote the resumer's id over the
owner's (``user_id = excluded.user_id``) -- and borrowed its classified domain and
cycle count in ``POST /api/governance/check``. The incidents list hands every
session id to every token holder, so the ids were not secret.

Preconditions
  - Firebase verification is patched to return the claims each test names.
  - The SQLite test engine (``db_engine``) is live; the PostgreSQL job runs the
    same file against the migrations.

Postconditions
  1. The owner reads and resumes their session; a governance role
     (governance_officer, platform_engineer) reads and resumes any session.
  2. Anyone else gets 403 on read, resume and governance check, and a refused
     resume changes neither the stored state nor its owner.
  3. A save never reassigns a session's owner.
  4. A session with no recorded owner is read by governance roles only.

Undefined here: the 403 body's wording beyond naming the session.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api import session as session_api
from src.api.main import app
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import CynefinDomain, EpistemicState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio

ALICE = {"uid": "alice", "roles": ["analyst"]}
BOB = {"uid": "bob", "roles": ["analyst"]}
BOB_AUTHOR = {"uid": "bob", "roles": ["graph_author"]}
OFFICER = {"uid": "officer", "roles": ["governance_officer"]}
ENGINEER = {"uid": "eng", "roles": ["platform_engineer"]}


@asynccontextmanager
async def as_user(user: dict[str, Any]) -> AsyncIterator[AsyncClient]:
    with patch(
        "src.middleware.auth._verify_firebase_token", new=AsyncMock(return_value=user)
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as client:
            yield client


async def _alices_session() -> str:
    async with as_user(ALICE) as client:
        resp = await client.post(
            "/api/v1/session/run", json={"description": "deployment latency timeout cpu"}
        )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["session_id"])


async def _row(engine: AsyncEngine, sid: str) -> tuple[str | None, dict[str, Any]]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT user_id, state_json FROM session_state WHERE session_id = :s"),
                {"s": sid},
            )
        ).one()
    state = row.state_json if isinstance(row.state_json, dict) else json.loads(row.state_json)
    return row.user_id, state


@pytest.mark.parametrize("cold", [False, True], ids=["cached", "from-the-store"])
async def test_only_the_owner_and_governance_read_a_session(
    db_engine: AsyncEngine, cold: bool
) -> None:
    sid = await _alices_session()
    if cold:
        session_api._session_cache.clear()
        session_api._session_owner.clear()
    for user, expected in ((BOB, 403), (ALICE, 200), (OFFICER, 200), (ENGINEER, 200)):
        async with as_user(user) as client:
            resp = await client.get(f"/api/v1/session/{sid}/state")
        assert resp.status_code == expected, f"{user['uid']}: {resp.status_code} {resp.text}"
        if expected == 403:
            assert sid in resp.text and "draft_explanation" not in resp.text


async def test_another_user_cannot_resume_a_session(db_engine: AsyncEngine) -> None:
    sid = await _alices_session()
    before = await _row(db_engine, sid)
    assert before[0] == "alice"

    async with as_user(BOB) as client:
        resp = await client.post(
            "/api/v1/session/run", json={"session_id": sid, "description": "bob was here"}
        )
    assert resp.status_code == 403, resp.text
    assert await _row(db_engine, sid) == before, "a refused resume changed the session"

    async with as_user(ALICE) as client:
        resp = await client.post(
            "/api/v1/session/run", json={"session_id": sid, "description": "alice again"}
        )
    assert resp.status_code == 200, resp.text


async def test_a_governance_resume_leaves_the_owner_in_place(db_engine: AsyncEngine) -> None:
    sid = await _alices_session()
    async with as_user(OFFICER) as client:
        resp = await client.post(
            "/api/v1/session/run", json={"session_id": sid, "description": "reviewed"}
        )
    assert resp.status_code == 200, resp.text
    owner, state = await _row(db_engine, sid)
    assert owner == "alice"
    assert state["draft_explanation"] != "", state


async def test_a_save_never_reassigns_the_owner(db_engine: AsyncEngine) -> None:
    store = SessionStateStore()
    sid = f"own-{uuid4()}"
    state = EpistemicState(session_id=sid, agent_id="a", draft_explanation="one")
    await store.save(state, user_id="alice")
    await store.save(state.model_copy(update={"draft_explanation": "two"}), user_id="bob")
    loaded = await store.load_with_owner(sid)
    assert loaded is not None
    assert loaded[1] == "alice"
    assert loaded[0].draft_explanation == "two"
    assert await store.load_with_owner("no-such-session") is None


async def test_an_ownerless_session_is_read_by_governance_only(db_engine: AsyncEngine) -> None:
    sid = f"legacy-{uuid4()}"
    await SessionStateStore().save(EpistemicState(session_id=sid, agent_id="a"))
    for user, expected in ((ALICE, 403), (ENGINEER, 200)):
        async with as_user(user) as client:
            resp = await client.get(f"/api/v1/session/{sid}/state")
        assert resp.status_code == expected, f"{user['uid']}: {resp.text}"


async def test_governance_check_does_not_read_another_users_session(
    db_engine: AsyncEngine,
) -> None:
    sid = f"own-{uuid4()}"
    await SessionStateStore().save(
        EpistemicState(
            session_id=sid, agent_id="a", cynefin_domain=CynefinDomain.COMPLICATED
        ),
        user_id="alice",
    )
    body = {
        "action_type": "GRAPH_WRITE",
        "payload": {},
        "subject_id": str(uuid4()),
        "session_id": sid,
        "narrative": "",
    }
    async with as_user(BOB_AUTHOR) as client:
        resp = await client.post("/api/governance/check", json=body)
    assert resp.status_code == 403, resp.text
    async with as_user({"uid": "alice", "roles": ["graph_author"]}) as client:
        resp = await client.post("/api/governance/check", json=body)
    assert resp.status_code != 403, resp.text

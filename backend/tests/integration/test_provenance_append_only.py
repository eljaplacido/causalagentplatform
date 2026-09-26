"""
Provenance is durable and append-only; erasure is a tombstone.

Written for septa-48 (register entry `provenance-not-append-only`). ADR-002 says
"Both provenance and contract records are append-only". The records lived in an
in-process dict, lost on restart, and DELETE /api/v1/provenance/session/{id}
popped a session's records out of it.

Preconditions: the db_engine fixture (SQLite, or PostgreSQL built from the
migrations with the app connected as causal_app); db_session connects as the
tables' owner on PostgreSQL, which grants do not bind and triggers do.

Postconditions:
  - an entry appended through the store is read back by a new store instance
    over the database, with no in-process state involved;
  - UPDATE and DELETE on provenance_entries and provenance_erasures are refused,
    by trigger, even for the owner; on PostgreSQL causal_app holds neither
    privilege at all;
  - no provenance route has a method that removes or rewrites: DELETE on the old
    erase path is not served, and the records are still there after it;
  - POST /api/v1/provenance/session/{id}/erasure, by a governance role with a
    reason, hides that session's entries from trace, export and invocations,
    returns and keeps the tombstone (who, when, why, how many), and leaves every
    row in the database; entries written after it are visible;
  - an erasure without a reason is refused and hides nothing;
  - every provenance read and the erasure are refused (403) to non-governance
    roles, and a refused erasure hides nothing.

Undefined, not asserted: tombstone ids, timestamps' exact text, the database
error's wording beyond "append-only" / "permission denied".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.semantic_layer.provenance import (
    LLMCapability,
    LLMInvocation,
    LLMProvider,
    ProvenanceRecord,
    ProvenanceRecordType,
)
from src.semantic_layer.provenance_store import ProvenanceStore
from tests.conftest import postgres_only

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.asyncio

OFFICER: dict[str, Any] = {"uid": "officer-1", "roles": ["governance_officer"]}
ENGINEER: dict[str, Any] = {"uid": "engineer-1", "roles": ["platform_engineer"]}
NON_GOVERNANCE = ("viewer", "analyst", "graph_author", "sre", "agent")
REASON = "Subject access request #42: remove from the provenance API."


class _As:
    def __init__(self, user: dict[str, Any]) -> None:
        self._user = user

    async def __aenter__(self) -> AsyncClient:
        self._patch = patch(
            "src.middleware.auth._verify_firebase_token",
            new_callable=AsyncMock,
            return_value=self._user,
        )
        self._patch.start()
        self._client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        )
        return await self._client.__aenter__()

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)
        self._patch.stop()


def _records(sid: str) -> list[ProvenanceRecord]:
    activity = ProvenanceRecord(
        record_type=ProvenanceRecordType.ACTIVITY, session_id=sid, agent_id="t", label="run"
    )
    entity = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id="t",
        was_generated_by=activity.id,
        attributes={"secret": "content that must stop being served"},
    )
    return [activity, entity]


def _invocation(sid: str) -> LLMInvocation:
    return LLMInvocation(
        session_id=sid,
        agent_id="t",
        capability=LLMCapability.FAST_CLASSIFICATION,
        provider=LLMProvider.OLLAMA,
        model_name="m",
    )


async def _seed(sid: str) -> list[ProvenanceRecord]:
    records = _records(sid)
    await ProvenanceStore().append(records, [_invocation(sid)])
    return records


async def _count(db_session: AsyncSession, table: str, sid: str) -> int:
    row = await db_session.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE session_id = :sid"),  # noqa: S608 - fixed names
        {"sid": sid},
    )
    return int(row.scalar_one())


async def test_entries_are_persisted_and_read_by_a_new_store(db_engine: object) -> None:
    sid = f"persist-{uuid4()}"
    records = await _seed(sid)
    view = await ProvenanceStore().read(sid)
    assert [r.id for r in view.records] == [r.id for r in records]
    assert view.records[1].attributes == records[1].attributes
    assert len(view.invocations) == 1
    assert view.erasures == []


@pytest.mark.parametrize("table", ["provenance_entries", "provenance_erasures"])
async def test_rows_cannot_be_updated_or_deleted(
    table: str, db_engine: object, db_session: AsyncSession
) -> None:
    sid = f"immutable-{uuid4()}"
    await _seed(sid)
    await ProvenanceStore().erase(sid, erased_by="officer-1", reason=REASON)

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text(f"UPDATE {table} SET session_id = 'other' WHERE session_id = :sid"),  # noqa: S608
            {"sid": sid},
        )
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text(f"DELETE FROM {table} WHERE session_id = :sid"),  # noqa: S608
            {"sid": sid},
        )
        await db_session.commit()
    await db_session.rollback()
    assert await _count(db_session, table, sid) >= 1


@postgres_only
@pytest.mark.parametrize("table", ["provenance_entries", "provenance_erasures"])
@pytest.mark.parametrize("statement", ["UPDATE {t} SET session_id = 'x'", "DELETE FROM {t}"])
async def test_the_app_role_holds_no_update_or_delete_privilege(
    table: str, statement: str, db_engine: AsyncEngine
) -> None:
    """Grants refuse causal_app before the trigger is reached."""
    async with db_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(text(statement.format(t=table)))
        await conn.rollback()


async def test_no_route_removes_provenance(db_engine: object, db_session: AsyncSession) -> None:
    sid = f"no-delete-{uuid4()}"
    await _seed(sid)
    async with _As(OFFICER) as client:
        resp = await client.delete(f"/api/v1/provenance/session/{sid}")
    assert resp.status_code in (404, 405)
    assert await _count(db_session, "provenance_entries", sid) == 3

    from src.api import provenance

    methods = {m for r in provenance.router.routes for m in getattr(r, "methods", ())}
    assert methods <= {"GET", "HEAD", "POST"}, methods


async def test_an_erasure_is_a_tombstone(db_engine: object, db_session: AsyncSession) -> None:
    sid = f"erase-{uuid4()}"
    await _seed(sid)

    async with _As(OFFICER) as client:
        resp = await client.post(
            f"/api/v1/provenance/session/{sid}/erasure", json={"reason": REASON}
        )
        assert resp.status_code == 201, resp.text
        tomb = resp.json()
        assert tomb["erased_by"] == "officer-1"
        assert tomb["reason"] == REASON
        assert tomb["hidden_count"] == 3
        assert tomb["erased_at"]

        trace = (await client.get(f"/api/v1/provenance/trace/{sid}")).json()
        export = (await client.get(f"/api/v1/provenance/export/{sid}")).json()
        invocations = (await client.get(f"/api/v1/provenance/invocations/{sid}")).json()

    assert trace["records"] == [] and trace["invocations"] == []
    assert trace["record_count"] == 0
    assert [e["reason"] for e in trace["erasures"]] == [REASON]
    assert trace["erasures"][0]["erased_by"] == "officer-1"
    assert "content that must stop being served" not in str(trace)
    assert export["entity"] == [] and export["activity"] == []
    assert invocations == []

    # Hidden, not deleted: the rows and the tombstone are all still there.
    assert await _count(db_session, "provenance_entries", sid) == 3
    assert await _count(db_session, "provenance_erasures", sid) == 1

    # What is written afterwards is new evidence, and is served.
    later = ProvenanceRecord(session_id=sid, agent_id="t", label="after")
    await ProvenanceStore().append([later])
    async with _As(ENGINEER) as client:
        trace = (await client.get(f"/api/v1/provenance/trace/{sid}")).json()
    assert [r["id"] for r in trace["records"]] == [str(later.id)]
    assert len(trace["erasures"]) == 1


@pytest.mark.parametrize("payload", [{}, {"reason": ""}, {"reason": "too short"}])
async def test_an_erasure_needs_a_reason(
    payload: dict[str, str], db_engine: object, db_session: AsyncSession
) -> None:
    sid = f"no-reason-{uuid4()}"
    await _seed(sid)
    async with _As(OFFICER) as client:
        resp = await client.post(f"/api/v1/provenance/session/{sid}/erasure", json=payload)
        assert resp.status_code == 422
        trace = (await client.get(f"/api/v1/provenance/trace/{sid}")).json()
    assert trace["record_count"] == 2
    assert await _count(db_session, "provenance_erasures", sid) == 0


@pytest.mark.parametrize("role", NON_GOVERNANCE)
async def test_reads_and_erasure_need_a_governance_role(
    role: str, db_engine: object, db_session: AsyncSession
) -> None:
    sid = f"rbac-{uuid4()}"
    await _seed(sid)
    async with _As({"uid": role, "roles": [role]}) as client:
        for path in ("trace", "export", "invocations"):
            resp = await client.get(f"/api/v1/provenance/{path}/{sid}")
            assert resp.status_code == 403, (path, resp.status_code)
        resp = await client.post(
            f"/api/v1/provenance/session/{sid}/erasure", json={"reason": REASON}
        )
        assert resp.status_code == 403
    assert await _count(db_session, "provenance_erasures", sid) == 0
    assert len((await ProvenanceStore().read(sid)).records) == 2

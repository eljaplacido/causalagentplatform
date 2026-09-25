"""
Every request value that reaches a query is a bound parameter, never SQL text.

Written for septa-50, which turned on bandit's B608 (SQL built from strings) for
backend/src. The thirteen sites it flagged interpolated fixed fragments and table
names from closed mappings, not request values, and were rewritten so the SQL is
no longer assembled from strings. These tests pin what each rewritten query must
return, against rows seeded here with known values, so the rewrite is checked
against the specification rather than against its own output.

Preconditions: the db_engine fixture's schema (SQLite, or PostgreSQL built from
the migrations when TEST_DATABASE_URL is set); an authenticated
caller (Firebase verification patched) with the roles each route requires.

Postconditions:
  - a filter returns exactly the seeded rows that match it, and the totals agree;
  - a filter value shaped like SQL (a quote, OR 1=1, a stacked statement) matches
    nothing, because it is compared as a string, and changes nothing: the seeded
    rows are all still there afterwards;
  - a domain scope restricts rows to the scope's domains, and an empty scope
    returns nothing.

Undefined, and not asserted: the order among rows with equal created_at, and the
exact text of the SQL the driver receives.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.db.types import TEXT_ARRAY, TIMESTAMP, typed_text
from src.semantic_layer.models import ApprovalStatus, FragmentApprovalStatus
from src.semantic_layer.registry import SemanticLayerRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import TextClause

pytestmark = pytest.mark.asyncio

#: Shapes an attacker would send. Each is a plausible value for a string filter.
INJECTIONS = [
    "x' OR '1'='1",
    "x' OR 1=1 --",
    "'; DELETE FROM audit_log; --",
    "devops') OR ('1'='1",
]

_USER: dict[str, Any] = {
    "uid": "sql-filter-test",
    "email": "sql@test.com",
    "name": "SQL Filter Test",
    "roles": ["governance_officer", "platform_engineer", "domain_expert"],
}
_CREATED_BY = json.dumps({"id": "u1", "email": "u1@test.com", "name": "U One"})


@pytest_asyncio.fixture
async def client(db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as ac:
            yield ac


async def _exec(engine: AsyncEngine, sql: str | TextClause, rows: list[dict[str, Any]]) -> None:
    statement = text(sql) if isinstance(sql, str) else sql
    async with engine.begin() as conn:
        for row in rows:
            await conn.execute(statement, row)


_COUNTS = {
    t: f"SELECT COUNT(*) FROM {t}"
    for t in ("audit_log", "incidents", "causal_entities", "graph_fragments", "assumptions")
}


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(_COUNTS[table]))).scalar() or 0)


# Seed rows are bound with the column types (src/db/types.py), as the app's own
# writes are, so they insert on SQLite and PostgreSQL alike.
_AUDIT_SQL = typed_text(
    "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, guardian_verdict, "
    "policy_rules_cited, session_id, created_at) "
    "VALUES (:id, :agent, :action, :payload, :verdict, :rules, :session, :at)",
    rules=TEXT_ARRAY,
    at=TIMESTAMP,
)


def _audit(
    agent: str, verdict: str, at: str, action: str = "CHECK",
    session: str | None = None, payload: str = "h",
) -> dict[str, Any]:
    return {
        "id": str(uuid4()), "agent": agent, "action": action, "payload": payload,
        "verdict": verdict, "session": session, "at": at, "rules": [],
    }


# ── GET /api/governance/audit-log ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def audit_rows(db_engine: AsyncEngine) -> None:
    await _exec(db_engine, _AUDIT_SQL, [
        _audit("agent-a", "APPROVED", "2026-01-01T12:00:00+00:00"),
        _audit("agent-a", "REJECTED", "2026-01-03T12:00:00+00:00"),
        _audit("agent-b", "APPROVED", "2026-01-05T12:00:00+00:00"),
    ])


async def test_audit_log_filters_return_exactly_the_matching_rows(
    client: AsyncClient, audit_rows: None
) -> None:
    async def get(query: str) -> dict[str, Any]:
        resp = await client.get(f"/api/governance/audit-log{query}")
        assert resp.status_code == 200, resp.text
        return resp.json()

    assert (await get(""))["total"] == 3
    by_agent = await get("?agent_id=agent-a")
    assert by_agent["total"] == 2
    assert {e["agentId"] for e in by_agent["entries"]} == {"agent-a"}
    assert (await get("?verdict=APPROVED"))["total"] == 2
    assert (await get("?agent_id=agent-a&verdict=APPROVED"))["total"] == 1
    window = await get("?from=2026-01-02T00:00:00Z&to=2026-01-04T00:00:00Z")
    assert window["total"] == 1
    assert window["entries"][0]["guardianVerdict"] == "REJECTED"
    # Newest first, and the page is a page of the filtered set.
    paged = await get("?page_size=2")
    assert paged["total"] == 3
    assert [e["agentId"] for e in paged["entries"]] == ["agent-b", "agent-a"]
    second = await get("?page_size=2&page=2")
    assert [e["guardianVerdict"] for e in second["entries"]] == ["APPROVED"]


@pytest.mark.parametrize("value", INJECTIONS)
async def test_audit_log_agent_id_injection_matches_nothing(
    client: AsyncClient, audit_rows: None, db_engine: AsyncEngine, value: str
) -> None:
    resp = await client.get("/api/governance/audit-log", params={"agent_id": value})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    assert resp.json()["entries"] == []
    assert await _count(db_engine, "audit_log") == 3


# ── GET /api/v1/incidents/ ────────────────────────────────────────────────────


_INCIDENT_SQL = typed_text(
    "INSERT INTO incidents (id, title, severity, status, proposed_interventions, "
    "submitted_by, created_at, updated_at) "
    "VALUES (:id, :title, 'P2', :status, :ints, 'u1', :at, :at)",
    at=TIMESTAMP,
)


@pytest_asyncio.fixture
async def incidents(db_engine: AsyncEngine) -> None:
    await _exec(db_engine, _INCIDENT_SQL, [
        {"id": "inc-1", "title": "one", "status": "ACTIVE", "at": "2026-01-01T00:00:00",
         "ints": json.dumps([{"id": "iv-1"}, {"id": "iv-2"}])},
        {"id": "inc-2", "title": "two", "status": "RESOLVED", "at": "2026-01-02T00:00:00",
         "ints": json.dumps([{"id": "iv-3"}])},
        {"id": "inc-3", "title": "three", "status": "ACTIVE", "at": "2026-01-03T00:00:00",
         "ints": "[]"},
    ])
    # Decisions of record: iv-1 approved, iv-3 rejected, and one for an incident
    # that is not listed, which must not leak onto any listed one.
    await _exec(db_engine, _AUDIT_SQL, [
        _audit("u1", "APPROVED", "2026-01-04T00:00:00", "INTERVENTION_APPROVED", "inc-1",
               json.dumps({"intervention_id": "iv-1", "decided_by": "u1"})),
        _audit("u1", "REJECTED", "2026-01-05T00:00:00", "INTERVENTION_REJECTED", "inc-2",
               json.dumps({"intervention_id": "iv-3", "decided_by": "u1"})),
        _audit("u1", "APPROVED", "2026-01-06T00:00:00", "INTERVENTION_APPROVED", "inc-9",
               json.dumps({"intervention_id": "iv-2", "decided_by": "u1"})),
    ])


async def test_incident_list_filters_and_merges_verdicts(
    client: AsyncClient, incidents: None
) -> None:
    resp = await client.get("/api/v1/incidents/")
    assert resp.status_code == 200, resp.text
    listed = {r["id"]: r for r in resp.json()}
    assert list(listed) == ["inc-3", "inc-2", "inc-1"]
    verdicts = {
        iv["id"]: iv.get("guardianVerdict")
        for r in listed.values() for iv in r["proposed_interventions"]
    }
    assert verdicts == {"iv-1": "APPROVED", "iv-2": None, "iv-3": "REJECTED"}

    resp = await client.get("/api/v1/incidents/", params={"status": "ACTIVE"})
    assert [r["id"] for r in resp.json()] == ["inc-3", "inc-1"]
    resp = await client.get("/api/v1/incidents/", params={"limit": 1})
    assert [r["id"] for r in resp.json()] == ["inc-3"]


@pytest.mark.parametrize("value", INJECTIONS)
async def test_incident_status_injection_matches_nothing(
    client: AsyncClient, incidents: None, db_engine: AsyncEngine, value: str
) -> None:
    resp = await client.get("/api/v1/incidents/", params={"status": value})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    assert await _count(db_engine, "incidents") == 3
    assert await _count(db_engine, "audit_log") == 3


# ── GET /api/v1/interventions/decisions ───────────────────────────────────────


async def test_decision_list_filters_by_incident(
    client: AsyncClient, incidents: None
) -> None:
    resp = await client.get("/api/v1/interventions/decisions")
    assert resp.status_code == 200, resp.text
    assert [d["intervention_id"] for d in resp.json()] == ["iv-2", "iv-3", "iv-1"]
    resp = await client.get("/api/v1/interventions/decisions", params={"incident_id": "inc-2"})
    assert [(d["intervention_id"], d["decision"]) for d in resp.json()] == [("iv-3", "REJECTED")]


@pytest.mark.parametrize("value", INJECTIONS)
async def test_decision_incident_injection_matches_nothing(
    client: AsyncClient, incidents: None, db_engine: AsyncEngine, value: str
) -> None:
    resp = await client.get("/api/v1/interventions/decisions", params={"incident_id": value})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    assert await _count(db_engine, "audit_log") == 3


# ── SemanticLayerRegistry list queries ────────────────────────────────────────


@pytest_asyncio.fixture
async def semantic_rows(db_engine: AsyncEngine) -> None:
    await _exec(db_engine, typed_text(
        "INSERT INTO causal_entities (id, name, entity_type, domain, approval_status, "
        "created_by, created_at, approved_by, approved_at) "
        "VALUES (:id, :name, 'SERVICE', :domain, :status, :by, :at, :aby, :aat)",
        at=TIMESTAMP, aat=TIMESTAMP,
    ), [
        {"id": str(uuid4()), "name": n, "domain": d, "status": s, "by": _CREATED_BY, "at": at,
         "aby": _CREATED_BY if s == "APPROVED" else None,
         "aat": at if s == "APPROVED" else None}
        for n, d, s, at in [
            ("e-dev-draft", "devops", "DRAFT", "2026-01-01T00:00:00"),
            ("e-dev-ok", "devops", "APPROVED", "2026-01-02T00:00:00"),
            ("e-fin-draft", "finance", "DRAFT", "2026-01-03T00:00:00"),
        ]
    ])
    await _exec(db_engine, typed_text(
        "INSERT INTO graph_fragments (id, name, domain, approval_status, created_by, "
        "created_at) VALUES (:id, :name, :domain, :status, :by, :at)",
        at=TIMESTAMP,
    ), [
        {"id": str(uuid4()), "name": n, "domain": d, "status": s, "by": _CREATED_BY, "at": at}
        for n, d, s, at in [
            ("f-dev-draft", "devops", "DRAFT", "2026-01-01T06:00:00"),
            ("f-fin-draft", "finance", "DRAFT", "2026-01-02T06:00:00"),
            ("f-dev-old", "devops", "DEPRECATED", "2026-01-03T06:00:00"),
        ]
    ])
    await _exec(db_engine, typed_text(
        "INSERT INTO assumptions (id, statement, assumption_type, justification, "
        "elicited_from, elicited_by, elicited_at, review_status, domain) "
        "VALUES (:id, :st, 'STRUCTURAL', 'seeded justification', 'DOMAIN_EXPERT', "
        ":by, :at, :status, :domain)",
        at=TIMESTAMP,
    ), [
        {"id": str(uuid4()), "st": st, "domain": d, "status": s, "by": _CREATED_BY, "at": at}
        for st, d, s, at in [
            ("a-dev-pending", "devops", "PENDING", "2026-01-02T00:00:00"),
            ("a-fin-pending", "finance", "PENDING", "2026-01-01T00:00:00"),
            # Not PENDING. It said VALIDATED, which ReviewStatus and the CHECK in
            # 001_initial.sql do not allow; SQLite's schema has no CHECK to refuse it.
            ("a-dev-done", "devops", "ACCEPTED", "2026-01-03T00:00:00"),
        ]
    ])


async def test_registry_lists_filter_and_scope(semantic_rows: None) -> None:
    reg = SemanticLayerRegistry()

    async def names(rows: Any) -> list[str]:
        return [r.name for r in rows]

    assert await names(await reg.list_entities()) == ["e-fin-draft", "e-dev-ok", "e-dev-draft"]
    assert await names(await reg.list_entities(domain="devops")) == ["e-dev-ok", "e-dev-draft"]
    assert await names(await reg.list_entities(status=ApprovalStatus.DRAFT)) == [
        "e-fin-draft", "e-dev-draft"
    ]
    assert await names(await reg.list_entities(domains={"finance"})) == ["e-fin-draft"]
    assert await names(await reg.list_entities(domain="devops", domains={"finance"})) == []
    assert await names(await reg.list_entities(domains=set())) == []

    assert await names(await reg.list_fragments(domains={"devops", "finance"})) == [
        "f-dev-old", "f-fin-draft", "f-dev-draft"
    ]
    assert await names(await reg.list_fragments(domain="devops")) == ["f-dev-old", "f-dev-draft"]
    assert await names(
        await reg.list_fragments(status=FragmentApprovalStatus.DRAFT, domains={"finance"})
    ) == ["f-fin-draft"]
    assert await names(await reg.list_fragments(domains=set())) == []

    pending = await reg.list_pending_assumptions()
    assert [a.statement for a in pending] == ["a-fin-pending", "a-dev-pending"]
    pending = await reg.list_pending_assumptions(domain="devops")
    assert [a.statement for a in pending] == ["a-dev-pending"]

    queue = await reg.list_pending_review_queue()
    assert [q["name"] for q in queue] == ["e-dev-draft", "f-dev-draft", "f-fin-draft",
                                          "e-fin-draft"]
    queue = await reg.list_pending_review_queue(domains={"finance"})
    assert {(q["object_type"], q["name"]) for q in queue} == {
        ("GraphFragment", "f-fin-draft"), ("CausalEntity", "e-fin-draft")
    }
    assert await reg.list_pending_review_queue(domains=set()) == []


@pytest.mark.parametrize("value", INJECTIONS)
async def test_registry_domain_injection_matches_nothing(
    semantic_rows: None, db_engine: AsyncEngine, value: str
) -> None:
    reg = SemanticLayerRegistry()
    assert list(await reg.list_entities(domain=value)) == []
    assert list(await reg.list_entities(domains={value})) == []
    assert list(await reg.list_fragments(domain=value)) == []
    assert list(await reg.list_pending_assumptions(domain=value)) == []
    assert await reg.list_pending_review_queue(domains={value}) == []
    assert await _count(db_engine, "causal_entities") == 3
    assert await _count(db_engine, "graph_fragments") == 3
    assert await _count(db_engine, "assumptions") == 3

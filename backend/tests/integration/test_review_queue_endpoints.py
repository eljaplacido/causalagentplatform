"""
A queued edge says what it connects: GET /api/governance/review-queue endpoints.

An edge's row in the queue used to carry only `name`, which for an edge is its
edge type -- every edge read "CAUSES", with no way to tell one from another.

Preconditions: the db_engine fixture's schema (SQLite, or PostgreSQL built from
the migrations); an authenticated caller (Firebase verification patched) with a
governance role; rows seeded directly with known ids and names.

Postconditions:
  - each CausalEdge row has `source` and `target`, each {id, object_type, name},
    with the stored id and object type;
  - `name` is the causal entity's name when the endpoint id is an entity, and
    null when no entity has that id -- never a made-up label;
  - an entity outside the caller's domain scope does not lend its name (null);
  - rows of other object types carry no endpoints (null).

Undefined, and not asserted: the order of rows with equal created_at.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.types import TIMESTAMP, UUID_ARRAY, typed_text
from src.semantic_layer.registry import SemanticLayerRegistry
from tests.conftest import insert_raw_audit_entry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_BY = json.dumps({"id": "u1", "email": "u1@test.com", "name": "U One"})

ENTITY_DEV = str(uuid4())  # "checkout-service", devops
ENTITY_FIN = str(uuid4())  # "ledger-db", finance
NOT_AN_ENTITY = str(uuid4())
EDGE_DEV = str(uuid4())  # checkout-service -> (no entity), devops
EDGE_FIN = str(uuid4())  # checkout-service (devops entity) -> ledger-db, finance
FRAGMENT = str(uuid4())


def _user(domains: list[str] | None) -> dict[str, Any]:
    user: dict[str, Any] = {
        "uid": "queue-test",
        "email": "q@test.com",
        "name": "Queue Test",
        "roles": ["governance_officer"],
    }
    if domains is not None:
        user["domains"] = domains
    return user


@pytest_asyncio.fixture
async def queued(db_session: AsyncSession) -> None:
    verdict = await insert_raw_audit_entry(db_session)
    await db_session.execute(
        typed_text(
            "INSERT INTO causal_entities (id, name, entity_type, domain, approval_status, "
            "created_by, created_at) VALUES (:id, :name, 'SERVICE', :domain, 'APPROVED', :by, :at)",
            at=TIMESTAMP,
        ),
        [
            {"id": ENTITY_DEV, "name": "checkout-service", "domain": "devops", "by": _BY,
             "at": "2026-01-01T00:00:00"},
            {"id": ENTITY_FIN, "name": "ledger-db", "domain": "finance", "by": _BY,
             "at": "2026-01-01T00:00:00"},
        ],
    )
    await db_session.execute(
        typed_text(
            "INSERT INTO causal_edges (id, source_id, source_type, target_id, target_type, "
            "edge_type, direction, confidence, evidence_refs, assumption_refs, approval_status, "
            "guardian_verdict_ref, version, lineage, domain, created_at) VALUES (:id, :src, "
            ":src_type, :tgt, :tgt_type, 'CAUSES', 'FORWARD', 0.6, :ev, :asm, 'DRAFT', :verdict, "
            "'1.0.0', '[]', :domain, :at)",
            ev=UUID_ARRAY,
            asm=UUID_ARRAY,
            at=TIMESTAMP,
        ),
        [
            {"id": EDGE_DEV, "src": ENTITY_DEV, "src_type": "CausalEntity", "tgt": NOT_AN_ENTITY,
             "tgt_type": "Outcome", "ev": [], "asm": [], "verdict": str(verdict),
             "domain": "devops", "at": "2026-01-02T00:00:00"},
            {"id": EDGE_FIN, "src": ENTITY_DEV, "src_type": "CausalEntity", "tgt": ENTITY_FIN,
             "tgt_type": "CausalEntity", "ev": [], "asm": [], "verdict": str(verdict),
             "domain": "finance", "at": "2026-01-03T00:00:00"},
        ],
    )
    await db_session.execute(
        typed_text(
            "INSERT INTO graph_fragments (id, name, domain, approval_status, created_by, "
            "created_at) VALUES (:id, 'f-draft', 'devops', 'DRAFT', :by, :at)",
            at=TIMESTAMP,
        ),
        {"id": FRAGMENT, "by": _BY, "at": "2026-01-04T00:00:00"},
    )
    await db_session.commit()


async def _queue(domains: list[str] | None) -> list[dict[str, Any]]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_user(domains),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as ac:
            resp = await ac.get("/api/governance/review-queue")
    assert resp.status_code == 200, resp.text
    return {row["id"]: row for row in resp.json()}  # type: ignore[return-value]


async def test_edge_rows_name_their_endpoints(queued: None) -> None:
    rows = await _queue(None)
    edge = rows[EDGE_DEV]
    assert edge["name"] == "CAUSES"
    assert edge["source"] == {"id": ENTITY_DEV, "object_type": "CausalEntity",
                              "name": "checkout-service"}
    # No entity has this id: the queue shows the id, and no name.
    assert edge["target"] == {"id": NOT_AN_ENTITY, "object_type": "Outcome", "name": None}
    assert rows[EDGE_FIN]["target"]["name"] == "ledger-db"
    assert rows[FRAGMENT]["source"] is None
    assert rows[FRAGMENT]["target"] is None


async def test_an_entity_outside_the_scope_lends_no_name(queued: None) -> None:
    rows = await _queue(["finance"])
    assert set(rows) == {EDGE_FIN}
    fin = rows[EDGE_FIN]
    # The source is a devops entity: outside a finance-only scope, so no name.
    assert fin["source"] == {"id": ENTITY_DEV, "object_type": "CausalEntity", "name": None}
    assert fin["target"]["name"] == "ledger-db"


async def test_the_registry_returns_the_same_endpoints(queued: None) -> None:
    queue = await SemanticLayerRegistry().list_pending_review_queue()
    by_id = {q["id"]: q for q in queue}
    assert by_id[EDGE_DEV]["source"]["name"] == "checkout-service"
    assert by_id[EDGE_DEV]["target"]["name"] is None
    assert "source" not in by_id[FRAGMENT]

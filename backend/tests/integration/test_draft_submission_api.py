"""
POST /api/semantic/drafts: a draft submitted through the API reaches the registry.

The route built its Guardian EpistemicState with no ``cynefin_domain``, so the state
defaulted to DISORDER and rule PR-006 ("no verdict can be APPROVED in DISORDER")
refused every draft submitted through it: 403, whatever was drafted and by whom. The
tests never saw it because they drafted through the registry directly, with a fixture
verdict, and never through the route.

**How the route classifies the situation.** As POST /api/governance/check does:
the domain the Guardian judges is a named reasoning session's own classification when
the request names one (``session_id``), and otherwise the caller's declaration
(``cynefin_domain``). Nothing declared is DISORDER, which PR-006 still refuses; CHAOTIC
is escalated (PR-003). Unlike /check, a ``session_id`` that names no stored session is
refused (404) rather than falling back to the declaration: a caller who names a session
expects its classification to be the one judged. A session's classification wins over a
declaration, so a caller cannot reclassify a session the platform left in DISORDER.

**Auditable.** The domain judged and where it came from (``declared by <uid>`` or
``session <id>``) are part of the checked payload, so the verdict's payload hash binds
them, and they are written in the audit row's ``notes``, where GET
/api/governance/audit-log shows them. The response says them too.

Preconditions: an authenticated caller with a drafting role (graph_author) whose domain
scope covers the draft's domain.

Postconditions:
  * a realistic entity or edge draft with a declared, non-chaotic domain is stored as
    DRAFT, bound to an APPROVED DRAFT_SUBMISSION verdict whose notes name the domain and
    its source;
  * nothing declared -> 403 citing PR-006, nothing stored, a REJECTED row in audit_log;
  * CHAOTIC declared -> 403, REQUIRES_ESCALATION citing PR-003, nothing stored;
  * a named session's domain is used, and a declaration does not overrule it;
  * a named session that does not exist -> 404, nothing written to audit_log.

Undefined here: whether a caller may name another user's session (session_state records
a user_id; neither /check nor this route reads it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import CynefinDomain, EpistemicState
from tests.conftest import make_causal_entity

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.registry import SemanticLayerRegistry

pytestmark = pytest.mark.asyncio

_DRAFTER: dict[str, Any] = {
    "uid": "drafter-1",
    "email": "drafter@test.com",
    "roles": ["graph_author"],
    "domains": ["devops"],
}


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    from src.api.main import app

    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_DRAFTER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer drafter-token"},
        ) as ac:
            yield ac


def _entity_payload() -> dict[str, Any]:
    return make_causal_entity(domain="devops").model_dump(mode="json")


def _edge_payload() -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "source": {"id": str(uuid4()), "objectType": "CausalEntity"},
        "target": {"id": str(uuid4()), "objectType": "Outcome"},
        "edgeType": "CAUSES",
        "direction": "FORWARD",
        "confidence": 0.7,
        "evidenceRefs": [],
        "assumptionRefs": [],
        "guardianVerdictRef": str(uuid4()),
        "domain": "devops",
    }


async def _audit_row(db_session: AsyncSession, entry_id: str) -> dict[str, Any]:
    row = await db_session.execute(
        text(
            "SELECT action_type, guardian_verdict, subject_id, notes, signature "
            "FROM audit_log WHERE id = :i"
        ),
        {"i": entry_id},
    )
    found = row.mappings().one_or_none()
    assert found is not None, f"no audit row {entry_id}"
    return dict(found)


async def _audit_count(db_session: AsyncSession) -> int:
    return int((await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar() or 0)


async def test_a_declared_domain_entity_draft_is_stored_as_draft(
    client: AsyncClient, registry: SemanticLayerRegistry, db_session: AsyncSession
) -> None:
    payload = _entity_payload()
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": payload, "cynefin_domain": "COMPLICATED"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["guardian_verdict"] == "APPROVED"
    assert body["cynefin_domain"] == "COMPLICATED"
    assert body["domain_source"] == "declared by drafter-1"

    stored = await registry.get_entity(UUID(body["object_id"]))
    assert stored.approval_status == "DRAFT"
    assert stored.created_by.id == "drafter-1"

    audit = await _audit_row(db_session, body["audit_entry_id"])
    assert audit["action_type"] == "DRAFT_SUBMISSION"
    assert audit["guardian_verdict"] == "APPROVED"
    assert str(audit["subject_id"]) == body["object_id"]
    assert audit["signature"], "the verdict the draft was stored under is the Guardian's"
    assert "COMPLICATED" in audit["notes"] and "declared by drafter-1" in audit["notes"]


async def test_a_declared_domain_edge_draft_is_stored_as_draft(
    client: AsyncClient, registry: SemanticLayerRegistry
) -> None:
    payload = _edge_payload()
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEdge", "payload": payload, "cynefin_domain": "COMPLEX"},
    )
    assert resp.status_code == 201, resp.text
    stored = await registry.get_edge(UUID(payload["id"]))
    assert stored.approval_status == "DRAFT"
    assert str(stored.guardian_verdict_ref) == resp.json()["audit_entry_id"]


async def test_nothing_declared_is_disorder_and_pr006_refuses(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _entity_payload()
    resp = await client.post(
        "/api/semantic/drafts", json={"object_type": "CausalEntity", "payload": payload}
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["verdict"] == "REJECTED"
    assert detail["policy_rules_cited"] == ["PR-006"]
    audit = await _audit_row(db_session, detail["audit_entry_id"])
    assert audit["guardian_verdict"] == "REJECTED"
    assert "DISORDER" in audit["notes"]
    stored = await db_session.execute(
        text("SELECT COUNT(*) FROM causal_entities WHERE id = :i"), {"i": payload["id"]}
    )
    assert stored.scalar() == 0


async def test_a_declared_disorder_is_refused_on_pr006(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": _entity_payload(),
              "cynefin_domain": "DISORDER"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["policy_rules_cited"] == ["PR-006"]


async def test_a_declared_chaotic_domain_is_escalated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _entity_payload()
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": payload, "cynefin_domain": "CHAOTIC"},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["verdict"] == "REQUIRES_ESCALATION"
    assert detail["policy_rules_cited"] == ["PR-003"]
    stored = await db_session.execute(
        text("SELECT COUNT(*) FROM causal_entities WHERE id = :i"), {"i": payload["id"]}
    )
    assert stored.scalar() == 0


async def test_a_named_sessions_domain_is_used(
    client: AsyncClient, registry: SemanticLayerRegistry, db_session: AsyncSession
) -> None:
    sid = f"draft-session-{uuid4()}"
    await SessionStateStore().save(
        EpistemicState(session_id=sid, agent_id="triage", cynefin_domain=CynefinDomain.COMPLEX)
    )
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": _entity_payload(), "session_id": sid},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cynefin_domain"] == "COMPLEX"
    assert body["domain_source"] == f"session {sid}"
    assert (await registry.get_entity(UUID(body["object_id"]))).approval_status == "DRAFT"
    audit = await _audit_row(db_session, body["audit_entry_id"])
    assert f"session {sid}" in audit["notes"]


async def test_a_declaration_does_not_overrule_the_sessions_disorder(
    client: AsyncClient,
) -> None:
    sid = f"draft-session-{uuid4()}"
    await SessionStateStore().save(
        EpistemicState(session_id=sid, agent_id="triage", cynefin_domain=CynefinDomain.DISORDER)
    )
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": _entity_payload(),
              "session_id": sid, "cynefin_domain": "COMPLICATED"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["policy_rules_cited"] == ["PR-006"]


async def test_a_session_that_does_not_exist_is_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    before = await _audit_count(db_session)
    resp = await client.post(
        "/api/semantic/drafts",
        json={"object_type": "CausalEntity", "payload": _entity_payload(),
              "session_id": f"no-such-{uuid4()}", "cynefin_domain": "COMPLICATED"},
    )
    assert resp.status_code == 404, resp.text
    assert await _audit_count(db_session) == before

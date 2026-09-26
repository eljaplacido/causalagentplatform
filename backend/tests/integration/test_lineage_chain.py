"""
Every approved object carries lineage from its source data, through the agents, to its approver.

Written for septa-15 (register entry `lineage-partial`). The registry's rule 3
says "All approved objects carry lineage." What was recorded was one lineage
step per human decision: nothing named the data the object came from, the
drafter of an edge was not recorded at all, and the verdict that authorised the
draft was overwritten on approval by the one that authorised the approval.

The chain under test (GET /api/semantic/objects/{object_type}/{object_id}/lineage)
is assembled from records the platform wrote, not from what the submitter put in
the object's own `lineage` list:

  source      the object's `source_data` (a reference and a sha256 digest),
              declared with the draft and carried in its version history;
  drafted_by  the person the token named and the agent the draft came through
              (with the platform version that recorded it), from revision 1;
  verdicts    every Guardian verdict and decision record issued for the object,
              from audit_log, with the revision each authorising verdict was
              spent on and whether its signature verifies;
  approved_by the reviewer, the time and the verdict, from the approving revision;
  steps       the version history itself (tests/unit/test_registry_versioning.py);
  provenance  the sessions those verdicts were issued in, and the provenance
              records the store holds for them.

Preconditions: the app over a clean database (SQLite, or PostgreSQL as
causal_app under row-level security); tokens mocked at the Firebase check; the
Guardian real (GovernanceAgent), so verdicts are signed and spent as in
production.

Postconditions:
  - an entity, an edge and a fragment each submitted through POST
    /api/semantic/drafts, checked and approved through the governance API,
    have a chain naming the source reference and digest, the drafting person
    and agent, the draft verdict (spent on revision 1) and the approval verdict
    (spent on revision 2) with the rules each cited, the human decision
    record, and the approver; the chain reports no gaps;
  - the provenance records held for the draft's session are linked;
  - an object whose draft named no source data cannot be approved (422), the
    approval verdict is not spent, and its chain names the gap;
  - an object approved before any of this was recorded has a chain that says
    what is missing rather than inventing it;
  - the chain and the version history are domain-scoped reads: outside the
    caller's domains they are 404, a viewer reads them for APPROVED objects
    only, and an unknown object type is 422;
  - a draft submitted without a Cynefin classification is still refused
    (PR-006): declaring the domain is what lets a draft through, as for /check.

Undefined, and not asserted: the order of verdicts that share a timestamp, the
wording of gap messages beyond the word each test names, and the platform
version string's format.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.semantic_layer.models import (
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    DataSource,
    EdgeDirection,
    EdgeType,
    EntityType,
    GraphFragment,
    LineageChain,
    SemanticObjectVersion,
    UserRef,
)
from src.semantic_layer.provenance import ProvenanceRecord
from src.semantic_layer.provenance_store import ProvenanceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

AUTHOR = {"uid": "author-7", "email": "a7@test.com", "roles": ["graph_author"],
          "domains": ["devops"]}
GOVERNANCE = {"uid": "gov-9", "email": "g9@test.com", "roles": ["governance_officer"],
              "domains": ["devops"]}
OUTSIDER = {"uid": "fin-1", "roles": ["analyst"], "domains": ["finance"]}
VIEWER = {"uid": "view-1", "roles": ["viewer"], "domains": ["devops"]}

SOURCE = DataSource(
    kind="dataset",
    ref="warehouse://devops/deploy_events@2026-09-25",
    digest="sha256:" + hashlib.sha256(b"deploy_events@2026-09-25").hexdigest(),
)
_PLACEHOLDER = UserRef(id="overwritten-by-the-token")


def _client(user: dict[str, Any]) -> AbstractAsyncContextManager[AsyncClient]:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncClient]:
        with patch(
            "src.middleware.auth._verify_firebase_token",
            new_callable=AsyncMock,
            return_value=user,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer lineage-test"},
            ) as client:
                yield client

    return _ctx()


def _payload(kind: str, source: DataSource | None = SOURCE) -> dict[str, Any]:
    obj: Any
    if kind == "CausalEntity":
        obj = CausalEntity(
            name="checkout-service", entity_type=EntityType.SERVICE, domain="devops",
            created_by=_PLACEHOLDER, source_data=source,
        )
    elif kind == "CausalEdge":
        obj = CausalEdge(
            source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
            target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
            edge_type=EdgeType.CAUSES, direction=EdgeDirection.FORWARD, confidence=0.7,
            guardian_verdict_ref=uuid4(), domain="devops", source_data=source,
        )
    else:
        obj = GraphFragment(
            name=f"deploys-{uuid4().hex[:6]}", domain="devops", created_by=_PLACEHOLDER,
            source_data=source,
        )
    return obj.model_dump(mode="json")


async def _submit(kind: str, source: DataSource | None = SOURCE) -> tuple[str, str]:
    """Draft through the API as AUTHOR. Returns (object id, draft verdict id)."""
    async with _client(AUTHOR) as client:
        resp = await client.post(
            "/api/semantic/drafts",
            json={
                "object_type": kind,
                "payload": _payload(kind, source),
                "cynefin_domain": "COMPLICATED",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["object_id"], body["audit_entry_id"]


async def _check(object_id: str, kind: str) -> str:
    async with _client(GOVERNANCE) as client:
        resp = await client.post(
            "/api/governance/check",
            json={
                "action_type": "GOVERNANCE_APPROVE",
                "payload": {"object_id": object_id, "object_type": kind},
                "subject_id": object_id,
                "cynefin_domain": "COMPLICATED",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "APPROVED", resp.json()
    return str(resp.json()["audit_entry_id"])


async def _approve(object_id: str, kind: str, verdict: str) -> Any:
    async with _client(GOVERNANCE) as client:
        return await client.post(
            f"/api/governance/approve/{object_id}",
            json={"object_type": kind, "verdict_id": verdict, "notes": "evidence reviewed"},
        )


async def _lineage(user: dict[str, Any], kind: str, object_id: str) -> Any:
    async with _client(user) as client:
        return await client.get(f"/api/semantic/objects/{kind}/{object_id}/lineage")


# ── The whole chain ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["CausalEntity", "CausalEdge", "GraphFragment"])
async def test_an_approved_object_carries_lineage_from_source_to_approver(
    kind: str, db_engine: object
) -> None:
    object_id, draft_verdict = await _submit(kind)
    session = f"api-draft-{AUTHOR['uid']}"
    record = ProvenanceRecord(session_id=session, agent_id="api-draft-submission")
    await ProvenanceStore().append([record])
    approve_verdict = await _check(object_id, kind)
    resp = await _approve(object_id, kind, approve_verdict)
    assert resp.status_code == 200, resp.text

    got = await _lineage(GOVERNANCE, kind, object_id)
    assert got.status_code == 200, got.text
    chain = LineageChain.model_validate(got.json())

    assert chain.object_type == kind and str(chain.object_id) == object_id
    assert chain.approval_status == "APPROVED" and chain.revision == 2

    # Source data, by reference and digest.
    assert chain.source_data == SOURCE

    # Who drafted it, and through what.
    assert chain.drafted_by is not None
    assert chain.drafted_by.person is not None
    assert chain.drafted_by.person.id == AUTHOR["uid"]
    assert chain.drafted_by.agent_id == "api-draft-submission"
    assert chain.drafted_by.agent_version
    assert chain.drafted_by.revision == 1

    # Every verdict, with what it was spent on and the rules it cited.
    by_id = {str(v.id): v for v in chain.verdicts}
    assert draft_verdict in by_id and approve_verdict in by_id
    drafted, approved = by_id[draft_verdict], by_id[approve_verdict]
    assert drafted.action_type == "DRAFT_SUBMISSION"
    assert drafted.spent_on_revision == 1
    assert approved.action_type == "GOVERNANCE_APPROVE"
    assert approved.spent_on_revision == 2
    for verdict in (drafted, approved):
        assert verdict.guardian_verdict == "APPROVED"
        assert verdict.signed is True
        assert verdict.agent_id
        assert isinstance(verdict.policy_rules_cited, list)
    decisions = [v for v in chain.verdicts if v.action_type == "HUMAN_APPROVAL"]
    assert len(decisions) == 1
    assert decisions[0].signed is False and decisions[0].notes == "evidence reviewed"

    # The approver.
    assert chain.approved_by is not None
    assert chain.approved_by.person is not None
    assert chain.approved_by.person.id == GOVERNANCE["uid"]
    assert str(chain.approved_by.verdict_id) == approve_verdict
    assert chain.approved_by.revision == 2

    # The history it was assembled from, and the provenance it links to.
    assert [(s.revision, s.change) for s in chain.steps] == [(1, "PROPOSED"), (2, "APPROVED")]
    assert session in chain.provenance_sessions
    assert record.id in chain.provenance_records

    assert chain.gaps == []
    assert chain.complete is True

    async with _client(GOVERNANCE) as client:
        history = await client.get(f"/api/semantic/objects/{kind}/{object_id}/versions")
    assert history.status_code == 200, history.text
    versions = [SemanticObjectVersion.model_validate(v) for v in history.json()]
    assert [v.revision for v in versions] == [1, 2]
    assert versions[0].state["approval_status"] == "DRAFT"


# ── Approval needs the chain's head ──────────────────────────────────────────


async def test_an_object_with_no_source_data_cannot_be_approved(
    db_engine: object, db_session: AsyncSession
) -> None:
    object_id, _ = await _submit("CausalEntity", source=None)
    approve_verdict = await _check(object_id, "CausalEntity")

    resp = await _approve(object_id, "CausalEntity", approve_verdict)
    assert resp.status_code == 422, resp.text
    assert "source" in resp.json()["detail"]

    spent = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM verdict_consumption WHERE verdict_id = :v"),
            {"v": approve_verdict},
        )
    ).scalar_one()
    assert spent == 0, "a refused approval must not burn its verdict"

    chain = LineageChain.model_validate(
        (await _lineage(GOVERNANCE, "CausalEntity", object_id)).json()
    )
    assert chain.approval_status == "DRAFT"
    assert chain.source_data is None
    assert chain.approved_by is None
    assert any("source" in gap for gap in chain.gaps)
    assert chain.complete is False


async def test_an_object_approved_before_lineage_was_recorded_says_what_is_missing(
    db_engine: object, db_session: AsyncSession
) -> None:
    entity_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO causal_entities (id, name, entity_type, domain, attributes, "
            " approval_status, created_by, created_at, approved_by, approved_at) "
            "VALUES (:id, 'legacy', 'SERVICE', 'devops', '{}', 'APPROVED', "
            " '{\"id\": \"legacy-author\"}', '2026-01-01T00:00:00+00:00', "
            " '{\"id\": \"legacy-approver\"}', '2026-01-02T00:00:00+00:00')"
        ),
        {"id": str(entity_id)},
    )
    await db_session.commit()

    got = await _lineage(GOVERNANCE, "CausalEntity", str(entity_id))
    assert got.status_code == 200, got.text
    chain = LineageChain.model_validate(got.json())
    assert chain.complete is False
    assert any("source" in gap for gap in chain.gaps)
    assert any("history" in gap for gap in chain.gaps)
    # What the row itself says is still reported, attributed as such.
    assert chain.drafted_by is not None and chain.drafted_by.person is not None
    assert chain.drafted_by.person.id == "legacy-author"
    assert chain.approved_by is not None and chain.approved_by.person is not None
    assert chain.approved_by.person.id == "legacy-approver"
    assert chain.approved_by.verdict_id is None


# ── Scope ────────────────────────────────────────────────────────────────────


async def test_lineage_and_history_are_scoped_reads(db_engine: object) -> None:
    object_id, _ = await _submit("CausalEntity")

    for path in ("lineage", "versions"):
        url = f"/api/semantic/objects/CausalEntity/{object_id}/{path}"
        async with _client(OUTSIDER) as client:
            assert (await client.get(url)).status_code == 404
        async with _client(VIEWER) as client:
            assert (await client.get(url)).status_code == 404, "a viewer reads approved only"
        async with _client(AUTHOR) as client:
            assert (await client.get(url)).status_code == 200

    resp = await _approve(object_id, "CausalEntity", await _check(object_id, "CausalEntity"))
    assert resp.status_code == 200, resp.text
    async with _client(VIEWER) as client:
        assert (
            await client.get(f"/api/semantic/objects/CausalEntity/{object_id}/lineage")
        ).status_code == 200

    async with _client(GOVERNANCE) as client:
        assert (
            await client.get(f"/api/semantic/objects/Treatment/{object_id}/lineage")
        ).status_code == 422
        assert (
            await client.get(f"/api/semantic/objects/CausalEntity/{uuid4()}/versions")
        ).status_code == 404


async def test_a_draft_without_a_classified_domain_is_still_refused(db_engine: object) -> None:
    async with _client(AUTHOR) as client:
        resp = await client.post(
            "/api/semantic/drafts",
            json={"object_type": "CausalEntity", "payload": _payload("CausalEntity")},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["verdict"] == "REJECTED"


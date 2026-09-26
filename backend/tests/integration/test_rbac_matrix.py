"""
The role matrix, and domain isolation, over HTTP.

Register entry `rbac-matrix-and-domain-isolation`: two role sets guarded seven
routes, and every other route -- including DELETE of a session's provenance (a
route since replaced by a tombstoning POST .../erasure) -- needed only a valid
token. `src/middleware/rbac.py` now holds one matrix,
ROUTE_ROLES, and each guarded route depends on its row.

Preconditions
  - The app is `src.api.main.app`; Firebase verification is patched to return a
    chosen claims dict, so the token is whatever the test says it is.
  - The SQLite test engine (`db_engine`) is live for every request.

Postconditions (from CYBERSECURITY.md §4.0-§4.2 and the register's `leaves_by`)
  1. Every mounted route but PUBLIC_ROUTES (the liveness probe) has a matrix
     row and depends on that row's guard -- reads included, so a token holding
     no platform role reads nothing. A matrix row names a mounted route.
  2. For every matrix row, a token holding no role the row grants gets 403; a
     token holding a granted role does not get 403.
  3. A user whose `domains` claim is one domain reads no semantic object of
     another domain through the API -- not by list, not by id, not through the
     review queue -- and cannot draft into, approve or reject in another domain.
  4. No `domains` claim: every domain for governance_officer/platform_engineer,
     no domain for any other role (fail closed).
  5. A viewer reads APPROVED semantic objects only.

Undefined here (no assertions)
  - The status a granted request gets when it is not 403: 200, 404 or 422
    depending on the body, which these tests do not try to make valid.
  - The order of listed objects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.middleware.auth import get_current_user
from src.middleware.rbac import ALL_ROLES, PUBLIC_ROUTES, ROUTE_ROLES, domain_scope, guard
from src.semantic_layer.provenance import ProvenanceRecord, ProvenanceRecordType
from src.semantic_layer.provenance_store import ProvenanceStore
from tests.conftest import (
    make_causal_edge,
    make_causal_entity,
    make_graph_fragment,
    make_user_ref,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.registry import SemanticLayerRegistry
    from tests.conftest import VerdictFactory

pytestmark = pytest.mark.asyncio

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ── Walking the mounted app ───────────────────────────────────────────────────


def mounted_routes(application: FastAPI) -> Iterator[tuple[str, str, APIRoute]]:
    """(method, full path, route) for every API route the app serves.

    FastAPI 0.141 keeps an included router behind a wrapper whose include context
    holds the prefix; older versions flattened it. Handle both.
    """

    def walk(routes: list[Any], prefix: str) -> Iterator[tuple[str, APIRoute]]:
        for r in routes:
            if isinstance(r, APIRoute):
                yield prefix + r.path, r
            elif getattr(r, "original_router", None) is not None:
                ctx = getattr(r, "include_context", None)
                inner = prefix + (getattr(ctx, "prefix", "") or "")
                yield from walk(r.original_router.routes, inner)

    for path, route in walk(list(application.routes), ""):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, path, route


def depends_on(route: APIRoute, dependency: Any) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is dependency:
            return True
        stack.extend(d.dependencies)
    return False


def unguarded(application: FastAPI) -> list[str]:
    """Non-public routes without a matrix row, or not depending on their row."""
    out = []
    for method, path, route in mounted_routes(application):
        key = (method, path)
        if key in PUBLIC_ROUTES:
            continue
        if key not in ROUTE_ROLES or not depends_on(route, guard(method, path)):
            out.append(f"{method} {path}")
    return out


def test_every_route_but_the_public_ones_is_guarded_by_its_matrix_row() -> None:
    assert unguarded(app) == []
    served = {(m, p) for m, p, _ in mounted_routes(app)}
    assert sum(1 for m, _ in served if m in STATE_CHANGING) >= 17
    assert sum(1 for m, _ in served if m == "GET") >= 30
    assert served >= PUBLIC_ROUTES


def test_every_matrix_row_is_a_mounted_route_that_depends_on_it() -> None:
    routes = {(m, p): r for m, p, r in mounted_routes(app)}
    stale = [k for k in ROUTE_ROLES if k not in routes]
    assert stale == [], f"matrix rows for routes the app does not serve: {stale}"
    loose = [k for k, r in routes.items() if k in ROUTE_ROLES and not depends_on(r, guard(*k))]
    assert loose == [], f"routes with a matrix row that do not depend on it: {loose}"


def test_the_coverage_check_bites_on_an_unguarded_route() -> None:
    """Negative control: routes on get_current_user alone are reported, reads too."""
    probe = FastAPI()

    @probe.post("/api/semantic/drafts")
    async def open_route(user: dict[str, Any] = Depends(get_current_user)) -> None: ...

    @probe.post("/api/v1/not-in-the-matrix")
    async def unknown(user: dict[str, Any] = Depends(get_current_user)) -> None: ...

    @probe.get("/api/v1/incidents/")
    async def open_read(user: dict[str, Any] = Depends(get_current_user)) -> None: ...

    @probe.get("/health")
    async def health() -> None: ...

    assert unguarded(probe) == [
        "POST /api/semantic/drafts",
        "POST /api/v1/not-in-the-matrix",
        "GET /api/v1/incidents/",
    ]


def test_guard_refuses_a_key_the_matrix_does_not_hold() -> None:
    with pytest.raises(KeyError):
        guard("POST", "/api/v1/nowhere")


# ── Per-route: refused without a granted role, admitted with one ─────────────


def _client(user: dict[str, Any]) -> Any:
    class _Ctx:
        async def __aenter__(self) -> AsyncClient:
            self._patch = patch(
                "src.middleware.auth._verify_firebase_token",
                new_callable=AsyncMock,
                return_value=user,
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

    return _Ctx()


def _concrete(path: str) -> str:
    def fill(m: re.Match[str]) -> str:
        return str(uuid4()) if m.group(0).endswith("_id}") else "x"

    return re.sub(r"\{[^}]+\}", fill, path)


async def _call(client: AsyncClient, method: str, path: str) -> Any:
    url = _concrete(path)
    if method in {"POST", "PUT", "PATCH"}:
        return await client.request(method, url, json={})
    return await client.request(method, url)


ROWS = sorted(ROUTE_ROLES.items())


@pytest.mark.parametrize(("key", "granted"), ROWS, ids=[f"{m} {p}" for (m, p), _ in ROWS])
async def test_a_token_without_a_granted_role_is_refused(
    key: tuple[str, str], granted: tuple[str, ...], db_engine: object
) -> None:
    method, path = key
    others = [r for r in ALL_ROLES if r not in granted]
    # Every role the row does not grant, plus a role the platform does not know.
    user = {"uid": "denied", "roles": [*others, "superuser"], "role": "admin"}
    async with _client(user) as client:
        resp = await _call(client, method, path)
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text[:200]}"
    assert "Insufficient permissions" in resp.text


@pytest.mark.parametrize(("key", "granted"), ROWS, ids=[f"{m} {p}" for (m, p), _ in ROWS])
async def test_each_granted_role_is_admitted(
    key: tuple[str, str], granted: tuple[str, ...], db_engine: object
) -> None:
    method, path = key
    for role in granted:
        async with _client({"uid": f"u-{role}", "roles": [role], "domains": ["devops"]}) as client:
            resp = await _call(client, method, path)
        assert resp.status_code != 403, f"{method} {path} as {role}: {resp.text[:200]}"


async def test_a_token_with_no_roles_is_refused_on_every_guarded_route(
    db_engine: object,
) -> None:
    """Reads included: the incidents list, the intervention decisions (reason and
    decided_by), the decision ledger and the metrics all answered a role-less
    token with 200 before they had matrix rows."""
    async with _client({"uid": "nobody", "roles": []}) as client:
        for method, path in ROUTE_ROLES:
            resp = await _call(client, method, path)
            assert resp.status_code == 403, f"{method} {path}: {resp.status_code}"


# ── The two routes nothing else calls ─────────────────────────────────────────


async def _seed_provenance(session_id: str) -> None:
    await ProvenanceStore().append(
        [
            ProvenanceRecord(
                record_type=ProvenanceRecordType.ENTITY,
                session_id=session_id,
                agent_id="test-agent",
            )
        ]
    )


async def test_provenance_is_erased_only_by_a_governance_role(db_engine: object) -> None:
    """Erasure is a tombstone (tests/integration/test_provenance_append_only.py);
    who may write one is the matrix's business."""
    sid = f"prov-{uuid4()}"
    await _seed_provenance(sid)
    path = f"/api/v1/provenance/session/{sid}/erasure"
    reason = {"reason": "Role-matrix test: hide this session."}
    for role in ("analyst", "graph_author", "sre", "agent", "viewer"):
        async with _client({"uid": role, "roles": [role]}) as client:
            resp = await client.post(path, json=reason)
        assert resp.status_code == 403
        view = await ProvenanceStore().read(sid)
        assert len(view.records) == 1 and not view.erasures, "a refused erasure hid records"

    async with _client({"uid": "pe", "roles": ["platform_engineer"]}) as client:
        resp = await client.post(path, json=reason)
    assert resp.status_code == 201
    view = await ProvenanceStore().read(sid)
    assert view.records == [] and [e.erased_by for e in view.erasures] == ["pe"]


async def test_calibration_probe_needs_a_platform_role(db_engine: object) -> None:
    body = {"confidences": [0.2, 0.8]}
    async with _client({"uid": "nobody"}) as client:
        refused = await client.post("/api/v1/platform/evaluation/calibration", json=body)
    async with _client({"uid": "v", "roles": ["viewer"]}) as client:
        admitted = await client.post("/api/v1/platform/evaluation/calibration", json=body)
    assert refused.status_code == 403
    assert admitted.status_code == 200


# ── Domain scope from the token ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"roles": ["governance_officer"]}, None),
        ({"roles": ["platform_engineer"]}, None),
        ({"roles": ["platform_engineer"], "domains": ["devops"]}, frozenset({"devops"})),
        ({"roles": ["analyst"]}, frozenset()),
        ({"roles": ["agent"]}, frozenset()),
        ({"roles": ["sre"], "domains": "devops"}, frozenset({"devops"})),
        ({"roles": ["analyst"], "domains": {"not": "a list"}}, frozenset()),
        ({"roles": ["analyst"], "domains": ["devops", 3, ""]}, frozenset({"devops"})),
    ],
)
def test_domain_scope_is_fail_closed_except_for_unscoped_roles(
    user: dict[str, Any], expected: frozenset[str] | None
) -> None:
    assert domain_scope(user) == expected


# ── Domain isolation over the API ─────────────────────────────────────────────


@pytest.fixture
async def two_domains(
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: Any,
) -> AsyncIterator[dict[str, dict[str, str]]]:
    """One entity, one edge and one fragment in each of devops and supply_chain, as DRAFTs."""
    out: dict[str, dict[str, str]] = {}
    for domain in ("devops", "supply_chain"):
        entity = make_causal_entity(domain=domain)
        edge = make_causal_edge(approved_audit_entry_id, domain=domain)
        fragment = make_graph_fragment(domain=domain)
        for obj in (entity, edge, fragment):
            await registry.propose_draft(obj, await verdict_for(obj.id))
        out[domain] = {
            "entity": str(entity.id),
            "edge": str(edge.id),
            "fragment": str(fragment.id),
        }
    yield out


DEVOPS_ANALYST = {"uid": "a-dev", "roles": ["analyst"], "domains": ["devops"]}
DEVOPS_GOVERNANCE = {"uid": "g-dev", "roles": ["governance_officer"], "domains": ["devops"]}


async def test_a_devops_user_lists_only_devops_objects(
    two_domains: dict[str, dict[str, str]],
) -> None:
    async with _client(DEVOPS_ANALYST) as client:
        entities = (await client.get("/api/semantic/entities")).json()
        fragments = (await client.get("/api/semantic/fragments")).json()
        asked = (
            await client.get("/api/semantic/entities", params={"domain": "supply_chain"})
        ).json()
    assert {e["domain"] for e in entities} == {"devops"}
    assert {f["domain"] for f in fragments} == {"devops"}
    assert asked == []
    seen = {e["id"] for e in entities} | {f["id"] for f in fragments}
    assert not seen & set(two_domains["supply_chain"].values())


async def test_a_devops_user_cannot_read_another_domain_by_id(
    two_domains: dict[str, dict[str, str]],
) -> None:
    other = two_domains["supply_chain"]
    mine = two_domains["devops"]
    async with _client(DEVOPS_ANALYST) as client:
        for url in (
            f"/api/semantic/entities/{other['entity']}",
            f"/api/semantic/edges/{other['edge']}",
            f"/api/semantic/edges/{other['edge']}/audit-trail",
            f"/api/semantic/fragments/{other['fragment']}",
        ):
            resp = await client.get(url)
            assert resp.status_code == 404, url
        for url in (
            f"/api/semantic/entities/{mine['entity']}",
            f"/api/semantic/edges/{mine['edge']}",
            f"/api/semantic/fragments/{mine['fragment']}",
        ):
            assert (await client.get(url)).status_code == 200, url


async def test_the_review_queue_shows_only_the_reviewers_domains(
    two_domains: dict[str, dict[str, str]],
) -> None:
    async with _client(DEVOPS_GOVERNANCE) as client:
        items = (await client.get("/api/governance/review-queue")).json()
    assert items, "the devops drafts are pending"
    assert {i["domain"] for i in items} == {"devops"}

    async with _client({"uid": "g-all", "roles": ["governance_officer"]}) as client:
        items = (await client.get("/api/governance/review-queue")).json()
    assert {i["domain"] for i in items} == {"devops", "supply_chain"}


async def test_no_domains_claim_sees_nothing_unless_unscoped(
    two_domains: dict[str, dict[str, str]],
) -> None:
    async with _client({"uid": "a", "roles": ["analyst"]}) as client:
        assert (await client.get("/api/semantic/entities")).json() == []
        entity = two_domains["devops"]["entity"]
        assert (await client.get(f"/api/semantic/entities/{entity}")).status_code == 404
    async with _client({"uid": "pe", "roles": ["platform_engineer"]}) as client:
        domains = {e["domain"] for e in (await client.get("/api/semantic/entities")).json()}
    assert domains == {"devops", "supply_chain"}


async def test_a_devops_user_cannot_draft_into_another_domain(
    db_engine: object, db_session: AsyncSession
) -> None:
    before = (await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    entity = make_causal_entity(domain="supply_chain").model_dump(mode="json")
    async with _client(DEVOPS_ANALYST) as client:
        resp = await client.post(
            "/api/semantic/drafts", json={"object_type": "CausalEntity", "payload": entity}
        )
    assert resp.status_code == 403
    assert "domain scope" in resp.text
    after = (await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    assert after == before, "an out-of-scope draft reached the Guardian"
    rows = (await db_session.execute(text("SELECT COUNT(*) FROM causal_entities"))).scalar()
    assert rows == 0


async def test_a_devops_reviewer_cannot_rule_on_another_domain(
    two_domains: dict[str, dict[str, str]],
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    other = two_domains["supply_chain"]["entity"]
    from uuid import UUID

    approve_verdict = await verdict_for(UUID(other), action_type="GOVERNANCE_APPROVE")
    reject_verdict = await verdict_for(UUID(other), action_type="GOVERNANCE_REJECT")
    async with _client(DEVOPS_GOVERNANCE) as client:
        approve = await client.post(
            f"/api/governance/approve/{other}",
            json={"object_type": "CausalEntity", "verdict_id": str(approve_verdict)},
        )
        reject = await client.post(
            f"/api/governance/reject/{other}",
            json={
                "object_type": "CausalEntity",
                "verdict_id": str(reject_verdict),
                "reason": "outside my domain, supposedly",
            },
        )
    assert approve.status_code == 404
    assert reject.status_code == 404
    status = (
        await db_session.execute(
            text("SELECT approval_status FROM causal_entities WHERE id = :i"), {"i": other}
        )
    ).scalar()
    assert status == "DRAFT"


async def test_a_viewer_reads_approved_objects_only(
    two_domains: dict[str, dict[str, str]],
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    from uuid import UUID

    approved = two_domains["devops"]["entity"]
    draft = two_domains["devops"]["fragment"]
    await registry.promote_to_approved(
        UUID(approved),
        "CausalEntity",
        await verdict_for(UUID(approved), action_type="GOVERNANCE_APPROVE"),
        make_user_ref("gov"),
    )
    viewer = {"uid": "v", "roles": ["viewer"], "domains": ["devops"]}
    async with _client(viewer) as client:
        entities = (await client.get("/api/semantic/entities")).json()
        drafts = (await client.get("/api/semantic/entities", params={"status": "DRAFT"})).json()
        by_id = await client.get(f"/api/semantic/entities/{approved}")
        fragment = await client.get(f"/api/semantic/fragments/{draft}")
        fragments = (await client.get("/api/semantic/fragments")).json()
        edge = await client.get(f"/api/semantic/edges/{two_domains['devops']['edge']}")
    assert [e["id"] for e in entities] == [approved]
    assert drafts == []
    assert by_id.status_code == 200
    assert fragment.status_code == 404
    assert fragments == []
    assert edge.status_code == 404

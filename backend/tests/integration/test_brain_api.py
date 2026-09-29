"""The second-brain routes, and decisions reported back to agentcenter.

A recorded decision is sent to agentcenter's event log with where its question
came from (`origin`), after the ledger seal; agentcenter being down changes
nothing about the recorded decision.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.analysis_examples import example
from src.api.main import app
from src.connectors import agentcenter as ac
from src.middleware.rbac import ALL_ROLES, ROUTE_ROLES

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "brain-test", "email": "b@test.com", "roles": ["analyst"]}


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token", new_callable=AsyncMock, return_value=_USER
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac_client:
            yield ac_client


@pytest.fixture
def brain_on(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/events/emit":
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"event_id": "e"})
        if req.url.path == "/api/hypotheses":
            return httpx.Response(
                200,
                json={"hypotheses": [{"hyp_id": "H-3", "spec": {"binding": {"builder": "none"}}}]},
            )
        return httpx.Response(200, json={})

    monkeypatch.setenv("AGENTCENTER_URL", "http://ac.test")
    monkeypatch.setenv("ORACLE_URL", "http://oracle.test")
    monkeypatch.setenv("AGENTCENTER_EMIT", "true")
    monkeypatch.setattr(ac, "_transport", httpx.MockTransport(respond))
    return sent


def _decide_body(**over: Any) -> dict[str, Any]:
    ex = example("checkout-ab-test")
    assert ex is not None
    body = {
        "action": "ship the new checkout",
        "graph": ex.graph,
        "treatment": ex.treatment,
        "outcome": ex.outcome,
        "direction": "increase",
        "data": ex.columns,
        "query": {"design": "randomized"},
        "minimum_worthwhile_effect": 0.01,
    }
    body.update(over)
    return body


async def test_a_recorded_decision_reaches_agentcenter_with_its_origin(
    client: AsyncClient, brain_on: list[dict[str, Any]]
) -> None:
    resp = await client.post("/api/v1/decisions", json=_decide_body(origin="oracle:H-01"))
    assert resp.status_code == 200, resp.text
    record = resp.json()["record"]
    assert len(brain_on) == 1
    event = brain_on[0]
    assert event["kind"] == "causalplatform.decision.recorded"
    assert event["payload"]["entry_digest"] == record["entry_digest"]
    assert event["payload"]["origin"] == "oracle:H-01"
    assert event["payload"]["verdict"] == resp.json()["verdict"]
    assert event["payload"]["benefit"] == pytest.approx(
        resp.json()["decision"]["evidence"]["benefit"]
    )


async def test_agentcenter_down_leaves_the_decision_recorded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    monkeypatch.setenv("AGENTCENTER_URL", "http://ac.test")
    monkeypatch.setenv("AGENTCENTER_EMIT", "true")
    monkeypatch.setattr(ac, "_transport", httpx.MockTransport(down))
    before = (await client.get("/api/v1/decisions")).json()["total"]
    resp = await client.post("/api/v1/decisions", json=_decide_body())
    assert resp.status_code == 200
    assert (await client.get("/api/v1/decisions")).json()["total"] == before + 1


async def test_suggestions_report_disabled_without_configuration(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTCENTER_URL", raising=False)
    body = (await client.get("/api/v1/brain/suggestions")).json()
    assert body["enabled"] is False and body["suggestions"] == []


async def test_data_route_maps_unavailable_to_409_and_unknown_to_404(
    client: AsyncClient, brain_on: list[dict[str, Any]]
) -> None:
    assert (await client.get("/api/v1/brain/suggestions/H-3/data")).status_code == 409
    assert (await client.get("/api/v1/brain/suggestions/H-404/data")).status_code == 404


def test_brain_routes_are_open_to_every_role() -> None:
    assert ROUTE_ROLES[("GET", "/api/v1/brain/suggestions")] == ALL_ROLES
    assert ROUTE_ROLES[("GET", "/api/v1/brain/suggestions/{hyp_id}/data")] == ALL_ROLES

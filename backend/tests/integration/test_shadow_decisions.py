"""Shadow decisions at runtime, through the API (septa-95).

A shadow decision (shadow/README.md) is analysed, ruled on and sealed like any other,
but nobody acts on it because of the platform: it is never emitted to agentcenter as
`causalplatform.decision.recorded`, it is listed apart from real decisions, and it
gets its follow-up like any other.

Preconditions: a clean database per test (db_engine); agentcenter replaced by an
httpx.MockTransport that records every event it receives, with emitting ON -- so the
only thing standing between a shadow decision and agentcenter is the shadow guard.

Postconditions:
  - POST /api/v1/decisions with `shadow` seals the tag into the ledger entry (and its
    index column) and sends no event; the real decisions around it each send one;
  - GET /api/v1/decisions lists real decisions by default, shadow ones with
    `scope=shadow` or their `tag`, both with `scope=all`; /verify recomputes the one
    chain that holds both;
  - a shadow decision's follow-up is taken like any other; the calibration report is
    over real decisions by default and over shadow ones only when asked;
  - a tag is an identifier: prose is refused with 422.

Negative control: delete the `emittable(record)` guard in
`src/connectors/agentcenter.py::emit_decision` and the first test fails (the fake
agentcenter receives the shadow decision).

Undefined here: the order of events beyond one per real decision, and the verdicts
(the example data decides them; the tests compare against what each response said).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.connectors import agentcenter as ac
from tests.integration.test_decision_followup import body, window

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "shadow-test", "email": "s@test.com", "roles": ["sre"]}
TAG = "shadow-round-1"


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token", new_callable=AsyncMock, return_value=_USER
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as c:
            yield c


@pytest.fixture
def agentcenter(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A fake agentcenter with emitting on; returns the events it received."""
    received: list[dict[str, Any]] = []

    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/events/emit":
            received.append(json.loads(req.content))
            return httpx.Response(200, json={"event_id": "e"})
        return httpx.Response(404)

    monkeypatch.setenv("AGENTCENTER_URL", "http://ac.test")
    monkeypatch.setenv("AGENTCENTER_EMIT", "true")
    monkeypatch.setattr(ac, "_transport", httpx.MockTransport(respond))
    return received


async def _decide(client: AsyncClient, **over: Any) -> dict[str, Any]:
    resp = await client.post("/api/v1/decisions", json=body(**over))
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["record"])


async def test_a_shadow_decision_is_sealed_listed_apart_and_never_emitted(
    client: AsyncClient, agentcenter: list[dict[str, Any]], db_session: AsyncSession
) -> None:
    first = await _decide(client)
    shadow = await _decide(client, shadow=TAG, origin="oracle:H-07")
    last = await _decide(client, data=window(seed=3))

    assert first["shadow"] is None and last["shadow"] is None
    assert shadow["shadow"] == TAG  # sealed: part of what entry_digest covers
    emitted = [e["payload"]["entry_digest"] for e in agentcenter]
    assert emitted == [first["entry_digest"], last["entry_digest"]]
    assert all(e["kind"] == "causalplatform.decision.recorded" for e in agentcenter)

    column = (
        await db_session.execute(
            text("SELECT sequence, shadow FROM decision_records ORDER BY sequence")
        )
    ).all()
    assert [tuple(r) for r in column] == [(0, None), (1, TAG), (2, None)]

    async def listed(**params: str) -> tuple[list[int], dict[str, Any]]:
        resp = await client.get("/api/v1/decisions", params=params)
        assert resp.status_code == 200, resp.text
        got = resp.json()
        assert got["total"] == len(got["records"])
        return sorted(r["sequence"] for r in got["records"]), got

    seqs, real = await listed()
    assert seqs == [0, 2] and real["scope"] == "real"
    assert (await listed(scope="shadow"))[0] == [1]
    assert (await listed(scope="all"))[0] == [0, 1, 2]
    seqs, tagged = await listed(tag=TAG)
    assert seqs == [1] and tagged["scope"] == "shadow" and tagged["tag"] == TAG
    assert (await listed(tag="shadow-round-2"))[0] == []
    action = body()["action"]
    assert (await listed(action=action))[0] == [0, 2]
    assert (await listed(action=action, scope="shadow"))[0] == [1]

    chain = (await client.get("/api/v1/decisions/verify")).json()
    assert chain["ok"] and chain["length"] == 3


async def test_a_shadow_decision_gets_a_follow_up_and_stays_out_of_real_calibration(
    client: AsyncClient, agentcenter: list[dict[str, Any]]
) -> None:
    real = await _decide(client)
    # Different data from the real one: two decisions on identical inputs share a
    # decision_digest, and the calibration report then refuses them as one decision
    # measured twice -- a gap that predates shadow mode (septa-135).
    shadow = await _decide(client, shadow=TAG, data=window(seed=5))
    for entry, seed in ((real["entry_digest"], 1), (shadow["entry_digest"], 2)):
        resp = await client.post(
            f"/api/v1/decisions/{entry}/followup", json={"data": window(seed=seed)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["realization"]["decidable"] is True

    async def calibrated(**params: str) -> int:
        resp = await client.get("/api/v1/decisions/calibration", params=params)
        assert resp.status_code == 200, resp.text
        return int(resp.json()["n_decisions"])

    assert await calibrated() == 1  # real only, by default
    assert await calibrated(scope="shadow") == 1
    assert await calibrated(tag=TAG) == 1
    assert await calibrated(scope="all") == 2
    assert len(agentcenter) == 1  # the follow-up sent nothing for the shadow one either


async def test_a_shadow_tag_is_an_identifier(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/decisions", json=body(shadow="round one; drop table"))
    assert resp.status_code == 422
    assert (await client.get("/api/v1/decisions", params={"tag": "a b"})).status_code == 422
    assert (await client.get("/api/v1/decisions", params={"scope": "both"})).status_code == 422
    assert (await client.get("/api/v1/decisions")).json()["total"] == 0

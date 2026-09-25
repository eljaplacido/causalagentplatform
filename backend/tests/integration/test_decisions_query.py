"""Everything the analysis can be asked, it can be asked over HTTP.

`POST /api/v1/decisions` used to re-declare the query as a flat copy that had
fallen eleven fields behind the kernel's CausalQuery -- `segment_by` among them.
So the segment-aware verdict (a pooled saving that hides a harmed subgroup must
not be acted on) worked in the library and over MCP, and was unreachable here,
while the register recorded `decision-ignores-segments` as closed. And because
the request ignored unknown fields, a caller who sent `segment_by` anyway got the
pooled verdict with no sign their declaration had been dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio
from causal_core.contract import CausalQuery
from httpx import ASGITransport, AsyncClient

from src.api.decisions import _QUERY_FIELDS_SET_HERE, DecideRequest, QueryOptions
from src.api.main import app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "query-test", "email": "q@test.com", "roles": ["sre"]}
GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def incidents() -> dict[str, list[Any]]:
    """-20 minutes on P1 (65% of incidents), +6 on P3: a real pooled saving, and a harm."""
    rng = np.random.default_rng(11)
    n = 4000
    severity = np.where(rng.random(n) < 0.65, "P1", "P3")
    load = rng.normal(0, 1, n)
    playbook = rng.binomial(1, 1 / (1 + np.exp(-0.9 * load))).astype(float)
    effect = np.where(severity == "P1", -20.0, 6.0)
    minutes = 60 + 8.0 * load + effect * playbook + rng.normal(0, 10, n)
    return {
        "load": load.tolist(),
        "playbook": playbook.tolist(),
        "minutes": minutes.tolist(),
        "severity": severity.tolist(),
    }


def body(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "apply remediation playbook X",
        "graph": GRAPH,
        "treatment": "playbook",
        "outcome": "minutes",
        "direction": "decrease",
        "data": incidents(),
        "risk_class": "low",
        "minimum_worthwhile_effect": 2.0,
    }
    payload.update(over)
    return payload


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token", new_callable=AsyncMock, return_value=_USER
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac:
            yield ac


async def test_a_harmed_segment_blocks_act_over_http(client: AsyncClient) -> None:
    blind = await client.post("/api/v1/decisions", json=body())
    assert blind.status_code == 200, blind.text
    assert blind.json()["verdict"] == "act", "premise: pooled, every check passes"

    seen = await client.post(
        "/api/v1/decisions", json=body(query={"segment_by": ["severity"]})
    )
    assert seen.status_code == 200, seen.text
    out = seen.json()
    assert out["verdict"] != "act", out["explanation"]
    harmed = [c for c in out["decision"]["checks"] if "materially harmed" in c["name"]]
    assert harmed and harmed[0]["passed"] is False
    assert out["outcome"]["kind"] == "result"
    assert {s["level"] for s in out["outcome"]["segments"]} == {"P1", "P3"}


async def test_an_unknown_field_is_refused_rather_than_dropped(client: AsyncClient) -> None:
    """How the bug stayed invisible: `segment_by` at the top level used to be ignored."""
    resp = await client.post("/api/v1/decisions", json=body(segment_by=["severity"]))
    assert resp.status_code == 422, resp.text


def test_every_causal_query_field_is_reachable() -> None:
    """Derived, not copied -- so a field added to the kernel arrives here by itself."""
    reachable = set(QueryOptions.model_fields) | (
        set(DecideRequest.model_fields) & _QUERY_FIELDS_SET_HERE
    )
    missing = set(CausalQuery.model_fields) - reachable - {"rung"}
    assert missing == set(), f"CausalQuery fields the HTTP API cannot set: {missing}"


def test_the_query_option_docs_name_every_option() -> None:
    """The `query` field's description once listed nine options by hand, six behind."""
    words = DecideRequest.model_fields["query"].description or ""
    unnamed = {name for name in QueryOptions.model_fields if name not in words}
    assert {"washout", "cluster", "sequence"} <= set(QueryOptions.model_fields)
    assert unnamed == set(), f"options the OpenAPI description does not name: {unnamed}"


async def test_a_staggered_rollout_is_decided_over_http(client: AsyncClient) -> None:
    """The panel design arrives with no endpoint change: the options derive from CausalQuery."""
    from causal_core.bench import t5

    frame = t5.simulate(t5.DESIGNS["staggered_constant"], 8, 0.5, np.random.default_rng(6))
    resp = await client.post(
        "/api/v1/decisions",
        json={
            "action": "keep the rollout",
            "graph": t5.GRAPH,
            "treatment": "rollout",
            "outcome": "latency",
            "direction": "increase",
            "data": {c: frame[c].tolist() for c in frame},
            "estimand": "att",
            "minimum_worthwhile_effect": 0.5,
            "query": {"design": "difference_in_differences", "unit": "unit", "time": "period"},
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["outcome"]["certificate"]["tier"] == "C"
    assert out["verdict"] == "act_with_approval", out["explanation"]
    assert out["outcome"]["event_study"]


async def test_a_watched_canary_is_decided_over_http(client: AsyncClient) -> None:
    """`sequence` arrives through the derived query options, like the panel fields did."""
    from causal_core.bench import t6

    frame = t6.simulate(t6.STREAMS["benefit"], 3000, np.random.default_rng(9))
    resp = await client.post(
        "/api/v1/decisions",
        json={
            "action": "promote the canary",
            "graph": t6.GRAPH,
            "treatment": "canary",
            "outcome": "latency",
            "direction": "increase",
            "data": {c: frame[c].tolist() for c in frame},
            "risk_class": "low",
            "query": {"design": "randomized", "sequence": "arrival",
                      "expected_treated_share": 0.5},
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["outcome"]["sequential"]["looks"]
    assert out["outcome"]["primary"]["ci"]["method"].startswith("asymptotic confidence sequence")

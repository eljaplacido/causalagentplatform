"""The assumption registry, end to end: versions, reviews, and what a revision breaks.

Before this, `POST /api/v1/decisions` took its graph in the request body and
nothing stored, versioned or retrieved the assumptions a verdict rested on. The
tests here walk the life of one model -- registered, revised, reviewed, decided
on, revised again -- and check the two things the registry exists for: that a
verdict names the exact version it used, and that a later revision says which
verdicts no longer stand.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app
from src.db.types import json_value

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

# Proposing a model is a draft (DRAFT_ROLES); deciding on it is sre's (LEDGER_ROLES).
AUTHOR = {"uid": "sre-alice", "email": "a@test.com", "roles": ["sre", "graph_author"]}
REVIEWER = {"uid": "gov-carol", "email": "c@test.com", "roles": ["governance_officer"]}
AUTHOR_WITH_ROLE = {"uid": "sre-alice", "email": "a@test.com", "roles": ["governance_officer"]}

V1 = "load -> playbook; load -> minutes; playbook -> minutes"
# backlog confounds the playbook and the outcome: the v1 adjustment set {load} is no
# longer enough if this graph is true.
V2 = V1 + "; backlog -> playbook; backlog -> minutes [origin=llm_prior]"


class _As:
    """Whose token the next request carries."""

    user: dict[str, Any] = AUTHOR


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    async def who(_token: str) -> dict[str, Any]:
        return _As.user

    with patch("src.middleware.auth._verify_firebase_token", new=AsyncMock(side_effect=who)):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac:
            _As.user = AUTHOR
            yield ac


def data(n: int = 3000, seed: int = 0) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    load = rng.normal(size=n)
    backlog = rng.normal(size=n)
    playbook = (0.9 * load + 0.7 * backlog + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 - 9.0 * playbook + 12 * load + 6 * backlog + rng.normal(0, 8, size=n)
    return {
        "load": load.tolist(),
        "backlog": backlog.tolist(),
        "playbook": playbook.tolist(),
        "minutes": minutes.tolist(),
    }


async def register(client: AsyncClient, name: str = "incident-remediation") -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/models", json={"name": name, "graph": V1, "why": "from the incident review"}
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def decide(client: AsyncClient, **model: Any) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/decisions",
        json={
            "action": "apply remediation playbook X",
            "model": {"name": "incident-remediation", **model},
            "treatment": "playbook",
            "outcome": "minutes",
            "direction": "decrease",
            "data": data(),
            "risk_class": "low",
            "minimum_worthwhile_effect": 2.0,
        },
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


# ── versions ──────────────────────────────────────────────────────────────────


async def test_a_model_is_registered_once_and_read_back(client: AsyncClient) -> None:
    v1 = await register(client)
    assert v1["version"] == 1 and v1["author_id"] == "sre-alice"
    assert v1["digest"].startswith("sha256:")

    again = await client.post(
        "/api/v1/models", json={"name": "incident-remediation", "graph": V1, "why": "twice"}
    )
    assert again.status_code == 409

    got = (await client.get("/api/v1/models/incident-remediation")).json()
    assert got["head"]["digest"] == v1["digest"]
    listed = (await client.get("/api/v1/models")).json()["models"]
    assert [m["name"] for m in listed] == ["incident-remediation"]


async def test_a_graph_the_kernel_cannot_analyse_is_refused(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/models", json={"name": "bad", "graph": "a -> b; b -> a", "why": "cyclic"}
    )
    assert resp.status_code == 422


async def test_a_revision_must_be_built_on_the_head(client: AsyncClient) -> None:
    v1 = await register(client)
    ok = await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={"parent_digest": v1["digest"], "graph": V2, "why": "backlog confounds both"},
    )
    assert ok.status_code == 201, ok.text
    stale = await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={"parent_digest": v1["digest"], "graph": V1, "why": "built on v1, head is v2"},
    )
    assert stale.status_code == 409

    d = (await client.get("/api/v1/models/incident-remediation/diff?from=1&to=2")).json()
    assert {(e["source"], e["target"]) for e in d["added_edges"]} == {
        ("backlog", "playbook"),
        ("backlog", "minutes"),
    }
    assert (await client.get("/api/v1/models/incident-remediation/verify")).json()["ok"]


async def test_a_revision_cannot_approve_an_edge(client: AsyncClient) -> None:
    v1 = await register(client)
    resp = await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={
            "parent_digest": v1["digest"],
            "graph": V1 + "; backlog -> minutes [status=approved]",
            "why": "approving my own edge",
        },
    )
    assert resp.status_code == 422
    assert "review" in resp.json()["detail"]


# ── reviews ───────────────────────────────────────────────────────────────────


async def test_reviews_are_role_gated_and_never_of_your_own_edges(client: AsyncClient) -> None:
    await register(client)
    v2 = (
        await client.post(
            "/api/v1/models/incident-remediation/versions",
            json={
                "parent_digest": (await client.get("/api/v1/models/incident-remediation"))
                .json()["head"]["digest"],
                "graph": V2,
                "why": "an LLM-proposed confounder",
            },
        )
    ).json()
    ruling = {
        "parent_digest": v2["digest"],
        "reviews": [
            {"edge": "backlog -> minutes", "status": "approved", "reason": "seen in the RCA"}
        ],
    }

    no_role = await client.post("/api/v1/models/incident-remediation/reviews", json=ruling)
    assert no_role.status_code == 403, "reviewing needs a governance role"

    _As.user = AUTHOR_WITH_ROLE
    own = await client.post("/api/v1/models/incident-remediation/reviews", json=ruling)
    assert own.status_code == 403, "a role does not let you approve an edge you introduced"
    assert "cannot review" in own.json()["detail"]

    _As.user = REVIEWER
    ok = await client.post("/api/v1/models/incident-remediation/reviews", json=ruling)
    assert ok.status_code == 201, ok.text
    v3 = ok.json()
    assert v3["version"] == 3 and v3["author_id"] == "gov-carol"
    heads = (await client.get("/api/v1/models")).json()["models"]
    assert heads[0]["unreviewed_edges"] == []


# ── decisions name the version; revisions say what they break ─────────────────


async def test_a_decision_names_the_version_it_rested_on(client: AsyncClient) -> None:
    v1 = await register(client)
    out = await decide(client)
    evidence = out["record"]["evidence"]
    assert evidence["model"] == {
        "name": "incident-remediation",
        "version": 1,
        "digest": v1["digest"],
    }
    assert (await client.get("/api/v1/decisions/verify")).json()["ok"]


async def test_graph_and_model_are_alternatives(client: AsyncClient) -> None:
    await register(client)
    base = {
        "action": "x",
        "treatment": "playbook",
        "outcome": "minutes",
        "direction": "decrease",
        "data": data(n=100),
    }
    both = await client.post(
        "/api/v1/decisions", json={**base, "graph": V1, "model": {"name": "incident-remediation"}}
    )
    assert both.status_code == 422
    neither = await client.post("/api/v1/decisions", json=base)
    assert neither.status_code == 422
    unknown = await client.post("/api/v1/decisions", json={**base, "model": {"name": "nope"}})
    assert unknown.status_code == 404
    wrong = await client.post(
        "/api/v1/decisions",
        json={**base, "model": {"name": "incident-remediation", "digest": "sha256:" + "0" * 64}},
    )
    assert wrong.status_code == 409, "insisting on a digest the version does not have is refused"


async def test_a_revision_says_which_verdicts_no_longer_stand(client: AsyncClient) -> None:
    v1 = await register(client)
    first = await decide(client)
    assert first["outcome"]["certificate"]["adjustment_set"] == ["load"]

    irrelevant = await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={"parent_digest": v1["digest"], "graph": V1 + "; season -> load",
              "why": "season drives load"},
    )
    assert irrelevant.status_code == 201
    rep = (await client.get("/api/v1/models/incident-remediation/impact?to=2")).json()
    assert rep["by_status"] == {"holds": 1}, rep

    confounded = await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={"parent_digest": irrelevant.json()["digest"],
              "graph": V2 + "; season -> load", "why": "backlog confounds both"},
    )
    assert confounded.status_code == 201
    rep = (await client.get("/api/v1/models/incident-remediation/impact")).json()
    assert rep["to"]["version"] == 3
    [row] = rep["decisions"]
    assert row["entry_digest"] == first["record"]["entry_digest"]
    assert row["impact"]["status"] == "invalidated"
    assert "load" in row["impact"]["detail"]


async def test_a_node_the_data_lacked_is_judged_as_the_analysis_saw_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The v2 graph names `backlog`; a decision whose data had no such column analysed it
    as latent, and impact must judge it the same way -- so the row records both the set the
    analysis used and the nodes it could not see."""
    v1 = await register(client)
    v2 = (
        await client.post(
            "/api/v1/models/incident-remediation/versions",
            json={"parent_digest": v1["digest"], "graph": V1 + "; backlog -> minutes",
                  "why": "backlog affects duration"},
        )
    ).json()
    cols = data()
    cols.pop("backlog")
    resp = await client.post(
        "/api/v1/decisions",
        json={
            "action": "apply remediation playbook X",
            "model": {"name": "incident-remediation"},
            "treatment": "playbook",
            "outcome": "minutes",
            "direction": "decrease",
            "data": cols,
            "risk_class": "low",
        },
    )
    assert resp.status_code == 200, resp.text
    stored = (
        await db_session.execute(text("SELECT query, unmeasured FROM decision_model_refs"))
    ).one()
    # JSON text on SQLite, decoded JSONB on PostgreSQL: compare the values.
    assert '"adjust_for":["load"]' in json.dumps(json_value(stored[0]), separators=(",", ":"))
    assert json_value(stored[1]) == ["backlog"]
    await client.post(
        "/api/v1/models/incident-remediation/versions",
        json={"parent_digest": v2["digest"], "graph": V1 + "; backlog -> minutes; "
              "backlog -> playbook", "why": "backlog also drives the playbook"},
    )
    rep = (await client.get("/api/v1/models/incident-remediation/impact")).json()
    [row] = rep["decisions"]
    assert row["impact"]["status"] == "invalidated", "an unmeasured confounder cannot be adjusted"


async def test_versions_are_append_only(db_session: AsyncSession, client: AsyncClient) -> None:
    await register(client)
    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("UPDATE causal_model_versions SET author_id = 'mallory'"))
        await db_session.commit()
    await db_session.rollback()
    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("DELETE FROM causal_model_versions"))
        await db_session.commit()


async def test_the_registry_requires_authentication(db_engine: object) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/models")).status_code == 401
        resp = await anon.post("/api/v1/models", json={"name": "m", "graph": V1, "why": "x" * 6})
        assert resp.status_code == 401

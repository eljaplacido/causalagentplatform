"""The platform finds out whether its decisions were right.

`decision-calibration` on the register said: "Nothing captures what happened
after a verdict [...] no regret or calibration number exists anywhere in this
repository." This is the loop that closes it, end to end through the API.

The half that matters most is the refusals. Calibrating a causal prediction
needs a causal estimate afterwards, and an action rolled out to everybody
leaves no comparison group -- so the decisions that vanish from a naive
calibration report are exactly the ones that were acted on hardest. Here the
kernel refuses the later window on its own, and the refusal is *recorded* as an
undecidable realization rather than dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {
    "uid": "followup-test-001",
    "email": "ops@test.com",
    "name": "Ops User",
    "roles": ["sre"],
}

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def window(
    effect: float = -9.0,
    *,
    treated_share: float | None = None,
    n: int = 2000,
    seed: int = 0,
) -> dict[str, list[float]]:
    """One observation window. `treated_share` forces a rollout level."""
    rng = np.random.default_rng(seed)
    load = rng.normal(size=n)
    if treated_share is None:
        playbook = (0.9 * load + rng.logistic(size=n) > 0).astype(float)
    else:
        playbook = (rng.random(n) < treated_share).astype(float)
    minutes = 60 + effect * playbook + 12 * load + rng.normal(0, 8, size=n)
    frame = pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})
    return {c: frame[c].tolist() for c in frame}


def body(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "apply remediation playbook X",
        "graph": GRAPH,
        "treatment": "playbook",
        "outcome": "minutes",
        "direction": "decrease",
        "data": window(),
        "risk_class": "low",
        "minimum_worthwhile_effect": 5.0,
    }
    payload.update(over)
    return payload


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac:
            yield ac


async def _decide(client: AsyncClient, **over: Any) -> str:
    resp = await client.post("/api/v1/decisions", json=body(**over))
    assert resp.status_code == 200, resp.text
    return str(resp.json()["record"]["entry_digest"])


# ── the plan is written when the decision is made ─────────────────────────────


async def test_deciding_records_what_would_have_to_be_re_measured(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _decide(client)

    row = (
        await db_session.execute(
            text(
                "SELECT kind, payload FROM decision_followups "
                "WHERE entry_digest = :e ORDER BY id"
            ),
            {"e": entry},
        )
    ).mappings().all()

    assert [r["kind"] for r in row] == ["plan"]
    import json

    plan = json.loads(row[0]["payload"])
    assert plan["graph"] == GRAPH
    assert plan["query"]["treatment"] == "playbook"
    assert plan["predicted_benefit"] is not None, "a verdict with no prediction cannot be scored"


# ── a measurement that can be made ────────────────────────────────────────────


async def test_a_prediction_that_held_is_scored_against_the_number_it_used(
    client: AsyncClient,
) -> None:
    entry = await _decide(client)

    resp = await client.post(
        f"/api/v1/decisions/{entry}/followup",
        json={"data": window(effect=-9.0, seed=1)},
    )
    assert resp.status_code == 200, resp.text
    r = resp.json()["realization"]

    assert r["decidable"] is True
    assert r["undecidable_reason"] is None
    assert r["realized_benefit"] > 5, "fewer minutes is the benefit, in the requested direction"
    assert r["intervals_overlap"] is True
    assert r["cleared_worthwhile"] is True
    assert r["realized_harm"] is False


async def test_an_action_that_turned_out_harmful_is_recorded_as_such(
    client: AsyncClient,
) -> None:
    entry = await _decide(client)

    resp = await client.post(
        f"/api/v1/decisions/{entry}/followup",
        json={"data": window(effect=+7.0, seed=1)},
    )
    assert resp.status_code == 200, resp.text
    r = resp.json()["realization"]

    assert r["decidable"] is True
    assert r["realized_benefit"] < 0
    assert r["realized_harm"] is True
    assert r["point_in_predicted_ci"] is False


# ── the measurement that cannot be made ───────────────────────────────────────


async def test_a_full_rollout_is_undecidable_and_is_kept(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The case a naive calibration report would silently drop."""
    entry = await _decide(client)

    resp = await client.post(
        f"/api/v1/decisions/{entry}/followup",
        json={"data": window(effect=-9.0, treated_share=1.0, seed=1)},
    )
    assert resp.status_code == 200, resp.text
    r = resp.json()["realization"]

    assert r["decidable"] is False
    assert r["undecidable_reason"], "the kernel's own reason must be carried through"
    assert r["realized_benefit"] is None
    assert r["point_in_predicted_ci"] is None, "undecidable must not score as a miss"
    assert "cannot answer" in resp.json()["explanation"]

    kept = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM decision_followups "
                "WHERE entry_digest = :e AND kind = 'realization'"
            ),
            {"e": entry},
        )
    ).scalar()
    assert kept == 1, "an undecidable measurement is still a measurement and is kept"


async def test_a_follow_up_on_an_unknown_decision_is_a_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/decisions/sha256:deadbeef/followup", json={"data": window()}
    )
    assert resp.status_code == 404
    assert "no follow-up plan" in resp.json()["detail"]


# ── the report ────────────────────────────────────────────────────────────────


async def test_calibration_over_nothing_reports_no_rate(client: AsyncClient) -> None:
    """The rule the gates hold themselves to, on the number the platform is judged by."""
    resp = await client.get("/api/v1/decisions/calibration")
    assert resp.status_code == 200, resp.text
    rep = resp.json()

    assert rep["n_decisions"] == 0
    assert rep["point_in_interval_rate"] is None, "no data must not read as perfect calibration"
    assert rep["acted_and_worthwhile_rate"] is None


async def test_calibration_counts_the_undecidable_beside_the_rates(client: AsyncClient) -> None:
    held = await _decide(client)
    await client.post(
        f"/api/v1/decisions/{held}/followup", json={"data": window(effect=-9.0, seed=1)}
    )

    rolled_out = await _decide(client, action="apply remediation playbook Y")
    await client.post(
        f"/api/v1/decisions/{rolled_out}/followup",
        json={"data": window(effect=-9.0, treated_share=1.0, seed=1)},
    )

    rep = (await client.get("/api/v1/decisions/calibration")).json()

    assert rep["n_decisions"] == 2
    assert rep["n_decidable"] == 1
    assert rep["n_undecidable"] == 1
    assert sum(rep["undecidable_by_reason"].values()) == 1
    assert rep["point_in_interval_rate"] is not None, "one decidable measurement is a rate of one"
    assert "not missing at random" in rep["note"]


async def test_the_followup_table_refuses_to_be_edited(db_session: AsyncSession) -> None:
    """Append-only, like the chain it scores. A disappointing row must not be removable."""
    await db_session.execute(
        text(
            "INSERT INTO decision_followups "
            "(entry_digest, decision_digest, kind, decidable, created_at, payload) "
            "VALUES ('e', 'd', 'realization', 1, 'now', '{}')"
        )
    )
    await db_session.commit()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("DELETE FROM decision_followups WHERE entry_digest = 'e'"))
        await db_session.commit()


async def test_a_decision_is_measured_once_and_the_first_measurement_stands(
    client: AsyncClient,
) -> None:
    """A second, friendlier window must not join the average and outvote the first."""
    digest = await _decide(client)
    first = await client.post(
        f"/api/v1/decisions/{digest}/followup", json={"data": window(effect=4.0, seed=1)}
    )
    assert first.status_code == 200, first.text
    assert first.json()["realization"]["realized_harm"] is True

    second = await client.post(
        f"/api/v1/decisions/{digest}/followup", json={"data": window(effect=-9.0, seed=2)}
    )
    assert second.status_code == 409, second.text
    assert "first measurement stands" in second.json()["detail"]

    rep = (await client.get("/api/v1/decisions/calibration")).json()
    assert rep["n_decisions"] == 1
    assert rep["acted_and_harmful"] == 1, "the harmful measurement is the one that counts"


async def test_the_database_refuses_a_second_measurement_too(db_session: AsyncSession) -> None:
    """The endpoint checks first; the unique index holds when two requests race."""
    insert = text(
        "INSERT INTO decision_followups "
        "(entry_digest, decision_digest, kind, decidable, created_at, payload) "
        "VALUES ('race', 'd', 'realization', 1, 'now', '{}')"
    )
    await db_session.execute(insert)
    await db_session.commit()
    with pytest.raises(Exception, match="(?i)unique"):
        await db_session.execute(insert)
        await db_session.commit()


async def test_a_calibration_limit_means_the_most_recent_decisions(client: AsyncClient) -> None:
    older = await _decide(client, action="playbook, older")
    await client.post(
        f"/api/v1/decisions/{older}/followup", json={"data": window(effect=-9.0, seed=1)}
    )
    newer = await _decide(client, action="playbook, newer")
    await client.post(
        f"/api/v1/decisions/{newer}/followup",
        json={"data": window(effect=-9.0, treated_share=1.0, seed=1)},
    )

    rep = (await client.get("/api/v1/decisions/calibration", params={"limit": 1})).json()
    assert rep["n_decisions"] == 1
    assert rep["n_undecidable"] == 1, "limit=1 must return the newer (undecidable) one"


async def test_followup_and_calibration_require_authentication(db_engine: object) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/decisions/calibration")).status_code == 401
        resp = await anon.post("/api/v1/decisions/x/followup", json={"data": {"a": [1]}})
        assert resp.status_code == 401

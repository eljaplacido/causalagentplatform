"""
The platform makes decisions and keeps them.

This is the test the register's `decision-ledger-not-persisted` entry named: a
decision made through the platform is persisted, returned by an endpoint, and
verifies as a chain from the stored rows rather than from anything held in memory.

`causal_decision.ledger` already proves the chain's own properties. What is left to
show here is the part only the platform can get wrong: that the record written is
the decision that was made, that reading it back and recomputing still holds, and
that a row edited in the database is caught rather than served.
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
from src.semantic_layer.decision_store import DecisionStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {
    "uid": "decision-test-001",
    "email": "ops@test.com",
    "name": "Ops User",
    "roles": ["sre"],
}

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def ops_data(n: int = 2000, effect: float = -9.0, noise: float = 8.0) -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    load = rng.normal(size=n)
    playbook = (0.9 * load + rng.logistic(size=n) > 0).astype(float)
    minutes = 60 + effect * playbook + 12 * load + rng.normal(0, noise, size=n)
    frame = pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})
    return {c: frame[c].tolist() for c in frame}


def body(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "apply remediation playbook X",
        "graph": GRAPH,
        "treatment": "playbook",
        "outcome": "minutes",
        "direction": "decrease",
        "data": ops_data(),
        "risk_class": "low",
        "minimum_worthwhile_effect": 5.0,
        "randomization_unit": "incident",
        "units_per_day": 40,
        "estimand": "att",
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


async def test_a_decision_is_made_explained_and_kept(
    client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    resp = await client.post("/api/v1/decisions", json=body())
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["verdict"] == "act"
    assert payload["explanation"].startswith("ACT:")
    assert payload["outcome"]["kind"] == "result"

    record = payload["record"]
    assert record["sequence"] == 0
    assert record["evidence"]["result_digest"] == payload["outcome"]["result_digest"]
    assert record["executed"] is False, "deciding is not doing"

    stored = await db_session.execute(
        text("SELECT entry_digest, verdict, subject FROM decision_records WHERE sequence = 0")
    )
    row = stored.mappings().one()
    assert row["entry_digest"] == record["entry_digest"]
    assert row["verdict"] == "act"
    assert row["subject"] == "apply remediation playbook X"


async def test_the_chain_grows_and_verifies_from_what_was_stored(
    client: AsyncClient, db_engine: object
) -> None:
    """Three decisions, then verification that reads the rows and recomputes."""
    for mwe in (5.0, 40.0, 5.0):
        resp = await client.post("/api/v1/decisions", json=body(minimum_worthwhile_effect=mwe))
        assert resp.status_code == 200, resp.text

    verify = await client.get("/api/v1/decisions/verify")
    assert verify.status_code == 200
    report = verify.json()
    assert report["ok"] is True, report["reason"]
    assert report["length"] == 3
    assert report["head_digest"]

    listed = await client.get("/api/v1/decisions")
    assert listed.json()["total"] == 3
    verdicts = [r["decision"]["verdict"] for r in listed.json()["records"]]
    assert verdicts == ["act", "refuse", "act"], "the bar changed, so the verdict did"


async def test_the_chain_survives_a_new_store_and_a_new_connection(
    client: AsyncClient, db_engine: object
) -> None:
    """Nothing is held in memory: a fresh store reads the same chain and it holds.

    The ledger module can only prove things about a list it was handed. This is the
    part that says the list came out of the database.
    """
    for _ in range(2):
        assert (await client.post("/api/v1/decisions", json=body())).status_code == 200

    fresh = DecisionStore()
    records = await fresh.all_records()
    assert [r.sequence for r in records] == [0, 1]
    assert records[1].prev_digest == records[0].entry_digest

    report = await fresh.verify()
    assert report.ok, report.reason
    assert report.head_digest == records[-1].entry_digest


async def _lift_append_only_guard(session: AsyncSession) -> None:
    """Simulate a writer with direct database access.

    `decision_records` is append-only, and since 2026-09-24 that is enforced in the
    test schema as well as in production. The triggers make tampering awkward; they
    have never made it impossible, and the hash chain exists precisely because they
    cannot -- anyone who can write these rows can also drop a trigger. So the tests
    that prove tampering is *detected* have to become that attacker first, and they
    do it explicitly here rather than by quietly relying on an unguarded table.

    No restore: each test builds a fresh in-memory database.
    """
    for trigger in ("tg_decision_records_no_update", "tg_decision_records_no_delete"):
        await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
    await session.commit()


async def test_editing_a_stored_record_is_caught_by_verification(
    client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """The case the whole chain exists for: a verdict rewritten in the database."""
    assert (await client.post("/api/v1/decisions", json=body())).status_code == 200
    assert (await client.post("/api/v1/decisions", json=body())).status_code == 200

    row = await db_session.execute(
        text("SELECT record FROM decision_records WHERE sequence = 0")
    )
    raw = row.scalar()
    assert isinstance(raw, str)
    # Rewrite the thing an operator would actually want to change after the fact:
    # a decision that was never carried out, recorded as though it had been.
    # (Edit the parsed JSON rather than the text: a string replace that silently
    # matches nothing produces a tamper test that never tampers.)
    import json

    parsed = json.loads(raw)
    assert parsed["executed"] is False
    parsed["executed"] = True
    tampered = json.dumps(parsed)
    assert tampered != raw, "the edit must actually change the stored bytes"

    await _lift_append_only_guard(db_session)
    await db_session.execute(
        text("UPDATE decision_records SET record = :r WHERE sequence = 0"), {"r": tampered}
    )
    await db_session.commit()

    verify = await client.get("/api/v1/decisions/verify")
    report = verify.json()
    assert report["ok"] is False
    assert report["broken_at"] == 0
    assert "altered" in report["reason"]


async def test_the_index_columns_are_not_trusted_over_the_record(
    client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Editing the cheap column is easier than editing the JSON, so try that too."""
    assert (await client.post("/api/v1/decisions", json=body())).status_code == 200

    await _lift_append_only_guard(db_session)
    await db_session.execute(
        text("UPDATE decision_records SET verdict = 'refuse' WHERE sequence = 0")
    )
    await db_session.commit()

    listed = await client.get("/api/v1/decisions")
    served = listed.json()["records"][0]["decision"]["verdict"]
    assert served == "act", "the record is the truth; the column is an index over it"

    verify = await client.get("/api/v1/decisions/verify")
    assert verify.json()["ok"] is True, "the record itself was not touched"


async def test_a_refusal_is_recorded_too(client: AsyncClient, db_engine: object) -> None:
    """An unanswerable question is a decision, and belongs in the trail."""
    resp = await client.post(
        "/api/v1/decisions",
        json=body(graph="playbook -> minutes; playbook <-> minutes"),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["outcome"]["kind"] == "refusal"
    assert payload["verdict"] in ("experiment", "refuse")
    assert payload["record"]["evidence"]["outcome_kind"] == "refusal"


async def test_bad_input_is_refused_before_anything_is_recorded(
    client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    resp = await client.post("/api/v1/decisions", json=body(minimum_worthwhile_effect=-1.0))
    assert resp.status_code == 422

    count = await db_session.execute(text("SELECT COUNT(*) FROM decision_records"))
    assert count.scalar() == 0


async def test_the_endpoint_requires_authentication(db_engine: object) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/decisions", json=body())
    assert resp.status_code == 401


async def test_the_chain_table_refuses_to_be_edited_in_place(
    client: AsyncClient, db_session: AsyncSession, db_engine: object
) -> None:
    """Before the tamper tests above drop it, prove the guard is really there.

    Those tests lift the append-only trigger to play the attacker; if it had
    never been created they would pass just the same, and would be asserting
    nothing about the schema. 003 declares it for PostgreSQL and the SQLite
    schema declares it too since 2026-09-24 -- this is what pins the second.
    """
    assert (await client.post("/api/v1/decisions", json=body())).status_code == 200

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text("UPDATE decision_records SET verdict = 'refuse' WHERE sequence = 0")
        )
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("DELETE FROM decision_records WHERE sequence = 0"))
        await db_session.commit()

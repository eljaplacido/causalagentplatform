"""scripts/seed_data.py --clear removes what the seeder wrote, and nothing else.

What --clear must do, on the schema each backend builds:

* delete the seeder's rows from the tables that allow a DELETE at all --
  causal_entities, graph_fragments, causal_edges, incidents -- and leave every
  other row in them alone;
* never touch an append-only table. audit_log (and decision_records,
  decision_followups, causal_model_versions, decision_model_refs) refuse a DELETE
  by trigger, and on PostgreSQL by grant too; the seeder's audit rows stay, and
  --clear says how many it kept;
* on PostgreSQL, run only as a role that may delete (the tables' owner):
  causal_app has no DELETE grant anywhere, so as causal_app it must refuse up
  front, naming the reason, without deleting anything.

The earlier --clear issued one DELETE per table with a WHERE over agent_id,
action_type and created_by -- columns only audit_log half-has -- starting with
audit_log itself, so it failed on the first statement on either backend.

These tests are synchronous: the seeder's public functions run their own event
loop (asyncio.run), as the CLI does, so each test points CloudSqlFactory at a
NullPool engine (no connection outlives the loop that opened it).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from scripts import seed_data
from src.db.factory import CloudSqlFactory
from src.db.scope import install_scope_listener, service_scope
from tests.conftest import PostgresTestDatabase, postgres_only

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from sqlalchemy.engine import URL

_SEMANTIC = ("causal_entities", "graph_fragments", "causal_edges")


def _use_engine(engine: AsyncEngine) -> None:
    CloudSqlFactory.reset()
    CloudSqlFactory._engine = engine
    CloudSqlFactory._session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(sql))).scalar() or 0)


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    out = {t: await _count(engine, f"SELECT COUNT(*) FROM {t}") for t in _SEMANTIC}
    out["incidents"] = await _count(engine, "SELECT COUNT(*) FROM incidents")
    out["audit_log"] = await _count(engine, "SELECT COUNT(*) FROM audit_log")
    return out


# ── SQLite: the real seeder, then --clear ─────────────────────────────────────


@pytest.fixture
def sqlite_file_engine(tmp_path: Path) -> Generator[AsyncEngine, None, None]:
    saved = {k: os.environ.get(k) for k in ("ENV", "SQLITE_PATH")}
    os.environ["ENV"] = "local"
    os.environ["SQLITE_PATH"] = str(tmp_path / "seed.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}", poolclass=NullPool
    )
    _use_engine(engine)
    asyncio.run(CloudSqlFactory.init_db())
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())
        CloudSqlFactory.reset()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def _insert_foreign_rows_sqlite(engine: AsyncEngine) -> None:
    """One row per table that is not the seeder's: --clear must keep them."""
    verdict = str(uuid4())
    other = json.dumps({"id": "user-042", "email": "u@example.com", "name": "U"})
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, 'governance-agent-api', 'GUARDIAN_CHECK', 'h', 'APPROVED', '[]', :ts)"
            ),
            {"id": verdict, "ts": seed_data._now_iso()},
        )
        await conn.execute(
            text(
                "INSERT INTO causal_entities "
                "(id, name, entity_type, domain, created_by, created_at) "
                "VALUES (:id, 'user-entity', 'SERVICE', 'devops', :by, :ts)"
            ),
            {"id": str(uuid4()), "by": other, "ts": seed_data._now_iso()},
        )
        await conn.execute(
            text(
                "INSERT INTO causal_edges (id, source_id, source_type, target_id, target_type, "
                "edge_type, confidence, guardian_verdict_ref, domain, created_at) "
                "VALUES (:id, :s, 'CausalEntity', :t, 'CausalEntity', 'CAUSES', 0.5, :v, "
                "'devops', :ts)"
            ),
            {"id": str(uuid4()), "s": str(uuid4()), "t": str(uuid4()), "v": verdict,
             "ts": seed_data._now_iso()},
        )
        await conn.execute(
            text(
                "INSERT INTO graph_fragments (id, name, domain, created_by, created_at) "
                "VALUES (:id, 'user-fragment', 'devops', :by, :ts)"
            ),
            {"id": str(uuid4()), "by": other, "ts": seed_data._now_iso()},
        )
        await conn.execute(
            text(
                "INSERT INTO incidents (id, title, severity, submitted_by, created_at, updated_at) "
                "VALUES ('INC-USER-1', 'user incident', 'HIGH', 'user-042', :ts, :ts)"
            ),
            {"ts": seed_data._now_iso()},
        )


def test_clear_removes_every_seeded_row_and_keeps_the_rest_sqlite(
    sqlite_file_engine: AsyncEngine,
) -> None:
    engine = sqlite_file_engine
    asyncio.run(_insert_foreign_rows_sqlite(engine))
    baseline = asyncio.run(_counts(engine))

    with service_scope("test: seed_data"):
        seed_data.seed_devops(None)
        seed_data.seed_marketing(None)
        seed_data.seed_evaluation_data(None)
        seed_data.seed_review_queue(None)
        seed_data.seed_incidents(None)
    seeded = asyncio.run(_counts(engine))
    for table in (*_SEMANTIC, "incidents"):
        assert seeded[table] > baseline[table], f"the seeder wrote nothing to {table}"

    with service_scope("test: seed_data --clear"):
        result = seed_data.clear_seeded_data(None)
    after = asyncio.run(_counts(engine))

    # Every seeded row gone, every foreign row kept.
    for table in (*_SEMANTIC, "incidents"):
        assert after[table] == baseline[table], table
        assert result["cleared"][table] == seeded[table] - baseline[table], table
    # The append-only audit trail is untouched, and the kept rows are reported.
    assert after["audit_log"] == seeded["audit_log"]
    assert result["retained_append_only"]["audit_log"] == asyncio.run(
        _count(engine, "SELECT COUNT(*) FROM audit_log WHERE agent_id = 'seeder'")
    )
    assert "audit_log" not in result["cleared"]

    # Idempotent: a second clear finds nothing to remove.
    with service_scope("test: seed_data --clear again"):
        again = seed_data.clear_seeded_data(None)
    assert all(n == 0 for n in again["cleared"].values())


# ── PostgreSQL: the migrated schema, as causal_app and as the owner ───────────
#
# The seeder's own INSERTs bind JSON text where the PostgreSQL schema has arrays
# (policy_rules_cited, tags, edge_ids), so it does not run there today; these
# tests write seeder-shaped rows with the schema's own types and check --clear's
# SQL and its privilege refusal against the real schema, grants and triggers.


async def _insert_rows_pg(owner: AsyncEngine) -> dict[str, Any]:
    seeder = json.dumps({"id": "seeder-001", "email": "seeder@causalplatform.local",
                         "name": "Data Seeder"})
    other = json.dumps({"id": "user-042", "email": "u@example.com", "name": "U"})
    seed_verdict, user_verdict = str(uuid4()), str(uuid4())
    async with owner.begin() as conn:
        for vid, agent in ((seed_verdict, "seeder"), (user_verdict, "governance-agent-api")):
            await conn.execute(
                text(
                    "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                    "guardian_verdict) VALUES (CAST(:id AS uuid), :agent, 'X', 'h', 'APPROVED')"
                ),
                {"id": vid, "agent": agent},
            )
        for name, by in (("seeded", seeder), ("user", other)):
            await conn.execute(
                text(
                    "INSERT INTO causal_entities (name, entity_type, domain, created_by) "
                    "VALUES (:name, 'SERVICE', 'devops', CAST(:by AS jsonb))"
                ),
                {"name": name, "by": by},
            )
            await conn.execute(
                text(
                    "INSERT INTO graph_fragments (name, domain, created_by) "
                    "VALUES (:name, 'devops', CAST(:by AS jsonb))"
                ),
                {"name": name, "by": by},
            )
        for vid in (seed_verdict, user_verdict):
            await conn.execute(
                text(
                    "INSERT INTO causal_edges (source_id, source_type, target_id, target_type, "
                    "edge_type, confidence, approval_status, guardian_verdict_ref, domain) "
                    "VALUES (gen_random_uuid(), 'CausalEntity', gen_random_uuid(), "
                    "'CausalEntity', 'CAUSES', 0.5, 'APPROVED', CAST(:v AS uuid), 'devops')"
                ),
                {"v": vid},
            )
        for iid, by in (("INC-SEED-001", "seeder-001"), ("INC-USER-1", "user-042")):
            await conn.execute(
                text(
                    "INSERT INTO incidents (id, title, severity, submitted_by) "
                    "VALUES (:id, 't', 'HIGH', :by)"
                ),
                {"id": iid, "by": by},
            )
    return {"seed_verdict": seed_verdict}


async def _truncate(owner: AsyncEngine) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE causal_edges, graph_fragments, causal_entities, incidents, "
                "verdict_consumption, audit_log RESTART IDENTITY CASCADE"
            )
        )


def _engine(url: URL, *, app: bool) -> AsyncEngine:
    engine = create_async_engine(url, poolclass=NullPool)
    if app:
        install_scope_listener(engine)
    return engine


@postgres_only
def test_clear_as_causal_app_refuses_before_deleting_anything_pg(
    postgres_test_db: PostgresTestDatabase,
) -> None:
    owner = _engine(postgres_test_db.owner_url, app=False)
    app = _engine(postgres_test_db.app_url, app=True)
    try:
        asyncio.run(_truncate(owner))
        asyncio.run(_insert_rows_pg(owner))
        before = asyncio.run(_counts(owner))
        _use_engine(app)
        with (
            service_scope("test: seed_data --clear as causal_app"),
            pytest.raises(RuntimeError, match="must run as the tables' owner"),
        ):
            seed_data.clear_seeded_data(None)
        assert asyncio.run(_counts(owner)) == before
    finally:
        CloudSqlFactory.reset()
        asyncio.run(_truncate(owner))
        asyncio.run(app.dispose())
        asyncio.run(owner.dispose())


@postgres_only
def test_clear_as_owner_removes_seeded_rows_and_keeps_audit_pg(
    postgres_test_db: PostgresTestDatabase,
) -> None:
    owner = _engine(postgres_test_db.owner_url, app=False)
    try:
        asyncio.run(_truncate(owner))
        asyncio.run(_insert_rows_pg(owner))
        before = asyncio.run(_counts(owner))
        _use_engine(_engine(postgres_test_db.owner_url, app=True))
        with service_scope("test: seed_data --clear as owner"):
            result = seed_data.clear_seeded_data(None)
        after = asyncio.run(_counts(owner))
        for table in (*_SEMANTIC, "incidents"):
            assert result["cleared"][table] == 1, table
            assert after[table] == before[table] - 1, table
        assert after["audit_log"] == before["audit_log"] == 2
        assert result["retained_append_only"]["audit_log"] == 1
        # What is left is the foreign row in each table.
        left = asyncio.run(
            _count(
                owner,
                "SELECT COUNT(*) FROM causal_entities WHERE created_by->>'id' = 'user-042'",
            )
        )
        assert left == 1
    finally:
        if CloudSqlFactory._engine is not None:
            asyncio.run(CloudSqlFactory._engine.dispose())
        CloudSqlFactory.reset()
        asyncio.run(_truncate(owner))
        asyncio.run(owner.dispose())

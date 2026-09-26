"""scripts/seed_data.py seeds both backends: SQLite, and PostgreSQL as causal_app.

The seeder wrote through raw ``text()`` INSERTs whose parameters were whatever
Python object came to hand: JSON text for ``TEXT[]`` / ``UUID[]`` columns
(audit_log.policy_rules_cited, causal_entities.tags, graph_fragments.tags and
edge_ids, causal_edges.evidence_refs / assumption_refs), ISO-8601 strings for
``TIMESTAMPTZ`` columns, and ``INSERT OR REPLACE`` (SQLite-only syntax) for
incidents. SQLite accepts all of it; asyncpg refuses the first bind. So the
seeder only ever worked on SQLite.

These tests run every seeding function the CLI's ``--all`` runs, as the CLI
does -- inside a service scope, each function running its own event loop -- and
check what landed, typed as the schema types it:

* on SQLite, from the schema the dev engine creates;
* on PostgreSQL, on the migrated schema, connected as causal_app (the role the
  backend runs as, which row-level security binds): the service scope is what
  lets the seeder write into every domain, and causal_app's INSERT/UPDATE grants
  are all it needs. Seeding does not need the tables' owner.

Seeding incidents twice must work: they are upserted on their fixed ids. (A
second full --all on PostgreSQL needs --clear first: graph_fragments is unique
on (name, version, domain) there.)

The tests are synchronous because the seeder calls ``asyncio.run`` itself; each
points CloudSqlFactory at a NullPool engine so no connection outlives its loop.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

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

_TABLES = ("audit_log", "causal_entities", "causal_edges", "graph_fragments", "incidents")


def _use_engine(engine: AsyncEngine) -> None:
    CloudSqlFactory.reset()
    CloudSqlFactory._engine = engine
    CloudSqlFactory._session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


def _seed_all() -> dict[str, Any]:
    with service_scope("test: scripts.seed_data --all"):
        return {
            "devops": seed_data.seed_devops(None),
            "marketing": seed_data.seed_marketing(None),
            "evaluation": seed_data.seed_evaluation_data(None),
            "reviews": seed_data.seed_review_queue(None),
            "incidents": seed_data.seed_incidents(None),
        }


async def _rows(engine: AsyncEngine, sql: str) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(text(sql))).all())


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    return {t: (await _rows(engine, f"SELECT COUNT(*) FROM {t}"))[0][0] for t in _TABLES}


def _as_list(value: Any) -> list[str]:
    """A TEXT[]/UUID[] (a list on PostgreSQL) or its JSON text (SQLite), as a list."""
    items = json.loads(value) if isinstance(value, str) else value
    return [str(v) for v in items]


def _check_seeded(engine: AsyncEngine, result: dict[str, Any]) -> None:
    counts = asyncio.run(_counts(engine))
    for table in _TABLES:
        assert counts[table] > 0, f"the seeder wrote nothing to {table}"
    assert counts["incidents"] == len(seed_data.SEED_INCIDENTS)
    assert counts["causal_entities"] == len(result["devops"]["entities"]) + len(
        result["reviews"]["entities"]
    )

    # Arrays hold the values, not their JSON text.
    tags = asyncio.run(
        _rows(engine, "SELECT tags FROM causal_entities WHERE name = 'api-gateway'")
    )
    assert _as_list(tags[0][0]) == ["devops", "service", "seeded"]
    rules = asyncio.run(
        _rows(
            engine,
            "SELECT policy_rules_cited FROM audit_log WHERE action_type = 'SEED_FRAGMENT'",
        )
    )
    assert sorted(_as_list(r[0]) for r in rules) == [["PR-001"], ["PR-001", "PR-007"]]

    # Every fragment's edge_ids resolve to real edges.
    edge_ids = {str(r[0]) for r in asyncio.run(_rows(engine, "SELECT id FROM causal_edges"))}
    fragments = asyncio.run(_rows(engine, "SELECT name, edge_ids FROM graph_fragments"))
    assert len(fragments) == 2 + len(seed_data.REVIEW_FRAGMENTS)
    for name, ids in fragments:
        listed = _as_list(ids)
        assert listed, name
        assert set(listed) <= edge_ids, name


# ── SQLite ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_file_engine(tmp_path: Path) -> Generator[AsyncEngine, None, None]:
    saved = {k: os.environ.get(k) for k in ("ENV", "SQLITE_PATH")}
    os.environ["ENV"] = "local"
    os.environ["SQLITE_PATH"] = str(tmp_path / "seed.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}", poolclass=NullPool)
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


def test_seed_all_on_sqlite_and_again(sqlite_file_engine: AsyncEngine) -> None:
    result = _seed_all()
    _check_seeded(sqlite_file_engine, result)
    # A second run adds new semantic rows and upserts the fixed-id incidents.
    _seed_all()
    counts = asyncio.run(_counts(sqlite_file_engine))
    assert counts["incidents"] == len(seed_data.SEED_INCIDENTS)


# ── PostgreSQL, as causal_app ─────────────────────────────────────────────────


async def _truncate(owner: AsyncEngine) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE causal_edges, graph_fragments, causal_entities, incidents, "
                "verdict_consumption, audit_log RESTART IDENTITY CASCADE"
            )
        )


@postgres_only
def test_seed_all_on_postgres_as_causal_app(
    postgres_test_db: PostgresTestDatabase,
) -> None:
    owner = create_async_engine(postgres_test_db.owner_url, poolclass=NullPool)
    app = create_async_engine(postgres_test_db.app_url, poolclass=NullPool)
    install_scope_listener(app)
    try:
        asyncio.run(_truncate(owner))
        role = asyncio.run(_rows(app, "SELECT current_user"))[0][0]
        assert role != postgres_test_db.owner_url.username  # really the app's login
        _use_engine(app)

        result = _seed_all()
        # Read back as the owner, whom row-level security does not bind.
        _check_seeded(owner, result)
        timestamps = asyncio.run(
            _rows(owner, "SELECT pg_typeof(created_at)::text FROM incidents LIMIT 1")
        )
        assert timestamps[0][0] == "timestamp with time zone"

        # The incidents upsert (ON CONFLICT on the fixed ids) on PostgreSQL. The
        # whole seed is not re-run here: graph_fragments is unique on (name,
        # version, domain) on PostgreSQL, so a second --all needs --clear first.
        with service_scope("test: scripts.seed_data --incidents again"):
            seed_data.seed_incidents(None)
        counts = asyncio.run(_counts(owner))
        assert counts["incidents"] == len(seed_data.SEED_INCIDENTS)
    finally:
        CloudSqlFactory.reset()
        asyncio.run(_truncate(owner))
        asyncio.run(app.dispose())
        asyncio.run(owner.dispose())

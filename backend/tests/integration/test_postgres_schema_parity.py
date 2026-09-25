"""
The PostgreSQL schema the migrations build is the schema the app is written for.

Written for septa-52 (register entry `postgres-schema-missing-session-incidents`).
The backend has two schemas: SQLITE_DDL in src/db/factory.py, which local
development and the default test run use, and src/db/migrations/*.sql, which a
PostgreSQL deployment runs. They had drifted: session_state and incidents existed
only in the SQLite one, so on PostgreSQL every session snapshot and every
incident write failed with a missing relation -- and nothing noticed, because
the suite ran only on SQLite.

Preconditions: TEST_DATABASE_URL names a PostgreSQL server, so the db_engine
fixture is a database built by applying src/db/migrations/*.sql in order
(tests/conftest.py). Without it the two schema tests are skipped -- the gate
step that sets it (backend-pytest-postgres in .gnomon/ci.sh) fails on any skip --
and the round trips run against SQLite, where they hold as well.

Postconditions:
  - the (table, column) pairs of the migrated PostgreSQL schema are exactly those
    of SQLITE_DDL: no table or column exists in one and not the other;
  - a session snapshot saved through SessionStateStore loads back equal, and a
    second save updates it in place;
  - an incident saved through save_incident reads back through the incidents API
    with its lists and times intact, and resubmitting it updates the row while
    keeping the first submission's created_at.

Undefined, and not asserted: column types (SQLite's are affinities, not types),
constraints and indexes, and the exact text of the SQL either store receives.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.api.incidents import get_incident, list_incidents, save_incident
from src.db.factory import SQLITE_DDL, ddl_statements
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import EpistemicState
from tests.conftest import migration_files, postgres_only

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio

_USER = {"uid": "parity-test", "roles": ["analyst"]}


async def _sqlite_columns() -> set[tuple[str, str]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            for statement in ddl_statements(SQLITE_DDL):
                await conn.execute(text(statement))
            tables = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                )
            ).scalars().all()
            pairs: set[tuple[str, str]] = set()
            for table in tables:
                # PRAGMA takes no bound parameter; the name comes from sqlite_master.
                rows = await conn.execute(text(f'PRAGMA table_info("{table}")'))
                pairs.update((table, row[1]) for row in rows)
            return pairs
    finally:
        await engine.dispose()


async def _postgres_columns(engine: AsyncEngine) -> set[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.table_name, c.column_name FROM information_schema.columns c "
                "JOIN information_schema.tables t "
                "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"
            )
        )
        return {(row[0], row[1]) for row in rows}


@postgres_only
async def test_the_fixture_applied_every_migration(db_engine: AsyncEngine) -> None:
    """Premise: the database under test is the migrations, 008 and 009 included."""
    names = [p.name for p in migration_files()]
    assert names == sorted(names)
    assert "008_session_state.sql" in names and "009_incidents.sql" in names
    async with db_engine.connect() as conn:
        dialect = conn.dialect.name
    assert dialect == "postgresql"


@postgres_only
async def test_migrations_and_sqlite_ddl_create_the_same_tables_and_columns(
    db_engine: AsyncEngine,
) -> None:
    sqlite = await _sqlite_columns()
    postgres = await _postgres_columns(db_engine)
    assert {t for t, _ in sqlite} >= {"session_state", "incidents", "audit_log"}, (
        "premise: the SQLite schema was read"
    )
    only_sqlite = sorted(sqlite - postgres)
    only_postgres = sorted(postgres - sqlite)
    assert not only_sqlite and not only_postgres, (
        f"schemas diverge -- only in SQLITE_DDL: {only_sqlite}; "
        f"only in the migrations: {only_postgres}"
    )


async def test_a_session_snapshot_round_trips(db_engine: AsyncEngine) -> None:
    store = SessionStateStore()
    state = EpistemicState(
        session_id="parity-session", agent_id="incident-triage-agent",
        draft_explanation="first draft",
    )
    await store.save(state, user_id="u-1")
    loaded = await store.load("parity-session")
    assert loaded is not None
    assert loaded.model_dump(mode="json") == state.model_dump(mode="json")

    revised = state.model_copy(update={"draft_explanation": "second draft"})
    await store.save(revised, user_id="u-1")
    again = await store.load("parity-session")
    assert again is not None and again.draft_explanation == "second draft"
    async with db_engine.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM session_state"))).scalar()
    assert count == 1, "a second save updates the session's row, it does not add one"

    assert await store.load("no-such-session") is None


async def test_an_incident_round_trips_and_resubmission_keeps_created_at(
    db_engine: AsyncEngine,
) -> None:
    fields = {
        "incident_id": "INC-PARITY-1",
        "title": "payments latency",
        "severity": "P1",
        "affected_services": ["payments", "ledger"],
        "session_id": "parity-session",
        "cynefin_domain": "COMPLICATED",
        "domain_confidence": 0.8,
        "overall_confidence": 0.65,
        "guardian_verdict": "REQUIRES_ESCALATION",
        "draft_explanation": "a deploy raised p99 latency",
        "causal_hypotheses": [{"cause": "deploy", "confidence": 0.7}],
        "proposed_interventions": [{"id": "iv-1", "title": "roll back"}],
        "submitted_by": "u-1",
        "requires_human_review": True,
    }
    await save_incident(**fields)  # type: ignore[arg-type]
    first = await get_incident("INC-PARITY-1", _USER)
    assert first.affected_services == ["payments", "ledger"]
    assert first.causal_hypotheses == [{"cause": "deploy", "confidence": 0.7}]
    assert first.proposed_interventions == [{"id": "iv-1", "title": "roll back"}]
    assert first.status == "ACTIVE"
    assert first.domain_confidence == pytest.approx(0.8)
    created = datetime.fromisoformat(first.created_at)
    assert created.tzinfo is not None, "a stored time comes back as an aware ISO-8601 time"

    await save_incident(**{**fields, "title": "payments latency (updated)",
                           "requires_human_review": False})  # type: ignore[arg-type]
    second = await get_incident("INC-PARITY-1", _USER)
    assert second.title == "payments latency (updated)"
    assert second.status == "INVESTIGATING"
    assert second.created_at == first.created_at, "the first submission's time stands"
    assert datetime.fromisoformat(second.updated_at) >= created

    listed = await list_incidents(limit=10, status_filter=None, user=_USER)
    assert [r.id for r in listed] == ["INC-PARITY-1"]

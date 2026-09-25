"""
Shared pytest fixtures for the Unified Enterprise Causal Platform test suite.

Fixture hierarchy:
  - db_engine    — in-memory SQLite async engine (per test), or PostgreSQL
                   built from the migrations when TEST_DATABASE_URL is set
  - db_session   — AsyncSession bound to db_engine
  - governance   — GovernanceAgent instance wired to test DB
  - registry     — SemanticLayerRegistry wired to test DB
  - approved_audit_entry_id — UUID of a pre-written APPROVED audit entry
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Ensure tests run in local mode with SQLite
os.environ.setdefault("ENV", "local")

# ── Import after ENV is set ────────────────────────────────────────────────────
from src.agents.governance import GovernanceAgent
from src.db.factory import SQLITE_DDL, CloudSqlFactory, ddl_statements
from src.db.types import TEXT_ARRAY, TIMESTAMP, typed_text
from src.semantic_layer.models import (
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    EdgeDirection,
    EdgeType,
    EntityType,
    EpistemicState,
    GraphFragment,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry
from src.semantic_layer.verdict import claim as verdict_claim
from src.semantic_layer.verdict import sign as verdict_sign

# The tests run the schema the dev engine creates, imported rather than copied.
# This file used to hold its own near-identical version of it, which drifted:
# the suite was green against an audit_log that production did not have.
SQLITE_SCHEMA = SQLITE_DDL


# ── Engine fixture: in-memory SQLite by default, PostgreSQL on request ────────
#
# The default is a fresh in-memory SQLite per test, built from SQLITE_DDL.
#
# With TEST_DATABASE_URL set (postgresql+asyncpg://user:pass@host:port/db), the
# same fixtures run against PostgreSQL instead, on a schema built from
# src/db/migrations/*.sql applied in order -- the schema production runs, not
# SQLITE_DDL. The suite used to know only SQLite, whose types are loose enough
# to accept a JSON string into a text[] column and an ISO string into a
# timestamptz, which asyncpg refuses: POST /api/governance/check could not write
# audit_log on PostgreSQL, and no test had ever said so.
#
# Isolation: one database per test session, created and migrated when the first
# test asks for it (named ``causal_test_<random>``, dropped at the end; the
# database named in the URL is used only to create it), then every table is
# TRUNCATEd before each test. TRUNCATE does not fire the append-only row
# triggers, which is why it, and not DELETE, is what resets them.

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "db" / "migrations"


def migration_files() -> list[Path]:
    """The PostgreSQL migrations, in the order they are applied."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _test_database_url() -> URL | None:
    raw = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not raw:
        return None
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(
            f"TEST_DATABASE_URL must be a postgresql+asyncpg:// URL, got {url.drivername}"
        )
    return url.set(drivername="postgresql+asyncpg")


def _asyncpg_dsn(url: URL, database: str) -> str:
    return url.set(drivername="postgresql", database=database).render_as_string(
        hide_password=False
    )


#: The backend the db_engine fixture uses in this run: "sqlite" or "postgresql".
TEST_DB_URL = _test_database_url()
TEST_DB_BACKEND = "postgresql" if TEST_DB_URL is not None else "sqlite"

#: For a test that needs the PostgreSQL schema (the migrations) to mean anything.
postgres_only = pytest.mark.skipif(
    TEST_DB_BACKEND != "postgresql", reason="needs PostgreSQL: set TEST_DATABASE_URL"
)


async def _create_migrated_database(url: URL, name: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(_asyncpg_dsn(url, url.database or "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(_asyncpg_dsn(url, name))
    try:
        for path in migration_files():
            # No arguments: asyncpg uses the simple query protocol, which runs a
            # whole script (DO blocks, functions, BEGIN/COMMIT) as psql would.
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _drop_database(url: URL, name: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(_asyncpg_dsn(url, url.database or "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def postgres_test_url() -> Generator[URL | None, None, None]:
    """A freshly migrated PostgreSQL database for this session, or None (SQLite run)."""
    if TEST_DB_URL is None:
        yield None
        return
    import asyncio

    name = f"causal_test_{uuid4().hex[:12]}"
    asyncio.run(_create_migrated_database(TEST_DB_URL, name))
    try:
        yield TEST_DB_URL.set(database=name)
    finally:
        asyncio.run(_drop_database(TEST_DB_URL, name))


async def _truncate_all(engine: AsyncEngine) -> None:
    """Empty every table, and restore any trigger a test disabled.

    A tamper test plays the attacker by disabling an append-only trigger
    (tests/integration/test_decision_ledger.py). The database outlives the test,
    so the guard comes back here, before the next one runs.
    """
    async with engine.begin() as conn:
        disabled = (
            await conn.execute(
                text(
                    "SELECT c.relname, t.tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal AND t.tgenabled = 'D'"
                )
            )
        ).all()
        for table_name, trigger in disabled:
            await conn.execute(text(f'ALTER TABLE "{table_name}" ENABLE TRIGGER "{trigger}"'))
        tables = (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).scalars().all()
        if tables:
            names = ", ".join(f'"{t}"' for t in sorted(tables))
            await conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


async def _sqlite_engine() -> AsyncEngine:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    # Enable FK enforcement per connection (SQLite requires this explicitly)
    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk_pragma(dbapi_conn: object, _: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        for statement in ddl_statements(SQLITE_SCHEMA):
            await conn.execute(text(statement))
    return engine


@pytest_asyncio.fixture
async def db_engine(postgres_test_url: URL | None) -> AsyncGenerator[AsyncEngine, None]:
    """
    A clean database for each test: in-memory SQLite, or PostgreSQL when
    TEST_DATABASE_URL is set (see above).

    Resets the CloudSqlFactory singleton so each test gets a clean state.
    """
    CloudSqlFactory.reset()
    os.environ["ENV"] = "local"
    os.environ["SQLITE_PATH"] = ":memory:"

    if postgres_test_url is None:
        engine = await _sqlite_engine()
    else:
        engine = create_async_engine(postgres_test_url, echo=False)
        await _truncate_all(engine)

    # Inject the test engine directly via class-level cache variables
    CloudSqlFactory.reset()
    CloudSqlFactory._engine = engine
    CloudSqlFactory._session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    yield engine

    await engine.dispose()
    CloudSqlFactory.reset()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession connected to the test engine."""
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session


#: What `verdict_for` is: give it the object you are about to write, get back a
#: verdict that authorises that write and nothing else.
VerdictFactory = Callable[[UUID], Awaitable[UUID]]


# ── Issuing Guardian verdicts in tests ───────────────────────────────────────
#
# A verdict authorises exactly one write: it is signed by the issuer, names the
# object it was issued for, and is spent on use. So a test cannot hold one
# pre-seeded verdict and spend it on whatever it likes -- it asks for one per
# object, the way real code does.
#
# `insert_raw_audit_entry` is the *unsigned* insert these fixtures used to do. It
# is kept because forging is now something worth testing: a row written straight
# into audit_log must be readable as history and refused as authority.


#: The one INSERT the fixtures write audit rows with, typed the way the Guardian's
#: own write is (src/db/types.py), so it runs on SQLite and on PostgreSQL alike.
_INSERT_AUDIT_ROW = typed_text(
    "INSERT INTO audit_log "
    "(id, agent_id, action_type, payload_hash, guardian_verdict, "
    " policy_rules_cited, session_id, created_at, subject_id, signature) "
    "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts, "
    " :subject_id, :signature)",
    rules=TEXT_ARRAY,
    ts=TIMESTAMP,
)


async def insert_raw_audit_entry(
    session: AsyncSession,
    *,
    verdict: str = "APPROVED",
    subject_id: UUID | None = None,
    signature: str | None = None,
    agent_id: str = "test-agent",
    action_type: str = "TEST_FIXTURE",
    payload_hash: str | None = None,
) -> UUID:
    """Insert a row into audit_log directly, bypassing the Guardian.

    This is what an attacker with database access -- or the interventions API --
    can do. Without a valid signature the row cannot authorise a semantic write.
    """
    entry_id = uuid4()
    await session.execute(
        _INSERT_AUDIT_ROW,
        {
            "id": str(entry_id),
            "agent_id": agent_id,
            "action_type": action_type,
            "hash": payload_hash or hashlib.sha256(b"fixture").hexdigest(),
            "verdict": verdict,
            "rules": ["PR-FIXTURE"],
            "sid": "test-session-001",
            "ts": datetime.now(UTC),
            "subject_id": str(subject_id) if subject_id else None,
            "signature": signature,
        },
    )
    await session.commit()
    return entry_id


async def issue_verdict_for(
    session: AsyncSession,
    subject_id: UUID,
    *,
    verdict: str = "APPROVED",
    agent_id: str = "test-agent",
    action_type: str = "TEST_FIXTURE",
) -> UUID:
    """Issue a properly signed verdict bound to ``subject_id``.

    Signs the same claim the Guardian signs, so the registry accepts it, without
    needing to stand up a GovernanceAgent for every fixture.
    """
    entry_id = uuid4()
    created_at = datetime.now(UTC).isoformat()
    payload_hash = hashlib.sha256(str(subject_id).encode()).hexdigest()
    signature = verdict_sign(
        verdict_claim(
            verdict_id=entry_id,
            subject_id=subject_id,
            payload_hash=payload_hash,
            verdict=verdict,
            action_type=action_type,
            agent_id=agent_id,
            created_at=created_at,
        )
    )
    await session.execute(
        _INSERT_AUDIT_ROW,
        {
            "id": str(entry_id),
            "agent_id": agent_id,
            "action_type": action_type,
            "hash": payload_hash,
            "verdict": verdict,
            "rules": ["PR-FIXTURE"],
            "sid": "test-session-001",
            "ts": created_at,
            "subject_id": str(subject_id),
            "signature": signature,
        },
    )
    await session.commit()
    return entry_id


@pytest_asyncio.fixture
async def approved_audit_entry_id(db_session: AsyncSession) -> UUID:
    """An APPROVED row in audit_log, written directly -- which is all this ever was.

    Useful as a `guardian_verdict_ref` foreign key and as the forged verdict the
    binding tests must see refused. It authorises nothing: it is unsigned and names
    no subject. Tests that need authority use `verdict_for`.
    """
    return await insert_raw_audit_entry(db_session)


@pytest_asyncio.fixture
async def verdict_for(db_session: AsyncSession) -> VerdictFactory:
    """Factory: ``await verdict_for(obj.id)`` -> a verdict that authorises that write."""

    async def _issue(subject_id: UUID, verdict: str = "APPROVED") -> UUID:
        return await issue_verdict_for(db_session, subject_id, verdict=verdict)

    return _issue


@pytest_asyncio.fixture
async def rejected_audit_entry_id(db_session: AsyncSession) -> UUID:
    """A REJECTED verdict, properly signed: refused because of its verdict, not its shape."""
    return await issue_verdict_for(db_session, uuid4(), verdict="REJECTED")


# ── Shared model factories ─────────────────────────────────────────────────────


def make_user_ref(uid: str = "test-user-001") -> UserRef:
    return UserRef(id=uid, email=f"{uid}@test.com", name="Test User")


def make_causal_entity(domain: str = "devops") -> CausalEntity:
    return CausalEntity(
        name="test-service",
        entity_type=EntityType.SERVICE,
        domain=domain,
        description="Test service entity",
        ontology_version="1.0.0",
        created_by=make_user_ref(),
    )


def make_causal_edge(verdict_id: UUID, domain: str = "devops") -> CausalEdge:
    source_id = uuid4()
    target_id = uuid4()
    return CausalEdge(
        source=CausalObjectRef(id=source_id, object_type="CausalEntity"),
        target=CausalObjectRef(id=target_id, object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        direction=EdgeDirection.FORWARD,
        confidence=0.85,
        guardian_verdict_ref=verdict_id,
        domain=domain,
    )


def make_graph_fragment(domain: str = "devops") -> GraphFragment:
    return GraphFragment(
        name="test-fragment",
        domain=domain,
        description="Test graph fragment",
        created_by=make_user_ref(),
    )


def make_epistemic_state(
    session_id: str = "test-session",
    agent_id: str = "test-agent",
) -> EpistemicState:
    return EpistemicState(session_id=session_id, agent_id=agent_id)


# ── Registry and governance fixtures ──────────────────────────────────────────


@pytest.fixture
def registry(db_engine: AsyncEngine) -> SemanticLayerRegistry:
    """Return a SemanticLayerRegistry wired to the test database."""
    return SemanticLayerRegistry()


@pytest.fixture
def governance(db_engine: AsyncEngine) -> GovernanceAgent:
    """Return a GovernanceAgent wired to the test database."""
    return GovernanceAgent(agent_id="test-governance-agent")

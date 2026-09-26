"""
Shared pytest fixtures for the Unified Enterprise Causal Platform test suite.

Fixture hierarchy:
  - db_engine    — the engine the app's code runs on: in-memory SQLite (per
                   test), or, when TEST_DATABASE_URL is set, PostgreSQL built from
                   the migrations and connected as causal_app, so row-level
                   security binds every query the app makes
  - db_session   — AsyncSession for seeding and reading back: on db_engine for
                   SQLite, on the owner's connection for PostgreSQL
  - governance   — GovernanceAgent instance wired to test DB
  - registry     — SemanticLayerRegistry wired to test DB
  - approved_audit_entry_id — UUID of a pre-written APPROVED audit entry
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from dataclasses import dataclass
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
from src.db.scope import install_scope_listener, service_scope
from src.db.types import TEXT_ARRAY, TIMESTAMP, typed_text
from src.semantic_layer.models import (
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    DataSource,
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


# ── No ambient LLM ───────────────────────────────────────────────────────────
#
# The platform calls an LLM whenever a provider is configured (src/agents/narration.py),
# so a developer's OLLAMA_BASE_URL or cloud key would reach every agent test and make
# its text depend on a live model. Each test starts with no provider configured; a
# test that wants one sets it (with a fake client, tests/unit/_narration_support.py).
#
# The live tests (@pytest.mark.llm_live) are the exception, and run only with
# LLM_LIVE=1. Otherwise they are DESELECTED, not skipped: the PostgreSQL gate step
# fails on any skip, and a deselected test is not a test that failed to run but one
# nobody asked for.

_LLM_PROVIDER_ENV = (
    "OLLAMA_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_FILE",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY_FILE",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("LLM_LIVE") == "1":
        return
    live = [item for item in items if item.get_closest_marker("llm_live")]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if not item.get_closest_marker("llm_live")]


@pytest.fixture(autouse=True)
def _no_ambient_llm(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("llm_live"):
        return
    for name in _LLM_PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


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
#
# Two connections to that database. The URL's own user (it must be able to
# create databases and roles) builds it, truncates it and seeds it: it owns the
# tables, so row-level security does not bind it. The app's code -- everything
# that goes through CloudSqlFactory -- connects as causal_app instead, the role
# production connects as (docs/QUICKSTART.md), which RLS binds. The login is a
# per-run role, ``causal_test_login_<random>``, member of causal_app, whose
# default ``role`` setting makes every session it opens run AS causal_app; its
# password is random, generated here, and dies with the role at the end of the
# run. Nothing about causal_app itself is changed, so two runs on one server do
# not collide.
#
# The domain scope the policies read comes from src/db/scope.py: per request,
# the auth middleware binds the caller's. A test that calls the registry or the
# stores directly has no request and so no user; `db_engine` declares a service
# scope for it (every domain), explicitly, as any non-request code must.

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


#: The role the application connects as (001_initial.sql creates it).
APP_ROLE = "causal_app"


async def _create_migrated_database(url: URL, name: str, login: str, password: str) -> None:
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
        # The app's login for this run. Identifiers and the password are ours
        # (hex), so formatting them in is not an injection path; CREATE ROLE
        # takes no bind parameters.
        await conn.execute(
            f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB '
            f"NOCREATEROLE PASSWORD '{password}' IN ROLE {APP_ROLE}"
        )
        await conn.execute(f'ALTER ROLE "{login}" SET role = {APP_ROLE}')
    finally:
        await conn.close()


async def _drop_database(url: URL, name: str, login: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(_asyncpg_dsn(url, url.database or "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{login}"')
    finally:
        await admin.close()


@dataclass(frozen=True)
class PostgresTestDatabase:
    """This run's migrated database: as its owner, and as the app connects."""

    owner_url: URL
    app_url: URL


@pytest.fixture(scope="session")
def postgres_test_db() -> Generator[PostgresTestDatabase | None, None, None]:
    """A freshly migrated PostgreSQL database for this session, or None (SQLite run)."""
    if TEST_DB_URL is None:
        yield None
        return
    import asyncio

    suffix = uuid4().hex[:12]
    name = f"causal_test_{suffix}"
    login = f"causal_test_login_{suffix}"
    password = secrets.token_hex(24)
    asyncio.run(_create_migrated_database(TEST_DB_URL, name, login, password))
    try:
        owner_url = TEST_DB_URL.set(database=name)
        yield PostgresTestDatabase(
            owner_url=owner_url,
            app_url=owner_url.set(username=login, password=password),
        )
    finally:
        asyncio.run(_drop_database(TEST_DB_URL, name, login))


@pytest.fixture(scope="session")
def postgres_test_url(postgres_test_db: PostgresTestDatabase | None) -> URL | None:
    """The owner's URL of this run's PostgreSQL database, or None (SQLite run)."""
    return None if postgres_test_db is None else postgres_test_db.owner_url


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
async def db_owner_engine(
    postgres_test_db: PostgresTestDatabase | None,
) -> AsyncGenerator[AsyncEngine | None, None]:
    """PostgreSQL only: the owner's engine, emptied for this test. None on SQLite."""
    if postgres_test_db is None:
        yield None
        return
    engine = create_async_engine(postgres_test_db.owner_url, echo=False)
    await _truncate_all(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_engine(
    postgres_test_db: PostgresTestDatabase | None,
    db_owner_engine: AsyncEngine | None,
) -> AsyncGenerator[AsyncEngine, None]:
    """
    The engine the app's code runs on, over a clean database for each test:
    in-memory SQLite, or PostgreSQL connected as causal_app when
    TEST_DATABASE_URL is set (see above).

    Resets the CloudSqlFactory singleton so each test gets a clean state, and
    declares a service scope for the test's own direct calls (see above);
    requests the test makes run under their caller's scope instead.
    """
    CloudSqlFactory.reset()
    os.environ["ENV"] = "local"
    os.environ["SQLITE_PATH"] = ":memory:"

    if postgres_test_db is None:
        engine = await _sqlite_engine()
    else:
        engine = create_async_engine(postgres_test_db.app_url, echo=False)
        install_scope_listener(engine)

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

    with service_scope("test fixture: direct calls outside a request"):
        yield engine

    await engine.dispose()
    CloudSqlFactory.reset()


@pytest_asyncio.fixture
async def db_session(
    db_engine: AsyncEngine, db_owner_engine: AsyncEngine | None
) -> AsyncGenerator[AsyncSession, None]:
    """An AsyncSession for seeding and reading back.

    On PostgreSQL it connects as the tables' owner, which row-level security does
    not bind, so a fixture can seed rows in any domain and a test can read back
    what the app wrote whatever the scope. What the app itself can see is
    db_engine's business.
    """
    session_factory = async_sessionmaker(
        db_owner_engine if db_owner_engine is not None else db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session


#: What `verdict_for` is: give it the object you are about to write, get back a
#: verdict that authorises that write and nothing else. The optional arguments are
#: the verdict (default APPROVED) and the action it was issued for: an approval needs
#: ``action_type="GOVERNANCE_APPROVE"`` (septa-60), a rejection
#: ``action_type="GOVERNANCE_REJECT"`` (septa-53).
VerdictFactory = Callable[..., Awaitable[UUID]]


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

    async def _issue(
        subject_id: UUID, verdict: str = "APPROVED", action_type: str = "TEST_FIXTURE"
    ) -> UUID:
        return await issue_verdict_for(
            db_session, subject_id, verdict=verdict, action_type=action_type
        )

    return _issue


@pytest_asyncio.fixture
async def rejected_audit_entry_id(db_session: AsyncSession) -> UUID:
    """A REJECTED verdict, properly signed: refused because of its verdict, not its shape."""
    return await issue_verdict_for(db_session, uuid4(), verdict="REJECTED")


# ── Shared model factories ─────────────────────────────────────────────────────


def make_user_ref(uid: str = "test-user-001") -> UserRef:
    return UserRef(id=uid, email=f"{uid}@test.com", name="Test User")


def make_data_source(ref: str = "test://fixtures/source.csv") -> DataSource:
    """Source data for a fixture object. Approval requires one (septa-15)."""
    return DataSource(
        kind="dataset", ref=ref, digest="sha256:" + hashlib.sha256(ref.encode()).hexdigest()
    )


#: The same, as the JSON a test that seeds a row directly puts in `source_data`.
SOURCE_DATA_JSON = make_data_source().model_dump_json()


def make_causal_entity(domain: str = "devops") -> CausalEntity:
    return CausalEntity(
        name="test-service",
        entity_type=EntityType.SERVICE,
        domain=domain,
        description="Test service entity",
        ontology_version="1.0.0",
        created_by=make_user_ref(),
        source_data=make_data_source(),
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
        source_data=make_data_source(),
    )


def make_graph_fragment(domain: str = "devops") -> GraphFragment:
    return GraphFragment(
        name="test-fragment",
        domain=domain,
        description="Test graph fragment",
        created_by=make_user_ref(),
        source_data=make_data_source(),
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

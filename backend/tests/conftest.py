"""
Shared pytest fixtures for the Unified Enterprise Causal Platform test suite.

Fixture hierarchy:
  - db_engine    — in-memory SQLite async engine (per test)
  - db_session   — AsyncSession bound to db_engine
  - governance   — GovernanceAgent instance wired to test DB
  - registry     — SemanticLayerRegistry wired to test DB
  - approved_audit_entry_id — UUID of a pre-written APPROVED audit entry
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
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
from src.db.factory import SQLITE_DDL, CloudSqlFactory
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


# ── Engine fixture (per-test isolated in-memory SQLite) ───────────────────────


@pytest_asyncio.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """
    Create a fresh in-memory SQLite async engine for each test.

    Resets the CloudSqlFactory singleton so each test gets a clean state.
    """
    CloudSqlFactory.reset()
    os.environ["ENV"] = "local"
    os.environ["SQLITE_PATH"] = ":memory:"

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

    # Create schema
    async with engine.begin() as conn:
        for statement in SQLITE_SCHEMA.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))

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
        text(
            "INSERT INTO audit_log "
            "(id, agent_id, action_type, payload_hash, guardian_verdict, "
            " policy_rules_cited, session_id, created_at, subject_id, signature) "
            "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts, "
            " :subject_id, :signature)"
        ),
        {
            "id": str(entry_id),
            "agent_id": agent_id,
            "action_type": action_type,
            "hash": payload_hash or hashlib.sha256(b"fixture").hexdigest(),
            "verdict": verdict,
            "rules": json.dumps(["PR-FIXTURE"]),
            "sid": "test-session-001",
            "ts": datetime.now(UTC).isoformat(),
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
        text(
            "INSERT INTO audit_log "
            "(id, agent_id, action_type, payload_hash, guardian_verdict, "
            " policy_rules_cited, session_id, created_at, subject_id, signature) "
            "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts, "
            " :subject_id, :signature)"
        ),
        {
            "id": str(entry_id),
            "agent_id": agent_id,
            "action_type": action_type,
            "hash": payload_hash,
            "verdict": verdict,
            "rules": json.dumps(["PR-FIXTURE"]),
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

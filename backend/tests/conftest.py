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
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any
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
from src.db.factory import CloudSqlFactory
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    EdgeDirection,
    EdgeType,
    ElicitedFrom,
    EntityType,
    EpistemicState,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry

# ── SQLite DDL compatible with the PostgreSQL schema ──────────────────────────
# SQLite does not support all PostgreSQL DDL features. We use a simplified
# schema that covers the columns needed by the test suite.

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    payload_hash        TEXT NOT NULL,
    guardian_verdict    TEXT NOT NULL
        CHECK (guardian_verdict IN ('APPROVED','REJECTED','REQUIRES_ESCALATION')),
    policy_rules_cited  TEXT NOT NULL DEFAULT '[]',
    session_id          TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS causal_entities (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    domain              TEXT NOT NULL,
    description         TEXT,
    attributes          TEXT NOT NULL DEFAULT '{}',
    ontology_version    TEXT NOT NULL DEFAULT '1.0.0',
    approval_status     TEXT NOT NULL DEFAULT 'DRAFT',
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    approved_at         TEXT,
    approved_by         TEXT,
    lineage             TEXT NOT NULL DEFAULT '[]',
    tags                TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS assumptions (
    id                  TEXT PRIMARY KEY,
    statement           TEXT NOT NULL,
    assumption_type     TEXT NOT NULL,
    scope               TEXT NOT NULL DEFAULT '[]',
    justification       TEXT NOT NULL,
    elicited_from       TEXT NOT NULL,
    elicited_by         TEXT NOT NULL,
    elicited_at         TEXT NOT NULL,
    review_status       TEXT NOT NULL DEFAULT 'PENDING',
    last_reviewed_at    TEXT,
    next_review_due     TEXT,
    sensitivity_result  TEXT,
    domain              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS causal_edges (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    target_type         TEXT NOT NULL,
    edge_type           TEXT NOT NULL,
    direction           TEXT NOT NULL DEFAULT 'FORWARD',
    confidence          REAL NOT NULL,
    evidence_refs       TEXT NOT NULL DEFAULT '[]',
    assumption_refs     TEXT NOT NULL DEFAULT '[]',
    approval_status     TEXT NOT NULL DEFAULT 'DRAFT',
    approved_by         TEXT,
    approved_at         TEXT,
    guardian_verdict_ref TEXT NOT NULL
        REFERENCES audit_log(id) DEFERRABLE INITIALLY DEFERRED,
    version             TEXT NOT NULL DEFAULT '1.0.0',
    lineage             TEXT NOT NULL DEFAULT '[]',
    domain              TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_fragments (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    domain              TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    edge_ids            TEXT NOT NULL DEFAULT '[]',
    entry_nodes         TEXT NOT NULL DEFAULT '[]',
    exit_nodes          TEXT NOT NULL DEFAULT '[]',
    approval_status     TEXT NOT NULL DEFAULT 'DRAFT',
    version             TEXT NOT NULL DEFAULT '1.0.0',
    usage_count         INTEGER NOT NULL DEFAULT 0,
    tags                TEXT NOT NULL DEFAULT '[]',
    guardian_verdict_ref TEXT REFERENCES audit_log(id),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    approved_at         TEXT,
    approved_by         TEXT
);

CREATE TABLE IF NOT EXISTS interventions (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    treatment_ref           TEXT,
    target_entity_ref       TEXT,
    initiated_by            TEXT NOT NULL,
    initiated_at            TEXT NOT NULL,
    approved_by             TEXT NOT NULL,
    approval_timestamp      TEXT NOT NULL,
    guardian_verdict_ref    TEXT NOT NULL REFERENCES audit_log(id),
    expected_effect         TEXT NOT NULL,
    actual_effect           TEXT,
    feedback_loop_closed    INTEGER NOT NULL DEFAULT 0,
    outcome_observed_at     TEXT,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_state (
    session_id           TEXT PRIMARY KEY,
    user_id              TEXT,
    agent_id             TEXT NOT NULL,
    state_json           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""


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
        for statement in SQLITE_SCHEMA.strip().split(";\n\n"):
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


# ── Pre-seeded approved audit entry ──────────────────────────────────────────


@pytest_asyncio.fixture
async def approved_audit_entry_id(db_session: AsyncSession) -> UUID:
    """
    Insert an APPROVED audit_log entry and return its UUID.

    Used by tests that need a valid guardian_verdict_ref.
    """
    entry_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO audit_log "
            "(id, agent_id, action_type, payload_hash, guardian_verdict, "
            " policy_rules_cited, session_id, created_at) "
            "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts)"
        ),
        {
            "id": str(entry_id),
            "agent_id": "test-agent",
            "action_type": "TEST_FIXTURE",
            "hash": hashlib.sha256(b"fixture").hexdigest(),
            "verdict": "APPROVED",
            "rules": json.dumps(["PR-FIXTURE"]),
            "sid": "test-session-001",
            "ts": datetime.utcnow().isoformat(),
        },
    )
    await db_session.commit()
    return entry_id


@pytest_asyncio.fixture
async def rejected_audit_entry_id(db_session: AsyncSession) -> UUID:
    """Insert a REJECTED audit_log entry and return its UUID."""
    entry_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO audit_log "
            "(id, agent_id, action_type, payload_hash, guardian_verdict, "
            " policy_rules_cited, session_id, created_at) "
            "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts)"
        ),
        {
            "id": str(entry_id),
            "agent_id": "test-agent",
            "action_type": "TEST_FIXTURE",
            "hash": hashlib.sha256(b"rejected-fixture").hexdigest(),
            "verdict": "REJECTED",
            "rules": json.dumps(["PR-001"]),
            "sid": "test-session-rejected",
            "ts": datetime.utcnow().isoformat(),
        },
    )
    await db_session.commit()
    return entry_id


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

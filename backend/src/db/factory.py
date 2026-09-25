"""
CloudSqlFactory — database engine and session factory.

Selection logic (matches carfdevops / projectcarfcynepic pattern):
  - ENV=local  (or ENV unset)  → SQLite via aiosqlite
  - ENV=cloudrun               → PostgreSQL via asyncpg with IAM-authenticated
                                  Cloud SQL socket

Code outside this module NEVER instantiates engine or connection objects
directly.  Always use:

    engine = CloudSqlFactory.get_engine()
    async_session = CloudSqlFactory.get_session_factory()

    async with async_session() as session:
        ...

This ensures transparent local ↔ Cloud Run switching with zero application
code changes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.secret_source import deployment_env, read_secret

log = structlog.get_logger(__name__)

# ── Sentinel: track whether the engine has been initialised ──────────────────
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for all ORM models."""


# ── Configuration helpers ─────────────────────────────────────────────────────


def _get_env() -> str:
    # The one local-vs-deployed rule, shared with the signing-key check.
    return deployment_env()


#: The schema the local/dev engine creates. The test suite runs *this* string --
#: it used to keep its own copy in tests/conftest.py, which is how a suite ends up
#: green against a schema production does not have.
def ddl_statements(ddl: str) -> list[str]:
    """Split a DDL script into executable statements.

    Splitting on every ``;`` breaks on a semicolon inside a ``--`` comment, which is
    how a schema change can fail with "near 'the': syntax error" and take a while to
    place. Comments are stripped first, and the split is on the statement terminator
    at the end of a line.

    A trigger body is the other place a ``;`` is not a terminator: SQLite's
    ``CREATE TRIGGER ... BEGIN SELECT RAISE(...); END;`` carries one inside the
    block. Splitting naively yields two fragments, neither of which parses, so
    a schema that declares triggers would fail to build -- or, worse, be
    dropped quietly by a caller that tolerates errors. Inside a trigger, only a
    line ending in ``END;`` terminates the statement.
    """
    stripped = [
        line for line in ddl.splitlines() if not line.lstrip().startswith("--")
    ]

    statements: list[str] = []
    buffer: list[str] = []
    in_trigger = False

    for line in stripped:
        buffer.append(line)
        text = line.strip()
        upper = text.upper()

        if not in_trigger and upper.startswith("CREATE TRIGGER"):
            in_trigger = True

        if in_trigger:
            if upper.endswith("END;"):
                statements.append("\n".join(buffer).strip().rstrip(";").strip())
                buffer = []
                in_trigger = False
            continue

        while ";" in text:
            head, _, text = text.partition(";")
            buffer[-1] = head
            candidate = "\n".join(buffer).strip()
            if candidate:
                statements.append(candidate)
            buffer = [text]

    tail = "\n".join(buffer).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    payload_hash        TEXT NOT NULL,
    guardian_verdict    TEXT NOT NULL
        CHECK (guardian_verdict IN ('APPROVED','REJECTED','REQUIRES_ESCALATION','PENDING')),
    policy_rules_cited  TEXT NOT NULL DEFAULT '[]',
    session_id          TEXT,
    created_at          TEXT NOT NULL,
    subject_id          TEXT,
    signature           TEXT,
    notes               TEXT
);
-- One verdict, one write. audit_log cannot carry a "used" flag because it is
-- append-only, so consumption is an insert here and the primary key is the rule:
-- the second attempt to spend a verdict violates it.
-- Decision records: the hash chain from causal_decision.ledger, one row per link.
-- `record` is the sealed entry as canonical JSON; the columns beside it exist so the
-- chain can be walked and served without parsing every row. They are a copy of what
-- the record already says, so the verifier reads the record and recomputes -- a row
-- whose columns disagree with its own record is exactly what tampering looks like.
CREATE TABLE IF NOT EXISTS decision_records (
    sequence            INTEGER PRIMARY KEY,
    entry_digest        TEXT NOT NULL UNIQUE,
    prev_digest         TEXT NOT NULL,
    decision_digest     TEXT NOT NULL,
    subject             TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    record              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_followups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_digest        TEXT NOT NULL,
    decision_digest     TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('plan', 'realization')),
    decidable           INTEGER,
    created_at          TEXT NOT NULL,
    payload             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_decision_followups_entry
    ON decision_followups (entry_digest, id);
-- One plan and one realization per decision (005_one_followup_per_decision.sql).
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_followups_one_per_kind
    ON decision_followups (entry_digest, kind);
CREATE TABLE IF NOT EXISTS verdict_consumption (
    verdict_id          TEXT PRIMARY KEY
        REFERENCES audit_log(id),
    subject_id          TEXT NOT NULL,
    context             TEXT NOT NULL,
    consumed_at         TEXT NOT NULL
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
    guardian_verdict_ref TEXT,
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
    approved_by         TEXT,
    -- 006_graph_fragment_lineage.sql: approval and rejection append to it.
    lineage             TEXT NOT NULL DEFAULT '[]'
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
-- 008_session_state.sql / 009_incidents.sql are the PostgreSQL versions of these two.
CREATE INDEX IF NOT EXISTS ix_session_state_agent_updated
    ON session_state (agent_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS incidents (
    id                       TEXT PRIMARY KEY,
    title                    TEXT NOT NULL,
    severity                 TEXT NOT NULL,
    affected_services        TEXT NOT NULL DEFAULT '[]',
    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    session_id               TEXT,
    cynefin_domain           TEXT,
    domain_confidence        REAL,
    overall_confidence       REAL,
    guardian_verdict         TEXT,
    draft_explanation        TEXT NOT NULL DEFAULT '',
    causal_hypotheses        TEXT NOT NULL DEFAULT '[]',
    proposed_interventions   TEXT NOT NULL DEFAULT '[]',
    submitted_by             TEXT NOT NULL,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_incidents_created_at ON incidents (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents (status);

-- audit_log is append-only. 001_initial.sql enforces this in PostgreSQL with
-- fn_audit_log_no_mutation; these are the SQLite equivalents, added 2026-09-24.
-- Without them the invariant held in production and not in any test: the
-- guardian suite's UPDATE and DELETE cases ran against a store that allowed
-- both, and passed because neither re-read the row afterwards.
-- decision_records and decision_followups are append-only too, and were
-- enforced only by 003/004's PostgreSQL triggers before 2026-09-24. A record of
-- what was decided is worth nothing if the decider can revise it afterwards, and
-- a calibration row is worth nothing if a disappointing one can be deleted.
CREATE TRIGGER IF NOT EXISTS tg_decision_records_no_update
BEFORE UPDATE ON decision_records
BEGIN
    SELECT RAISE(ABORT, 'decision_records is append-only: UPDATE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_decision_records_no_delete
BEFORE DELETE ON decision_records
BEGIN
    SELECT RAISE(ABORT, 'decision_records is append-only: DELETE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_decision_followups_no_update
BEFORE UPDATE ON decision_followups
BEGIN
    SELECT RAISE(ABORT, 'decision_followups is append-only: UPDATE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_decision_followups_no_delete
BEFORE DELETE ON decision_followups
BEGIN
    SELECT RAISE(ABORT, 'decision_followups is append-only: DELETE is not permitted');
END;
-- The assumption registry (007_causal_models.sql), append-only like the chain.
CREATE TABLE IF NOT EXISTS causal_model_versions (
    digest              TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    version             INTEGER NOT NULL CHECK (version >= 1),
    parent_digest       TEXT REFERENCES causal_model_versions(digest),
    author_id           TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    record              TEXT NOT NULL,
    UNIQUE (name, version)
);
CREATE TABLE IF NOT EXISTS decision_model_refs (
    entry_digest        TEXT PRIMARY KEY,
    model_name          TEXT NOT NULL,
    model_version       INTEGER NOT NULL,
    model_digest        TEXT NOT NULL REFERENCES causal_model_versions(digest),
    verdict             TEXT NOT NULL,
    action              TEXT NOT NULL,
    query               TEXT NOT NULL,
    unmeasured          TEXT NOT NULL DEFAULT '[]',
    recorded_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_decision_model_refs_model
    ON decision_model_refs (model_name, model_version);
CREATE TRIGGER IF NOT EXISTS tg_causal_model_versions_no_update
BEFORE UPDATE ON causal_model_versions
BEGIN
    SELECT RAISE(ABORT, 'causal_model_versions is append-only: UPDATE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_causal_model_versions_no_delete
BEFORE DELETE ON causal_model_versions
BEGIN
    SELECT RAISE(ABORT, 'causal_model_versions is append-only: DELETE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_decision_model_refs_no_update
BEFORE UPDATE ON decision_model_refs
BEGIN
    SELECT RAISE(ABORT, 'decision_model_refs is append-only: UPDATE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_decision_model_refs_no_delete
BEFORE DELETE ON decision_model_refs
BEGIN
    SELECT RAISE(ABORT, 'decision_model_refs is append-only: DELETE is not permitted');
END;
CREATE TRIGGER IF NOT EXISTS tg_audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE operations are forbidden');
END;
CREATE TRIGGER IF NOT EXISTS tg_audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE operations are forbidden');
END;
"""


def _sqlite_url() -> str:
    """Return the aiosqlite connection URL for local development."""
    db_path = os.getenv(
        "SQLITE_PATH",
        str(Path(__file__).parent.parent.parent / "local_dev.db"),
    )
    return f"sqlite+aiosqlite:///{db_path}"


def _postgres_url() -> str:
    """
    Return the asyncpg connection URL for Cloud Run.

    Supports two modes:
    1. Unix socket (Cloud SQL Auth Proxy / direct socket)  — preferred on GCP
    2. TCP connection string                               — for integration tests

    If CLOUD_SQL_INSTANCE_CONNECTION_NAME is set, uses the Unix socket path.
    Otherwise falls back to DATABASE_URL env var (TCP).
    """
    instance_connection = os.getenv("CLOUD_SQL_INSTANCE_CONNECTION_NAME")
    if instance_connection:
        # Cloud SQL Unix socket path used when running in Cloud Run
        db_name = os.getenv("DB_NAME", "causal_platform")
        db_user = os.getenv("DB_USER", "causal_platform_sa")
        socket_dir = os.getenv("CLOUD_SQL_SOCKET_DIR", "/cloudsql")
        socket_path = f"{socket_dir}/{instance_connection}"
        return (
            f"postgresql+asyncpg://{db_user}@/{db_name}"
            f"?host={socket_path}"
        )

    # Fallback: explicit DATABASE_URL (TCP, used for integration tests)
    url = read_secret("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Running in non-local ENV but neither CLOUD_SQL_INSTANCE_CONNECTION_NAME "
            "nor DATABASE_URL (or DATABASE_URL_FILE) is set."
        )
    # Normalise postgres:// → postgresql+asyncpg://
    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _engine_kwargs(env: str) -> dict[str, Any]:
    """Return SQLAlchemy engine keyword arguments appropriate for the environment."""
    if env == "local":
        return {
            # SQLite: single file, no pool needed
            "connect_args": {"check_same_thread": False},
            "echo": os.getenv("SQL_ECHO", "false").lower() == "true",
        }
    # PostgreSQL / Cloud Run
    return {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_pre_ping": True,
        "pool_recycle": 1800,  # 30 min — avoids stale Cloud SQL connections
        "echo": os.getenv("SQL_ECHO", "false").lower() == "true",
    }


# ── Public API ────────────────────────────────────────────────────────────────


class CloudSqlFactory:
    """
    Factory for SQLAlchemy async engine and session objects.

    Usage::

        engine = CloudSqlFactory.get_engine()
        SessionLocal = CloudSqlFactory.get_session_factory()

        async with SessionLocal() as session:
            result = await session.execute(select(MyModel))

    All methods are idempotent — subsequent calls return the same singleton.
    Uses explicit class-level cache variables (not lru_cache on staticmethod,
    which has known descriptor issues in Python 3.13+).
    """

    # Explicit singleton cache — avoids lru_cache descriptor issues on staticmethod
    _engine: AsyncEngine | None = None
    _session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        """Return (or create) the singleton async engine."""
        if cls._engine is not None:
            return cls._engine

        env = _get_env()
        if env == "local":
            url = _sqlite_url()
            log.info("db.engine.sqlite", url=url)
        else:
            url = _postgres_url()
            log.info("db.engine.postgres", env=env)

        cls._engine = create_async_engine(url, **_engine_kwargs(env))
        log.info("db.engine.created", env=env, dialect=cls._engine.dialect.name)
        return cls._engine

    @classmethod
    def get_session_factory(cls) -> async_sessionmaker[AsyncSession]:
        """Return (or create) the singleton session factory."""
        if cls._session_factory is not None:
            return cls._session_factory

        engine = cls.get_engine()
        cls._session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        log.info("db.session_factory.created")
        return cls._session_factory

    @classmethod
    async def get_session(cls) -> AsyncGenerator[AsyncSession, None]:
        """
        FastAPI dependency that yields an AsyncSession and commits/rolls back.

        Usage in a route::

            @router.get("/foo")
            async def foo(session: AsyncSession = Depends(CloudSqlFactory.get_session)):
                ...
        """
        session_factory = cls.get_session_factory()
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @classmethod
    async def init_db(cls) -> None:
        """
        Create all tables for local dev (SQLite) and run ORM Base.metadata.

        On a SQLite engine: runs SQLITE_DDL, the SQLite rendering of the
        PostgreSQL migrations.

        On PostgreSQL the schema is src/db/migrations/*.sql, applied in order by
        whoever provisions the database (docker-compose mounts them as init
        scripts); SQLITE_DDL is never run there. The choice is by the engine's
        dialect, not by ENV: SQLite DDL sent to PostgreSQL fails, whatever ENV says.
        """
        from sqlalchemy import text as sa_text

        engine = cls.get_engine()

        if engine.dialect.name == "sqlite":
            async with engine.begin() as conn:
                for stmt in ddl_statements(SQLITE_DDL):
                    await conn.execute(sa_text(stmt))

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("db.tables.created")

    @classmethod
    def reset(cls) -> None:
        """
        Clear the cached engine and session factory.

        Only for use in tests — allows each test to get a fresh in-memory
        SQLite engine without cross-test contamination.
        """
        cls._engine = None
        cls._session_factory = None
        log.debug("db.factory.reset")

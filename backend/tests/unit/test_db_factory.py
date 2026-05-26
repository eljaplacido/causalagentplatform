"""
Unit tests for CloudSqlFactory (src/db/factory.py).

Tests environment detection, URL building, and singleton behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.db.factory import (
    CloudSqlFactory,
    _engine_kwargs,
    _get_env,
    _postgres_url,
    _sqlite_url,
)


# ── _get_env ──────────────────────────────────────────────────────────────────


def test_get_env_default() -> None:
    """ENV defaults to 'local' when unset."""
    with patch.dict(os.environ, {}, clear=True):
        assert _get_env() == "local"


def test_get_env_from_env_var() -> None:
    """ENV reads from ENV environment variable."""
    with patch.dict(os.environ, {"ENV": "staging"}, clear=True):
        assert _get_env() == "staging"


def test_get_env_lowercased() -> None:
    """ENV value is lowercased."""
    with patch.dict(os.environ, {"ENV": "CLOUDRUN"}, clear=True):
        assert _get_env() == "cloudrun"


# ── _sqlite_url ───────────────────────────────────────────────────────────────


def test_sqlite_url_default() -> None:
    """SQLite URL points to local_dev.db by default."""
    with patch.dict(os.environ, {}, clear=True):
        url = _sqlite_url()
        assert url.startswith("sqlite+aiosqlite:///")
        assert "local_dev.db" in url


def test_sqlite_url_custom_path() -> None:
    """SQLite URL respects SQLITE_PATH override."""
    with patch.dict(os.environ, {"SQLITE_PATH": "/tmp/test.db"}, clear=True):
        url = _sqlite_url()
        assert "/tmp/test.db" in url


# ── _postgres_url ─────────────────────────────────────────────────────────────


def test_postgres_url_unix_socket() -> None:
    """Postgres URL uses Unix socket when CLOUD_SQL_INSTANCE_CONNECTION_NAME is set."""
    env = {
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "my-project:region:instance",
        "DB_NAME": "test_db",
        "DB_USER": "test_user",
    }
    with patch.dict(os.environ, env, clear=True):
        url = _postgres_url()
        assert "postgresql+asyncpg://" in url
        assert "test_user" in url
        assert "test_db" in url
        assert "/cloudsql/my-project:region:instance" in url


def test_postgres_url_tcp() -> None:
    """Postgres URL falls back to DATABASE_URL when no socket config."""
    env = {"DATABASE_URL": "postgresql://user:pass@localhost:5432/db"}
    with patch.dict(os.environ, env, clear=True):
        url = _postgres_url()
        assert "postgresql+asyncpg://" in url
        assert "user:pass@localhost:5432/db" in url


def test_postgres_url_tcp_with_postgres_prefix() -> None:
    """Postgres URL normalises postgres:// prefix."""
    env = {"DATABASE_URL": "postgres://user:pass@localhost:5432/db"}
    with patch.dict(os.environ, env, clear=True):
        url = _postgres_url()
        assert "postgresql+asyncpg://" in url


def test_postgres_url_missing_config() -> None:
    """Postgres URL raises RuntimeError when neither socket nor DATABASE_URL set."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="CLOUD_SQL_INSTANCE_CONNECTION_NAME"):
            _postgres_url()


# ── _engine_kwargs ────────────────────────────────────────────────────────────


def test_engine_kwargs_local() -> None:
    """Local engine kwargs include check_same_thread=False."""
    kwargs = _engine_kwargs("local")
    assert kwargs["connect_args"]["check_same_thread"] is False
    assert kwargs["echo"] is False


def test_engine_kwargs_local_echo() -> None:
    """Local engine kwargs respect SQL_ECHO=true."""
    with patch.dict(os.environ, {"SQL_ECHO": "true"}, clear=True):
        kwargs = _engine_kwargs("local")
        assert kwargs["echo"] is True


def test_engine_kwargs_cloudrun() -> None:
    """Cloud Run engine kwargs include pool settings."""
    kwargs = _engine_kwargs("cloudrun")
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 20
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800


# ── CloudSqlFactory singleton ─────────────────────────────────────────────────


def test_factory_reset() -> None:
    """Reset clears cached engine and session factory."""
    CloudSqlFactory._engine = "fake_engine"  # type: ignore[assignment]
    CloudSqlFactory._session_factory = "fake_session"  # type: ignore[assignment]
    CloudSqlFactory.reset()
    assert CloudSqlFactory._engine is None
    assert CloudSqlFactory._session_factory is None


def test_get_engine_creates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_engine creates engine on first call and returns cached instance."""
    CloudSqlFactory.reset()
    monkeypatch.setenv("ENV", "local")

    engine1 = CloudSqlFactory.get_engine()
    engine2 = CloudSqlFactory.get_engine()
    assert engine1 is engine2
    assert engine1.dialect.name == "sqlite"

    CloudSqlFactory.reset()


def test_get_session_factory_creates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_session_factory creates factory on first call and returns cached instance."""
    CloudSqlFactory.reset()
    monkeypatch.setenv("ENV", "local")

    factory1 = CloudSqlFactory.get_session_factory()
    factory2 = CloudSqlFactory.get_session_factory()
    assert factory1 is factory2

    CloudSqlFactory.reset()


# ── CloudSqlFactory.get_session dependency ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_yields_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_session yields a session and commits on success."""
    CloudSqlFactory.reset()
    monkeypatch.setenv("ENV", "local")

    session_gen = CloudSqlFactory.get_session()
    session = await session_gen.asend(None)
    assert session is not None
    # Clean up
    try:
        await session_gen.asend(None)
    except StopAsyncIteration:
        pass

    CloudSqlFactory.reset()


# ── CloudSqlFactory.init_db ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_db_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_db creates tables for local SQLite."""
    CloudSqlFactory.reset()
    monkeypatch.setenv("ENV", "local")

    await CloudSqlFactory.init_db()

    engine = CloudSqlFactory.get_engine()
    from sqlalchemy import text as sa_text
    async with engine.connect() as conn:
        result = await conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result.fetchall()}
        assert "audit_log" in tables
        assert "causal_entities" in tables
        assert "session_state" in tables

    CloudSqlFactory.reset()

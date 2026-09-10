"""
Unit tests for Neo4jFactory (src/db/neo4j_factory.py).

Tests driver creation, availability checks, and lifecycle with mocked neo4j.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.neo4j_factory import Neo4jFactory


@pytest.fixture(autouse=True)
def _reset_neo4j():
    """Reset Neo4jFactory singleton between tests."""
    Neo4jFactory.reset()
    yield
    Neo4jFactory.reset()


# ── get_driver ────────────────────────────────────────────────────────────────


def test_get_driver_not_configured() -> None:
    """get_driver returns None when NEO4J_URI is not set."""
    with patch.dict(os.environ, {}, clear=True):
        driver = Neo4jFactory.get_driver()
    assert driver is None
    assert Neo4jFactory._available is False


def test_get_driver_creates_singleton() -> None:
    """get_driver creates driver once and returns cached instance."""
    mock_driver = MagicMock()

    with patch.dict(os.environ, {"NEO4J_URI": "bolt://localhost:7687"}, clear=True), patch(
        "neo4j.AsyncGraphDatabase.driver",
        return_value=mock_driver,
    ):
        driver1 = Neo4jFactory.get_driver()
        driver2 = Neo4jFactory.get_driver()

    assert driver1 is mock_driver
    assert driver1 is driver2
    assert Neo4jFactory._available is True


def test_get_driver_creation_failure() -> None:
    """get_driver returns None when driver creation fails."""
    with patch.dict(os.environ, {"NEO4J_URI": "bolt://bad:7687"}, clear=True), patch(
        "neo4j.AsyncGraphDatabase.driver",
        side_effect=RuntimeError("connection refused"),
    ):
        driver = Neo4jFactory.get_driver()

    assert driver is None
    assert Neo4jFactory._available is False


def test_get_driver_returns_none_when_available_false() -> None:
    """get_driver returns None immediately when _available is False."""
    Neo4jFactory._available = False
    driver = Neo4jFactory.get_driver()
    assert driver is None


# ── verify_connectivity ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_connectivity_success() -> None:
    """verify_connectivity returns True when driver verifies successfully."""
    mock_driver = MagicMock()
    mock_driver.verify_connectivity = AsyncMock()

    Neo4jFactory._driver = mock_driver
    Neo4jFactory._available = True

    result = await Neo4jFactory.verify_connectivity()
    assert result is True


@pytest.mark.asyncio
async def test_verify_connectivity_no_driver() -> None:
    """verify_connectivity returns False when no driver is configured."""
    Neo4jFactory._available = False
    result = await Neo4jFactory.verify_connectivity()
    assert result is False


@pytest.mark.asyncio
async def test_verify_connectivity_failure() -> None:
    """verify_connectivity returns False when verification throws."""
    mock_driver = MagicMock()
    mock_driver.verify_connectivity = AsyncMock(side_effect=RuntimeError("timeout"))

    Neo4jFactory._driver = mock_driver
    Neo4jFactory._available = True

    result = await Neo4jFactory.verify_connectivity()
    assert result is False


# ── is_available ──────────────────────────────────────────────────────────────


def test_is_available_when_cached_true() -> None:
    """is_available returns cached True value."""
    Neo4jFactory._available = True
    assert Neo4jFactory.is_available() is True


def test_is_available_when_cached_false() -> None:
    """is_available returns cached False value."""
    Neo4jFactory._available = False
    assert Neo4jFactory.is_available() is False


def test_is_available_fallback_to_env() -> None:
    """is_available checks NEO4J_URI env when cache is None."""
    Neo4jFactory._available = None
    with patch.dict(os.environ, {"NEO4J_URI": "bolt://localhost:7687"}, clear=True):
        assert Neo4jFactory.is_available() is True


def test_is_available_fallback_no_env() -> None:
    """is_available returns False when no cache and no env var."""
    Neo4jFactory._available = None
    with patch.dict(os.environ, {}, clear=True):
        assert Neo4jFactory.is_available() is False


# ── reset ─────────────────────────────────────────────────────────────────────


def test_reset_clears_cache() -> None:
    """reset clears both driver and availability cache."""
    Neo4jFactory._driver = MagicMock()
    Neo4jFactory._available = True
    Neo4jFactory.reset()
    assert Neo4jFactory._driver is None
    assert Neo4jFactory._available is None


# ── close ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_driver() -> None:
    """close closes the driver and clears the cache."""
    mock_driver = MagicMock()
    mock_driver.close = AsyncMock()
    Neo4jFactory._driver = mock_driver

    await Neo4jFactory.close()
    mock_driver.close.assert_awaited_once()
    assert Neo4jFactory._driver is None


@pytest.mark.asyncio
async def test_close_no_driver() -> None:
    """close is a no-op when no driver exists."""
    Neo4jFactory._driver = None
    await Neo4jFactory.close()
    assert Neo4jFactory._driver is None

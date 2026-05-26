"""Tests for Neo4j-native experience buffer.

These tests validate:
1. Schema creation and constraints
2. Incident storage with TTL
3. Similar incident querying with relevance decay
4. Cleanup of expired incidents
5. Fallback to SQL when Neo4j unavailable
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.neo4j_factory import Neo4jFactory
from src.skills.experience_buffer_neo4j import (
    cleanup_expired_incidents,
    get_buffer_stats,
    query_similar_incidents,
    store_incident,
    _calculate_decay_factor,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_neo4j_driver():
    """Create a mock Neo4j driver for testing."""
    driver = MagicMock()
    session = AsyncMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Reset factory state before each test
    Neo4jFactory.reset()
    
    with patch.object(Neo4jFactory, '_driver', driver):
        with patch.object(Neo4jFactory, '_available', True):
            yield driver, session


@pytest.fixture
def mock_no_neo4j():
    """Simulate Neo4j not being configured."""
    Neo4jFactory.reset()
    with patch.object(Neo4jFactory, '_available', False):
        yield


# ── Decay Calculation Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio(False)
def test_decay_factor_fresh_incident():
    """Fresh incidents should have decay factor close to 1.0."""
    now = datetime.now(timezone.utc)
    decay = _calculate_decay_factor(now)
    assert decay == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio(False)
def test_decay_factor_old_incident():
    """Old incidents should have reduced decay factor."""
    old = datetime.now(timezone.utc) - timedelta(days=30)  # One half-life
    decay = _calculate_decay_factor(old)
    assert decay == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio(False)
def test_decay_factor_very_old_incident():
    """Very old incidents should have minimum decay factor."""
    very_old = datetime.now(timezone.utc) - timedelta(days=120)
    decay = _calculate_decay_factor(very_old)
    assert decay >= 0.1  # Minimum threshold
    assert decay < 0.2


# ── Storage Tests ────────────────────────────────────────────────────────────


async def test_store_incident_neo4j_unavailable(mock_no_neo4j):
    """When Neo4j is unavailable, storage should return appropriate status."""
    result = await store_incident(
        incident_id="test-123",
        description="Test incident",
        domain="devops",
        severity="P2",
        services=[],
        metrics=[],
        resolution=None,
        cynefin_domain=None,
        guardian_verdict=None,
    )
    
    assert result["stored"] is False
    assert result["reason"] == "neo4j_not_available"


async def test_store_incident_success(mock_neo4j_driver):
    """Successful incident storage should return stored=True."""
    driver, session = mock_neo4j_driver
    
    # Mock successful record return
    mock_record = MagicMock()
    mock_record.__getitem__ = MagicMock(return_value={
        "id": "test-123",
        "description": "Test incident",
    })
    
    result_mock = AsyncMock()
    result_mock.single = AsyncMock(return_value=mock_record)
    session.run = AsyncMock(return_value=result_mock)
    
    result = await store_incident(
        incident_id="test-123",
        description="Test incident",
        domain="devops",
        severity="P1",
        services=["api-gateway"],
        metrics=[{"name": "latency", "pattern": "spike"}],
        resolution="Rollback deployment",
        cynefin_domain="COMPLICATED",
        guardian_verdict="APPROVED",
    )
    
    assert result["stored"] is True
    assert result["incident_id"] == "test-123"
    assert "ttl_at" in result


# ── Query Tests ─────────────────────────────────────────────────────────────


async def test_query_similar_neo4j_unavailable(mock_no_neo4j):
    """When Neo4j unavailable, query should return empty results with proper metadata."""
    result = await query_similar_incidents(
        description="high latency on api",
        domain="devops",
    )
    
    assert result["similar_incidents"] == []
    assert result["retrieval_source"] == "neo4j_not_available"
    assert result["candidate_count"] == 0


async def test_query_similar_no_matches(mock_neo4j_driver):
    """Query with no matching incidents should return empty list."""
    driver, session = mock_neo4j_driver
    
    # Mock empty result
    result_mock = AsyncMock()
    result_mock.data = AsyncMock(return_value=[])
    session.run = AsyncMock(return_value=result_mock)
    
    result = await query_similar_incidents(
        description="completely unique description xyz123",
        domain="devops",
    )
    
    assert result["similar_incidents"] == []
    assert result["retrieval_source"] == "neo4j_experience_buffer"


async def test_query_similar_with_results(mock_neo4j_driver):
    """Query with matching incidents should return ranked results."""
    driver, session = mock_neo4j_driver
    
    # Mock matching records
    now = datetime.now(timezone.utc).isoformat()
    mock_records = [
        {
            "id": "incident-1",
            "description": "high latency api gateway timeout",
            "domain": "devops",
            "cynefin_domain": "COMPLICATED",
            "resolution": "Scaled up instances",
            "created_at": now,
            "guardian_verdict": "APPROVED",
            "severity": "P2",
        }
    ]
    
    result_mock = AsyncMock()
    result_mock.data = AsyncMock(return_value=mock_records)
    session.run = AsyncMock(return_value=result_mock)
    
    result = await query_similar_incidents(
        description="high latency api gateway",
        domain="devops",
        limit=5,
    )
    
    assert len(result["similar_incidents"]) == 1
    assert result["similar_incidents"][0]["session_id"] == "incident-1"
    assert result["similar_incidents"][0]["source"] == "neo4j_experience_buffer"
    assert "decay_factor" in result["similar_incidents"][0]
    assert result["similar_incidents"][0]["similarity"] > 0


async def test_query_similar_applies_decay(mock_neo4j_driver):
    """Older incidents should have reduced similarity due to decay."""
    driver, session = mock_neo4j_driver
    
    # Create old and new records with same description
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=60)).isoformat()  # 2 half-lives
    new_time = now.isoformat()
    
    mock_records = [
        {
            "id": "old-incident",
            "description": "timeout latency degradation",
            "domain": "devops",
            "cynefin_domain": "COMPLICATED",
            "resolution": "Restarted service",
            "created_at": old_time,
            "guardian_verdict": "APPROVED",
            "severity": "P2",
        },
        {
            "id": "new-incident",
            "description": "timeout latency degradation",
            "domain": "devops",
            "cynefin_domain": "COMPLICATED",
            "resolution": "Scaled horizontally",
            "created_at": new_time,
            "guardian_verdict": "APPROVED",
            "severity": "P2",
        }
    ]
    
    result_mock = AsyncMock()
    result_mock.data = AsyncMock(return_value=mock_records)
    session.run = AsyncMock(return_value=result_mock)
    
    result = await query_similar_incidents(
        description="timeout latency degradation",
        domain="devops",
    )
    
    assert len(result["similar_incidents"]) == 2
    
    # New incident should have higher similarity than old
    new_sim = next(r for r in result["similar_incidents"] if r["session_id"] == "new-incident")["similarity"]
    old_sim = next(r for r in result["similar_incidents"] if r["session_id"] == "old-incident")["similarity"]
    
    assert new_sim > old_sim


# ── Cleanup Tests ────────────────────────────────────────────────────────────


async def test_cleanup_expired_incidents(mock_neo4j_driver):
    """Cleanup should remove expired incidents."""
    driver, session = mock_neo4j_driver
    
    # Mock count result
    count_mock = AsyncMock()
    count_record = MagicMock()
    count_record.__getitem__ = MagicMock(return_value=5)
    count_mock.single = AsyncMock(return_value=count_record)
    
    # Mock delete result
    delete_mock = AsyncMock()
    
    session.run = AsyncMock(side_effect=[count_mock, delete_mock])
    
    result = await cleanup_expired_incidents()
    
    assert result["cleaned"] is True
    assert result["removed_count"] == 5


async def test_cleanup_no_expired(mock_neo4j_driver):
    """Cleanup with no expired incidents should report 0 removed."""
    driver, session = mock_neo4j_driver
    
    count_mock = AsyncMock()
    count_record = MagicMock()
    count_record.__getitem__ = MagicMock(return_value=0)
    count_mock.single = AsyncMock(return_value=count_record)
    
    session.run = AsyncMock(return_value=count_mock)
    
    result = await cleanup_expired_incidents()
    
    assert result["cleaned"] is True
    assert result["removed_count"] == 0


# ── Stats Tests ──────────────────────────────────────────────────────────────


async def test_get_buffer_stats_neo4j_unavailable(mock_no_neo4j):
    """Stats should indicate unavailable when Neo4j not configured."""
    result = await get_buffer_stats()
    
    assert result["available"] is False
    assert result["reason"] == "neo4j_not_available"


# ── Integration-style Tests ─────────────────────────────────────────────────


async def test_domain_filtering(mock_neo4j_driver):
    """Query with domain filter should only return matching incidents."""
    driver, session = mock_neo4j_driver
    
    now = datetime.now(timezone.utc).isoformat()
    mock_records = [
        {
            "id": "devops-1",
            "description": "deployment failed",
            "domain": "devops",
            "cynefin_domain": "COMPLICATED",
            "resolution": "Fixed",
            "created_at": now,
            "guardian_verdict": "APPROVED",
            "severity": "P2",
        }
    ]
    
    result_mock = AsyncMock()
    result_mock.data = AsyncMock(return_value=mock_records)
    session.run = AsyncMock(return_value=result_mock)
    
    result = await query_similar_incidents(
        description="deployment",
        domain="devops",
    )
    
    # Verify domain filter was applied in Cypher query
    call_args = session.run.call_args
    assert "domain" in str(call_args)
    
    assert len(result["similar_incidents"]) == 1
    assert result["similar_incidents"][0]["cynefin_domain"] == "COMPLICATED"

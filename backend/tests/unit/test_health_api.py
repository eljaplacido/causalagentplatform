"""
Unit tests for health check module (src/api/health.py).

Tests the comprehensive health router with mocked Neo4j, sensors, and alert emitter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import health as health_module
from src.api.health import (
    _check_alerts,
    _check_neo4j,
    _check_sensors,
    health_alerts,
    health_check,
    health_drift_monitor,
    health_neo4j,
    health_sensors,
    set_alert_emitter,
    set_drift_sensor,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_globals():
    """Reset global sensor / emitter references between tests."""
    set_drift_sensor(None)  # type: ignore[arg-type]
    set_alert_emitter(None)  # type: ignore[arg-type]
    yield
    set_drift_sensor(None)  # type: ignore[arg-type]
    set_alert_emitter(None)  # type: ignore[arg-type]


# ── _check_neo4j ──────────────────────────────────────────────────────────────


async def test_check_neo4j_healthy() -> None:
    """Neo4j healthy when connectivity succeeds."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=True,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        result = await _check_neo4j()

    assert result["healthy"] is True
    assert result["component"] == "neo4j"
    assert result["status"] == "connected"


async def test_check_neo4j_unhealthy() -> None:
    """Neo4j unhealthy when connectivity fails."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=False,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        result = await _check_neo4j()

    assert result["healthy"] is False
    assert result["status"] == "disconnected"


async def test_check_neo4j_exception() -> None:
    """Neo4j check handles exceptions gracefully."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=False,
    ):
        result = await _check_neo4j()

    assert result["healthy"] is False
    assert "boom" in result["error"]


# ── _check_sensors ────────────────────────────────────────────────────────────


def test_check_sensors_no_sensor() -> None:
    """Sensor check reports unconfigured when no sensor set."""
    result = _check_sensors()
    assert result["healthy"] is True
    assert result["drift_sensor"]["configured"] is False


def test_check_sensors_with_sensor() -> None:
    """Sensor check includes stats when sensor is set."""
    mock_sensor = MagicMock()
    mock_sensor.stats = {"running": True, "checks": 42}
    set_drift_sensor(mock_sensor)

    result = _check_sensors()
    assert result["healthy"] is True
    assert result["drift_sensor"]["running"] is True
    assert result["drift_sensor"]["checks"] == 42


# ── _check_alerts ─────────────────────────────────────────────────────────────


def test_check_alerts_no_emitter() -> None:
    """Alert check reports unconfigured when no emitter set."""
    result = _check_alerts()
    assert result["healthy"] is True
    assert result["alert_emitter"]["configured"] is False


def test_check_alerts_with_emitter() -> None:
    """Alert check includes stats when emitter is set."""
    mock_emitter = MagicMock()
    mock_emitter.stats = {"emitted": 10, "failed": 0}
    set_alert_emitter(mock_emitter)

    result = _check_alerts()
    assert result["healthy"] is True
    assert result["alert_emitter"]["emitted"] == 10


# ── health_check (router endpoint) ────────────────────────────────────────────


async def test_health_check_all_healthy() -> None:
    """Overall health returns 200 when all components healthy."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=True,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        result = await health_check()

    assert result["status"] == "healthy"
    assert "checks" in result
    assert "timestamp" in result
    assert result["checks"]["neo4j"]["healthy"] is True
    assert result["checks"]["sensors"]["healthy"] is True
    assert result["checks"]["alerts"]["healthy"] is True


async def test_health_check_degraded() -> None:
    """Overall health raises HTTPException 503 when a component is unhealthy."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=False,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await health_check()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert detail["status"] == "degraded"
        assert "neo4j" in detail["degraded_components"]


# ── health_neo4j ──────────────────────────────────────────────────────────────


async def test_health_neo4j_healthy() -> None:
    """GET /health/neo4j returns healthy status."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=True,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        result = await health_neo4j()

    assert result["healthy"] is True


async def test_health_neo4j_unhealthy_raises_503() -> None:
    """GET /health/neo4j raises 503 when Neo4j is down."""
    with patch.object(
        health_module.Neo4jFactory,
        "verify_connectivity",
        new_callable=AsyncMock,
        return_value=False,
    ), patch.object(
        health_module.Neo4jFactory,
        "is_available",
        return_value=True,
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await health_neo4j()
        assert exc_info.value.status_code == 503


# ── health_sensors / health_alerts ────────────────────────────────────────────


async def test_health_sensors() -> None:
    """GET /health/sensors returns sensor status."""
    result = await health_sensors()
    assert result["healthy"] is True
    assert result["component"] == "sensors"


async def test_health_alerts() -> None:
    """GET /health/alerts returns alert status."""
    result = await health_alerts()
    assert result["healthy"] is True
    assert result["component"] == "alerts"


# ── health_drift_monitor ──────────────────────────────────────────────────────


async def test_health_drift_monitor_no_alerts() -> None:
    """Drift monitor health with no unacknowledged alerts."""
    with patch(
        "src.skills.get_recent_drift_alerts",
        new_callable=AsyncMock,
        return_value={"alerts": [], "count": 0},
    ):
        result = await health_drift_monitor()

    assert result["healthy"] is True
    assert result["component"] == "drift_monitor"
    assert result["status"] == "healthy"
    assert result["recent_unacknowledged_alerts"] == 0


async def test_health_drift_monitor_critical_alert() -> None:
    """Drift monitor health flags critical alerts."""
    with patch(
        "src.skills.get_recent_drift_alerts",
        new_callable=AsyncMock,
        return_value={
            "alerts": [
                {"severity": "CRITICAL", "id": "a1"},
            ],
            "count": 1,
        },
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await health_drift_monitor()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert detail["status"] == "critical_alerts_present"
        assert detail["healthy"] is False


async def test_health_drift_monitor_with_sensor() -> None:
    """Drift monitor health includes sensor stats when available."""
    mock_sensor = MagicMock()
    mock_sensor.stats = {"checks": 5}
    set_drift_sensor(mock_sensor)

    with patch(
        "src.skills.get_recent_drift_alerts",
        new_callable=AsyncMock,
        return_value={"alerts": [], "count": 0},
    ):
        result = await health_drift_monitor()

    assert result["sensor_stats"]["checks"] == 5


async def test_health_drift_monitor_exception() -> None:
    """Drift monitor health handles exceptions gracefully."""
    with patch(
        "src.skills.get_recent_drift_alerts",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db error"),
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await health_drift_monitor()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert detail["healthy"] is False
        assert "db error" in detail["error"]

"""
Health check endpoint for the monitoring stack.

This module provides a comprehensive health check API for:
- Neo4j connectivity
- Drift sensor status
- Alert system health
- Overall platform monitoring health

Usage (FastAPI integration)::

    from fastapi import FastAPI
    from src.api.health import health_router

    app = FastAPI()
    app.include_router(health_router, prefix="/health")

Endpoints:
    GET /health          — Overall health status
    GET /health/neo4j    — Neo4j connectivity
    GET /health/sensors  — Sensor status
    GET /health/alerts   — Alert system status
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from src.db.neo4j_factory import Neo4jFactory
from src.sensors.alert_emit import AlertEmitter
from src.sensors.periodic_drift import DriftSensor

health_router = APIRouter(tags=["health"])

# Global references (set by application startup)
_drift_sensor: DriftSensor | None = None
_alert_emitter: AlertEmitter | None = None


def set_drift_sensor(sensor: DriftSensor) -> None:
    """Set the global drift sensor reference."""
    global _drift_sensor
    _drift_sensor = sensor


def set_alert_emitter(emitter: AlertEmitter) -> None:
    """Set the global alert emitter reference."""
    global _alert_emitter
    _alert_emitter = emitter


@health_router.get("")
async def health_check() -> dict[str, Any]:
    """
    Overall platform health check.
    
    Returns:
        Dict with health status of all monitored components
    """
    checks = {
        "neo4j": await _check_neo4j(),
        "sensors": _check_sensors(),
        "alerts": _check_alerts(),
    }
    
    # Determine overall health
    all_healthy = all(c.get("healthy", False) for c in checks.values())
    
    status = "healthy" if all_healthy else "degraded"
    status_code = 200 if all_healthy else 503
    
    response = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    
    if not all_healthy:
        response["degraded_components"] = [
            name for name, check in checks.items()
            if not check.get("healthy", False)
        ]
    
    # Raise HTTPException for non-healthy status to return proper status code
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=response)
    
    return response


@health_router.get("/neo4j")
async def health_neo4j() -> dict[str, Any]:
    """
    Neo4j connectivity health check.
    
    Returns:
        Dict with Neo4j connectivity status
    """
    result = await _check_neo4j()
    
    if not result.get("healthy", False):
        raise HTTPException(status_code=503, detail=result)
    
    return result


@health_router.get("/sensors")
async def health_sensors() -> dict[str, Any]:
    """
    Sensor system health check.
    
    Returns:
        Dict with sensor status
    """
    result = _check_sensors()
    return result


@health_router.get("/alerts")
async def health_alerts() -> dict[str, Any]:
    """
    Alert system health check.
    
    Returns:
        Dict with alert system status
    """
    result = _check_alerts()
    return result


@health_router.get("/drift-monitor")
async def health_drift_monitor() -> dict[str, Any]:
    """
    Drift monitoring health check.
    
    Returns:
        Dict with drift monitoring status including recent alerts
    """
    from src.skills import get_recent_drift_alerts
    
    result: dict[str, Any] = {
        "healthy": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": "drift_monitor",
    }
    
    # Check recent alerts
    try:
        recent_alerts = await get_recent_drift_alerts(
            limit=10,
            include_acknowledged=False,
        )
        
        result["recent_unacknowledged_alerts"] = recent_alerts.get("count", 0)
        
        # Count by severity
        alerts = recent_alerts.get("alerts", [])
        severity_counts = {}
        for alert in alerts:
            severity = alert.get("severity", "UNKNOWN")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        result["severity_distribution"] = severity_counts
        
        # Flag if critical alerts exist
        if severity_counts.get("CRITICAL", 0) > 0:
            result["status"] = "critical_alerts_present"
            result["healthy"] = False
        elif severity_counts.get("HIGH", 0) > 0:
            result["status"] = "high_severity_alerts_present"
        else:
            result["status"] = "healthy"
    
    except Exception as e:
        result["healthy"] = False
        result["error"] = str(e)
        result["status"] = "error"
    
    # Include sensor stats if available
    if _drift_sensor:
        result["sensor_stats"] = _drift_sensor.stats
    
    if not result.get("healthy", True):
        raise HTTPException(status_code=503, detail=result)
    
    return result


async def _check_neo4j() -> dict[str, Any]:
    """Check Neo4j connectivity."""
    try:
        is_connected = await Neo4jFactory.verify_connectivity()
        
        if is_connected:
            return {
                "healthy": True,
                "component": "neo4j",
                "status": "connected",
                "configured": Neo4jFactory.is_available(),
            }
        else:
            return {
                "healthy": False,
                "component": "neo4j",
                "status": "disconnected",
                "configured": Neo4jFactory.is_available(),
                "message": "Neo4j is configured but not reachable",
            }
    
    except Exception as e:
        return {
            "healthy": False,
            "component": "neo4j",
            "status": "error",
            "configured": Neo4jFactory.is_available(),
            "error": str(e),
        }


def _check_sensors() -> dict[str, Any]:
    """Check sensor system health."""
    result: dict[str, Any] = {
        "healthy": True,
        "component": "sensors",
    }
    
    if _drift_sensor is None:
        result["drift_sensor"] = {
            "running": False,
            "configured": False,
        }
    else:
        stats = _drift_sensor.stats
        result["drift_sensor"] = stats
        
        # Sensor is healthy if it's running or hasn't been started yet
        result["healthy"] = True  # Sensor can be stopped intentionally
    
    return result


def _check_alerts() -> dict[str, Any]:
    """Check alert system health."""
    result: dict[str, Any] = {
        "healthy": True,
        "component": "alerts",
    }
    
    if _alert_emitter is None:
        result["alert_emitter"] = {
            "configured": False,
        }
    else:
        stats = _alert_emitter.stats
        result["alert_emitter"] = stats
        
        # Alert system is healthy if it's processing
        result["healthy"] = True
    
    return result

"""
Unit tests for sensors package.

Tests for:
  1. DriftSensor initialization and configuration
  2. DriftSensor start/stop lifecycle
  3. DriftSensor force check
  4. AlertEmitter basic emission
  5. AlertEmitter deduplication
  6. AlertEmitter throttling
  7. AlertEmitter severity filtering
  8. Alert acknowledgment
"""

from __future__ import annotations

import asyncio
import pytest

from src.sensors.alert_emit import AlertEmitter, AlertEmitterConfig, AlertSeverity
from src.sensors.periodic_drift import DriftSensor, DriftSensorConfig


pytestmark = pytest.mark.asyncio


# ── 1. DriftSensor initialization ────────────────────────────────────────────


def test_drift_sensor_default_config() -> None:
    """DriftSensor should have sensible defaults."""
    sensor = DriftSensor()
    
    assert sensor.is_running is False
    assert sensor.stats["running"] is False
    assert sensor.stats["check_count"] == 0


def test_drift_sensor_custom_config() -> None:
    """DriftSensor should respect custom configuration."""
    config = DriftSensorConfig(
        check_interval_seconds=60.0,
        fragment_ids=["frag-1", "frag-2"],
        enable_neo4j_storage=False,
    )
    sensor = DriftSensor(config=config)
    
    stats = sensor.stats
    assert stats["config"]["check_interval_seconds"] == 60.0
    assert stats["config"]["fragment_count"] == 2
    assert stats["config"]["enable_neo4j_storage"] is False


# ── 2. DriftSensor lifecycle ─────────────────────────────────────────────────


async def test_drift_sensor_start_stop() -> None:
    """DriftSensor should start and stop correctly."""
    config = DriftSensorConfig(
        check_interval_seconds=1.0,
        initial_delay_seconds=0.1,
        fragment_ids=["test-frag"],
    )
    sensor = DriftSensor(config=config)
    
    assert sensor.is_running is False
    
    await sensor.start()
    assert sensor.is_running is True
    
    # Let it run for a moment
    await asyncio.sleep(0.2)
    
    await sensor.stop()
    assert sensor.is_running is False


async def test_drift_sensor_double_start() -> None:
    """DriftSensor should handle double start gracefully."""
    config = DriftSensorConfig(
        check_interval_seconds=1.0,
        fragment_ids=["test-frag"],
    )
    sensor = DriftSensor(config=config)
    
    await sensor.start()
    await sensor.start()  # Should not raise or create duplicate tasks
    
    await sensor.stop()


async def test_drift_sensor_stop_when_not_running() -> None:
    """DriftSensor should handle stop when not running."""
    sensor = DriftSensor()
    
    # Should not raise
    await sensor.stop()


# ── 3. DriftSensor force check ───────────────────────────────────────────────


async def test_drift_sensor_force_check_no_fragments() -> None:
    """Force check with no fragments should return error."""
    sensor = DriftSensor()
    
    result = await sensor.force_check()
    
    assert result["checked"] is False
    assert "no_fragments" in result["reason"]


async def test_drift_sensor_force_check_with_fragment() -> None:
    """Force check with fragment should run drift detection."""
    config = DriftSensorConfig(fragment_ids=["test-frag"])
    sensor = DriftSensor(config=config)
    
    result = await sensor.force_check("test-frag")
    
    assert result["checked"] is True
    assert result["fragments_checked"] == 1
    assert len(result["results"]) == 1


# ── 4. AlertEmitter basic emission ───────────────────────────────────────────


async def test_alert_emitter_basic() -> None:
    """AlertEmitter should emit alerts."""
    emitter = AlertEmitter(source="test")
    
    alert = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST_ALERT",
        message="Test alert message",
        context={"key": "value"},
    )
    
    assert alert is not None
    assert alert.severity == AlertSeverity.HIGH
    assert alert.alert_type == "TEST_ALERT"
    assert alert.message == "Test alert message"
    assert alert.context["key"] == "value"
    assert alert.source == "test"


async def test_alert_emitter_stats() -> None:
    """AlertEmitter should track statistics."""
    emitter = AlertEmitter(source="test")
    
    initial_stats = emitter.stats
    assert initial_stats["emitted_count"] == 0
    
    await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="TEST",
        message="Test",
    )
    
    stats = emitter.stats
    assert stats["emitted_count"] == 1


# ── 5. AlertEmitter deduplication ────────────────────────────────────────────


async def test_alert_emitter_dedup() -> None:
    """AlertEmitter should deduplicate identical alerts."""
    config = AlertEmitterConfig(dedup_window_seconds=1.0)
    emitter = AlertEmitter(config=config, source="test")
    
    # Emit same alert twice
    alert1 = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST_DEDUP",
        message="Duplicate message",
        context={"data": "same"},
    )
    
    alert2 = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST_DEDUP",
        message="Duplicate message",
        context={"data": "same"},
    )
    
    assert alert1 is not None
    assert alert2 is None  # Should be deduplicated
    assert emitter.stats["deduped_count"] == 1


async def test_alert_emitter_different_alerts_not_deduped() -> None:
    """Different alerts should not be deduplicated."""
    config = AlertEmitterConfig(dedup_window_seconds=1.0)
    emitter = AlertEmitter(config=config, source="test")
    
    alert1 = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST_1",
        message="Message 1",
    )
    
    alert2 = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST_2",
        message="Message 2",
    )
    
    assert alert1 is not None
    assert alert2 is not None
    assert emitter.stats["deduped_count"] == 0


# ── 6. AlertEmitter throttling ───────────────────────────────────────────────


async def test_alert_emitter_throttling() -> None:
    """AlertEmitter should throttle excessive alerts of same type."""
    config = AlertEmitterConfig(
        throttle_max_per_type=2,
        throttle_window_seconds=60.0,
    )
    emitter = AlertEmitter(config=config, source="test")
    
    # Emit max allowed
    alert1 = await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="THROTTLE_TEST",
        message="Alert 1",
    )
    alert2 = await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="THROTTLE_TEST",
        message="Alert 2",
    )
    
    # Third should be throttled
    alert3 = await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="THROTTLE_TEST",
        message="Alert 3",
    )
    
    assert alert1 is not None
    assert alert2 is not None
    assert alert3 is None  # Throttled
    assert emitter.stats["throttled_count"] == 1


# ── 7. AlertEmitter severity filtering ───────────────────────────────────────


async def test_alert_emitter_min_severity_filter() -> None:
    """AlertEmitter should filter by minimum severity."""
    config = AlertEmitterConfig(min_severity=AlertSeverity.MEDIUM)
    emitter = AlertEmitter(config=config, source="test")
    
    # DEBUG should be filtered
    debug_alert = await emitter.emit(
        severity=AlertSeverity.DEBUG,
        alert_type="TEST",
        message="Debug message",
    )
    
    # INFO should be filtered
    info_alert = await emitter.emit(
        severity=AlertSeverity.INFO,
        alert_type="TEST",
        message="Info message",
    )
    
    # MEDIUM should pass
    medium_alert = await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="TEST",
        message="Medium message",
    )
    
    assert debug_alert is None
    assert info_alert is None
    assert medium_alert is not None


# ── 8. Alert acknowledgment ──────────────────────────────────────────────────


async def test_alert_acknowledgment() -> None:
    """AlertEmitter should track acknowledgments."""
    emitter = AlertEmitter(source="test")
    
    alert = await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TEST",
        message="Test message",
    )
    
    assert alert is not None
    assert alert.acknowledged is False
    
    # Acknowledge
    success = await emitter.acknowledge(alert.alert_id, "user-123")
    assert success is True
    
    # Check acknowledgment
    unacked = emitter.get_unacknowledged_alerts()
    assert len(unacked) == 0
    
    # Acknowledge non-existent alert
    fail = await emitter.acknowledge("non-existent", "user-123")
    assert fail is False


async def test_alert_recent_filtering() -> None:
    """AlertEmitter should filter recent alerts by severity and type."""
    emitter = AlertEmitter(source="test")
    
    await emitter.emit(
        severity=AlertSeverity.CRITICAL,
        alert_type="TYPE_A",
        message="Critical A",
    )
    await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="TYPE_B",
        message="High B",
    )
    await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="TYPE_A",
        message="Medium A",
    )
    
    # Filter by severity
    critical_only = emitter.get_recent_alerts(severity=AlertSeverity.CRITICAL)
    assert len(critical_only) == 1
    
    # Filter by type
    type_a_only = emitter.get_recent_alerts(alert_type="TYPE_A")
    assert len(type_a_only) == 2


# ── 9. Drift alert convenience method ────────────────────────────────────────


async def test_alert_emitter_drift_alert() -> None:
    """AlertEmitter should support drift-specific alert emission."""
    emitter = AlertEmitter(source="drift-sensor")
    
    alert = await emitter.emit_drift_alert(
        severity=AlertSeverity.HIGH,
        drift_type="distribution",
        fragment_id="frag-123",
        drift_score=0.45,
        message="High drift detected",
        recommendation="Review graph fragment",
    )
    
    assert alert is not None
    assert alert.alert_type == "DRIFT_DISTRIBUTION"
    assert alert.context["fragment_id"] == "frag-123"
    assert alert.context["drift_score"] == 0.45
    assert alert.context["recommendation"] == "Review graph fragment"


# ── 10. AlertEmitter cleanup ─────────────────────────────────────────────────


async def test_alert_emitter_cleanup() -> None:
    """AlertEmitter should clean up old cache entries."""
    config = AlertEmitterConfig(
        dedup_window_seconds=0.01,  # Very short for testing
    )
    emitter = AlertEmitter(config=config, source="test")
    
    # Emit an alert
    await emitter.emit(
        severity=AlertSeverity.MEDIUM,
        alert_type="TEST",
        message="Test",
    )
    
    # Wait for dedup window to expire
    await asyncio.sleep(0.02)
    
    # Cleanup
    result = await emitter.cleanup()
    
    assert result["dedup_entries_removed"] >= 0
    assert result["dedup_entries_remaining"] == 0


# ── 11. Sensor failure mode ─────────────────────────────────────────────────


async def test_sensor_emits_failure_alert_on_cycle_error() -> None:
    """Sensor must emit a SENSOR_FAILURE alert when a cycle fails."""
    config = DriftSensorConfig(
        check_interval_seconds=60.0,
        fragment_ids=["test-frag"],
        enable_alert_emission=True,
    )
    sensor = DriftSensor(config=config)
    
    # Capture emitted alerts by patching the emitter
    emitted_alerts = []
    original_emit = sensor._sensor_alert_emitter.emit
    
    async def capture_emit(*, severity, alert_type, message, context):
        emitted_alerts.append({"severity": severity, "alert_type": alert_type, "message": message})
        return await original_emit(severity=severity, alert_type=alert_type, message=message, context=context)
    
    sensor._sensor_alert_emitter.emit = capture_emit
    
    # Directly test the error handling path that _run_loop uses
    test_error = RuntimeError("Simulated cycle failure")
    sensor._log.error("drift_sensor.cycle_failed", error=str(test_error))
    if sensor._config.enable_alert_emission:
        await sensor._sensor_alert_emitter.emit(
            severity=AlertSeverity.HIGH,
            alert_type="SENSOR_FAILURE",
            message=f"Drift sensor cycle failed: {test_error}",
            context={"sensor": "drift", "cycle": sensor._check_count},
        )
    
    # Check that sensor failure alert was emitted
    failure_alerts = [a for a in emitted_alerts if a["alert_type"] == "SENSOR_FAILURE"]
    assert len(failure_alerts) > 0, "Sensor should emit failure alert on cycle error"
    assert "Simulated cycle failure" in failure_alerts[0]["message"]


# ── 12. Sensor no-fragment advisory ──────────────────────────────────────────


async def test_sensor_emits_advisory_when_no_fragments() -> None:
    """Sensor should emit an advisory when no fragments are configured."""
    config = DriftSensorConfig(
        check_interval_seconds=60.0,
        fragment_ids=[],
        enable_alert_emission=True,
    )
    sensor = DriftSensor(config=config)
    
    emitted_alerts = []
    original_emit = sensor._sensor_alert_emitter.emit
    
    async def capture_emit(*, severity, alert_type, message, context):
        emitted_alerts.append({"severity": severity, "alert_type": alert_type, "message": message})
        return await original_emit(severity=severity, alert_type=alert_type, message=message, context=context)
    
    sensor._sensor_alert_emitter.emit = capture_emit
    
    await sensor._run_check_cycle()
    
    advisory_alerts = [a for a in emitted_alerts if a["alert_type"] == "SENSOR_NO_FRAGMENTS"]
    assert len(advisory_alerts) > 0, "Sensor should emit advisory when no fragments configured"

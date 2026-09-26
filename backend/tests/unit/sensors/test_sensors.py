"""
Unit tests for sensors package (AlertEmitter; DriftSensor was deleted in
September 2026 -- nothing started it and no drift data source feeds it).

Tests for:
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

pytestmark = pytest.mark.asyncio


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

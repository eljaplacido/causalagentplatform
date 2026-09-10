"""
Alert Emitter — structured alert emission and management.

This module provides production-ready alert emission capabilities:
- Structured alert formatting
- Multiple severity levels
- Integration with logging, callbacks, and external systems
- Alert deduplication and throttling

EpistemicState constraints enforced:
- audit_log_required: All alerts logged with full context
- no_semantic_layer_writes: Alerts are audit-only

Usage::

    emitter = AlertEmitter()
    await emitter.emit(
        severity=AlertSeverity.HIGH,
        alert_type="DISTRIBUTION_DRIFT",
        message="High drift detected in fragment-X",
        context={"kl_divergence": 0.35, "fragment_id": "fragment-X"},
    )
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class AlertSeverity(StrEnum):
    """Alert severity levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Alert:
    """Structured alert data."""
    alert_id: str
    timestamp: datetime
    severity: AlertSeverity
    alert_type: str
    message: str
    source: str
    context: dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
            "alert_type": self.alert_type,
            "message": self.message,
            "source": self.source,
            "context": self.context,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
        }


@dataclass
class AlertEmitterConfig:
    """Configuration for the alert emitter."""

    # Deduplication window (seconds)
    dedup_window_seconds: float = 300.0  # 5 minutes

    # Throttling: max alerts per type per window
    throttle_max_per_type: int = 10
    throttle_window_seconds: float = 3600.0  # 1 hour

    # Minimum severity to emit (lower severities are logged but not emitted)
    min_severity: AlertSeverity = AlertSeverity.INFO

    # Callbacks for different severity levels
    on_critical: Callable[[Alert], None] | None = None
    on_high: Callable[[Alert], None] | None = None
    on_medium: Callable[[Alert], None] | None = None

    # Include full context in logs
    log_full_context: bool = True


class AlertEmitter:
    """
    Structured alert emission system.
    
    Provides:
    - Structured alert formatting
    - Deduplication (don't emit same alert repeatedly)
    - Throttling (limit alerts per type)
    - Severity-based routing
    - Acknowledgment tracking
    
    Args:
        config: AlertEmitterConfig instance
        source: Source identifier for all alerts from this emitter
    """

    def __init__(
        self,
        config: AlertEmitterConfig | None = None,
        source: str = "platform",
    ):
        self._config = config or AlertEmitterConfig()
        self._source = source
        self._log = log.bind(emitter=source)

        # Deduplication tracking: alert_hash -> last_emitted_time
        self._dedup_cache: dict[str, datetime] = {}

        # Throttling tracking: alert_type -> list of timestamps
        self._throttle_cache: dict[str, list[datetime]] = {}

        # Alert history (last 1000 alerts)
        self._alert_history: list[Alert] = []
        self._max_history = 1000

        # Statistics
        self._emitted_count = 0
        self._deduped_count = 0
        self._throttled_count = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Get emitter statistics."""
        return {
            "emitted_count": self._emitted_count,
            "deduped_count": self._deduped_count,
            "throttled_count": self._throttled_count,
            "history_size": len(self._alert_history),
            "dedup_cache_size": len(self._dedup_cache),
        }

    async def emit(
        self,
        severity: AlertSeverity,
        alert_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> Alert | None:
        """
        Emit an alert.
        
        Args:
            severity: Alert severity level
            alert_type: Type/category of alert
            message: Human-readable alert message
            context: Additional context data
        
        Returns:
            Alert object if emitted, None if deduped/throttled/filtered
        """
        now = datetime.now(UTC)
        ctx = context or {}

        # Check minimum severity filter
        severity_levels = [
            AlertSeverity.DEBUG,
            AlertSeverity.INFO,
            AlertSeverity.LOW,
            AlertSeverity.MEDIUM,
            AlertSeverity.HIGH,
            AlertSeverity.CRITICAL,
        ]

        if severity_levels.index(severity) < severity_levels.index(self._config.min_severity):
            # Below minimum severity - log but don't emit
            self._log.debug(
                "alert.filtered",
                severity=severity.value,
                alert_type=alert_type,
                message=message,
                reason="below_min_severity",
            )
            return None

        # Generate alert ID
        alert_hash = self._hash_alert(alert_type, message, ctx)
        alert_id = f"{self._source}-{alert_hash[:16]}-{int(now.timestamp())}"

        # Check deduplication
        if self._is_duped(alert_hash, now):
            self._deduped_count += 1
            self._log.debug(
                "alert.deduped",
                alert_type=alert_type,
                message=message,
            )
            return None

        # Check throttling
        if self._is_throttled(alert_type, now):
            self._throttled_count += 1
            self._log.warning(
                "alert.throttled",
                alert_type=alert_type,
                message=message,
            )
            return None

        # Create alert
        alert = Alert(
            alert_id=alert_id,
            timestamp=now,
            severity=severity,
            alert_type=alert_type,
            message=message,
            source=self._source,
            context=ctx,
        )

        # Update tracking
        self._dedup_cache[alert_hash] = now
        self._throttle_cache.setdefault(alert_type, []).append(now)
        self._alert_history.append(alert)

        # Trim history if needed
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

        # Emit
        await self._emit_alert(alert)
        self._emitted_count += 1

        return alert

    async def emit_drift_alert(
        self,
        severity: AlertSeverity,
        drift_type: str,
        fragment_id: str,
        drift_score: float,
        message: str,
        recommendation: str | None = None,
    ) -> Alert | None:
        """
        Convenience method for emitting drift alerts.
        
        Args:
            severity: Alert severity
            drift_type: Type of drift (distribution, causal, semantic, concept)
            fragment_id: Affected graph fragment
            drift_score: Numerical drift score
            message: Alert message
            recommendation: Recommended action
        
        Returns:
            Alert object if emitted, None otherwise
        """
        context = {
            "drift_type": drift_type,
            "fragment_id": fragment_id,
            "drift_score": drift_score,
            "recommendation": recommendation,
        }

        return await self.emit(
            severity=severity,
            alert_type=f"DRIFT_{drift_type.upper()}",
            message=message,
            context=context,
        )

    async def acknowledge(self, alert_id: str, acknowledged_by: str) -> bool:
        """
        Acknowledge an alert.
        
        Args:
            alert_id: ID of the alert to acknowledge
            acknowledged_by: User or agent ID
        
        Returns:
            True if acknowledged, False if not found
        """
        for alert in self._alert_history:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.now(UTC)

                self._log.info(
                    "alert.acknowledged",
                    alert_id=alert_id,
                    acknowledged_by=acknowledged_by,
                )
                return True

        return False

    def get_recent_alerts(
        self,
        severity: AlertSeverity | None = None,
        alert_type: str | None = None,
        limit: int = 100,
        include_acknowledged: bool = True,
    ) -> list[Alert]:
        """
        Get recent alerts from history.
        
        Args:
            severity: Filter by severity
            alert_type: Filter by alert type
            limit: Maximum number to return
            include_acknowledged: Whether to include acknowledged alerts
        
        Returns:
            List of Alert objects
        """
        alerts = self._alert_history

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        if alert_type:
            alerts = [a for a in alerts if a.alert_type == alert_type]

        if not include_acknowledged:
            alerts = [a for a in alerts if not a.acknowledged]

        return alerts[-limit:]

    def get_unacknowledged_alerts(self) -> list[Alert]:
        """Get all unacknowledged alerts."""
        return [a for a in self._alert_history if not a.acknowledged]

    def _hash_alert(self, alert_type: str, message: str, context: dict) -> str:
        """Generate a hash for deduplication."""
        content = f"{alert_type}:{message}:{sorted(context.items())}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _is_duped(self, alert_hash: str, now: datetime) -> bool:
        """Check if this alert was recently emitted."""
        last_time = self._dedup_cache.get(alert_hash)
        if not last_time:
            return False

        elapsed = (now - last_time).total_seconds()
        return elapsed < self._config.dedup_window_seconds

    def _is_throttled(self, alert_type: str, now: datetime) -> bool:
        """Check if this alert type has been throttled."""
        timestamps = self._throttle_cache.get(alert_type, [])

        # Filter to recent window
        cutoff = now - timedelta(seconds=self._config.throttle_window_seconds)
        recent = [t for t in timestamps if t > cutoff]

        # Update cache
        self._throttle_cache[alert_type] = recent

        return len(recent) >= self._config.throttle_max_per_type

    async def _emit_alert(self, alert: Alert) -> None:
        """Emit the alert through all configured channels."""
        # Always log
        log_fn = self._log.info
        if alert.severity == AlertSeverity.CRITICAL:
            log_fn = self._log.error
        elif alert.severity == AlertSeverity.HIGH:
            log_fn = self._log.warning
        elif alert.severity == AlertSeverity.DEBUG:
            log_fn = self._log.debug

        log_kwargs = {
            "alert_id": alert.alert_id,
            "severity": alert.severity.value,
            "alert_type": alert.alert_type,
            "message": alert.message,
        }

        if self._config.log_full_context:
            log_kwargs["context"] = alert.context

        log_fn("alert.emitted", **log_kwargs)

        # Severity-specific callbacks
        callback = None
        if alert.severity == AlertSeverity.CRITICAL and self._config.on_critical:
            callback = self._config.on_critical
        elif alert.severity == AlertSeverity.HIGH and self._config.on_high or alert.severity == AlertSeverity.MEDIUM and self._config.on_medium:
            callback = self._config.on_medium

        if callback:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                self._log.error("alert.callback_failed", error=str(e), alert_id=alert.alert_id)

    async def cleanup(self) -> dict[str, Any]:
        """
        Clean up old deduplication and throttling entries.
        
        Returns:
            Dict with cleanup statistics
        """
        now = datetime.now(UTC)

        # Clean dedup cache
        old_dedup_count = len(self._dedup_cache)
        cutoff = now - timedelta(seconds=self._config.dedup_window_seconds * 2)
        self._dedup_cache = {
            k: v for k, v in self._dedup_cache.items()
            if v > cutoff
        }

        # Clean throttle cache
        throttle_cutoff = now - timedelta(seconds=self._config.throttle_window_seconds * 2)
        for alert_type in list(self._throttle_cache.keys()):
            self._throttle_cache[alert_type] = [
                t for t in self._throttle_cache[alert_type]
                if t > throttle_cutoff
            ]
            if not self._throttle_cache[alert_type]:
                del self._throttle_cache[alert_type]

        return {
            "dedup_entries_removed": old_dedup_count - len(self._dedup_cache),
            "dedup_entries_remaining": len(self._dedup_cache),
            "throttle_types_remaining": len(self._throttle_cache),
        }

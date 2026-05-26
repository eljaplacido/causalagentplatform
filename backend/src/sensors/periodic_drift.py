"""
Periodic Drift Sensor — scheduled drift detection for continuous monitoring.

This module implements a production-ready sensor that periodically runs
drift detection on configured graph fragments. It integrates with:
- DriftMonitorAgent for drift detection logic
- Experience buffer for pattern learning
- Neo4j for alert storage
- AlertEmitter for notification dispatch

EpistemicState constraints enforced:
- audit_log_required: All sensor runs logged
- no_semantic_layer_writes: Sensor is read-only (alerts only)

Usage::

    config = DriftSensorConfig(
        check_interval_seconds=300,  # 5 minutes
        fragment_ids=["frag-1", "frag-2"],
        enable_neo4j_storage=True,
    )
    sensor = DriftSensor(config)
    await sensor.start()
    # ... runs continuously ...
    await sensor.stop()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from src.agents.drift_monitor import DriftMonitorAgent
from src.semantic_layer.models import CynefinDomain, EpistemicState
from src.sensors.alert_emit import AlertEmitter, AlertSeverity
from src.skills import get_recent_drift_alerts

log = structlog.get_logger(__name__)


@dataclass
class DriftSensorConfig:
    """Configuration for the periodic drift sensor."""
    
    # Timing
    check_interval_seconds: float = 300.0  # 5 minutes default
    initial_delay_seconds: float = 10.0  # Wait before first check
    
    # Target fragments to monitor (empty = monitor all)
    fragment_ids: list[str] = field(default_factory=list)
    
    # Drift detection thresholds
    kl_threshold: float = 0.1
    severe_kl_threshold: float = 0.5
    
    # Storage and emission
    enable_neo4j_storage: bool = True
    enable_alert_emission: bool = True
    
    # Pattern learning from experience buffer
    enable_pattern_learning: bool = True
    pattern_learning_window_days: int = 30
    
    # Alert suppression (don't alert on same fragment within this window)
    alert_suppression_seconds: float = 3600.0  # 1 hour
    
    # Callback for custom alert handling
    alert_callback: Callable[[dict[str, Any]], None] | None = None
    
    def __post_init__(self):
        if self.check_interval_seconds < 60:
            log.warning(
                "drift_sensor.short_interval",
                interval_seconds=self.check_interval_seconds,
                message="Very short intervals may impact performance",
            )


class DriftSensor:
    """
    Continuous drift detection sensor.
    
    Runs periodic drift checks on configured graph fragments and emits
    alerts when drift is detected. Integrates with the experience buffer
    for pattern learning.
    
    The sensor runs asynchronously and can be started/stopped dynamically.
    
    Args:
        config: DriftSensorConfig instance with sensor settings
        agent: Optional DriftMonitorAgent instance (creates default if not provided)
    """
    
    def __init__(
        self,
        config: DriftSensorConfig | None = None,
        agent: DriftMonitorAgent | None = None,
    ):
        self._config = config or DriftSensorConfig()
        self._agent = agent or DriftMonitorAgent(
            agent_id="drift-sensor-agent",
            kl_threshold=self._config.kl_threshold,
            severe_kl_threshold=self._config.severe_kl_threshold,
            enable_neo4j_storage=self._config.enable_neo4j_storage,
            enable_alert_emission=self._config.enable_alert_emission,
        )
        
        # Dedicated alert emitter for sensor-level notifications (failures,
        # pattern learning, etc.). Distinct from the agent's drift alerts.
        self._sensor_alert_emitter = AlertEmitter(source="drift-sensor")
        
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_alert_times: dict[str, datetime] = {}
        self._check_count = 0
        self._drift_detected_count = 0
        
        self._log = log.bind(sensor="drift_sensor")
    
    @property
    def is_running(self) -> bool:
        """Return True if the sensor is currently running."""
        return self._running
    
    @property
    def stats(self) -> dict[str, Any]:
        """Get sensor statistics."""
        return {
            "running": self._running,
            "check_count": self._check_count,
            "drift_detected_count": self._drift_detected_count,
            "config": {
                "check_interval_seconds": self._config.check_interval_seconds,
                "fragment_count": len(self._config.fragment_ids),
                "enable_neo4j_storage": self._config.enable_neo4j_storage,
                "enable_alert_emission": self._config.enable_alert_emission,
            },
        }
    
    async def start(self) -> None:
        """Start the periodic drift sensor."""
        if self._running:
            self._log.warning("drift_sensor.already_running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._log.info(
            "drift_sensor.started",
            interval_seconds=self._config.check_interval_seconds,
            fragment_count=len(self._config.fragment_ids),
        )
    
    async def stop(self) -> None:
        """Stop the periodic drift sensor."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        self._log.info(
            "drift_sensor.stopped",
            total_checks=self._check_count,
            total_drifts_detected=self._drift_detected_count,
        )
    
    async def _run_loop(self) -> None:
        """Main sensor loop."""
        # Initial delay
        await asyncio.sleep(self._config.initial_delay_seconds)
        
        while self._running:
            try:
                await self._run_check_cycle()
            except Exception as e:
                self._log.error("drift_sensor.cycle_failed", error=str(e))
                # Emit a sensor-level alert so operators know the sensor itself is failing
                if self._config.enable_alert_emission:
                    await self._sensor_alert_emitter.emit(
                        severity=AlertSeverity.HIGH,
                        alert_type="SENSOR_FAILURE",
                        message=f"Drift sensor cycle failed: {e}",
                        context={"sensor": "drift", "cycle": self._check_count},
                    )
            
            # Wait for next check
            await asyncio.sleep(self._config.check_interval_seconds)
    
    async def _run_check_cycle(self) -> None:
        """Run a single drift check cycle."""
        self._check_count += 1
        cycle_start = datetime.now(timezone.utc)
        
        self._log.debug("drift_sensor.check_starting", cycle=self._check_count)
        
        # Determine which fragments to check
        fragments_to_check = self._config.fragment_ids
        if not fragments_to_check:
            # If no specific fragments configured, log and emit advisory
            self._log.debug("drift_sensor.no_fragments_configured")
            if self._check_count == 1:
                # Only emit on first occurrence to avoid noise
                await self._sensor_alert_emitter.emit(
                    severity=AlertSeverity.INFO,
                    alert_type="SENSOR_NO_FRAGMENTS",
                    message="No graph fragments configured for drift monitoring",
                    context={"sensor": "drift", "recommendation": "Configure fragment_ids in DriftSensorConfig"},
                )
            # Still log the cycle completion with empty check
            self._log.info(
                "drift_sensor.check_complete",
                cycle=self._check_count,
                fragments_checked=0,
                duration_seconds=0.0,
            )
            return
        
        # Check each fragment
        for fragment_id in fragments_to_check:
            await self._check_fragment(fragment_id)
        
        cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        self._log.info(
            "drift_sensor.check_complete",
            cycle=self._check_count,
            fragments_checked=len(fragments_to_check),
            duration_seconds=cycle_duration,
        )
    
    async def _check_fragment(self, fragment_id: str) -> None:
        """Check a single fragment for drift."""
        # Create ephemeral EpistemicState for this check
        state = EpistemicState(
            session_id=f"drift-sensor-{self._check_count}-{fragment_id}",
            agent_id=self._agent.agent_id,
            cynefin_domain=CynefinDomain.COMPLICATED,
            active_graph_fragment_id=fragment_id,
        )
        
        try:
            # Run drift detection
            result_state = await self._agent.process(state)
            
            # Check if drift was detected
            drift_flags = [f for f in result_state.flags if "drift" in f.lower()]
            
            if drift_flags:
                self._drift_detected_count += 1
                
                # Check suppression window
                last_alert = self._last_alert_times.get(fragment_id)
                now = datetime.now(timezone.utc)
                
                should_alert = True
                if last_alert:
                    seconds_since_last = (now - last_alert).total_seconds()
                    if seconds_since_last < self._config.alert_suppression_seconds:
                        should_alert = False
                        self._log.debug(
                            "drift_sensor.alert_suppressed",
                            fragment_id=fragment_id,
                            seconds_since_last=seconds_since_last,
                        )
                
                if should_alert:
                    self._last_alert_times[fragment_id] = now
                    await self._handle_drift_alert(fragment_id, result_state, drift_flags)
            
            # Pattern learning from experience buffer
            if self._config.enable_pattern_learning:
                await self._learn_from_experience(fragment_id, result_state)
        
        except Exception as e:
            self._log.error(
                "drift_sensor.fragment_check_failed",
                fragment_id=fragment_id,
                error=str(e),
            )
            # Emit sensor-level alert for fragment check failure
            if self._config.enable_alert_emission:
                await self._sensor_alert_emitter.emit(
                    severity=AlertSeverity.HIGH,
                    alert_type="FRAGMENT_CHECK_FAILURE",
                    message=f"Drift check failed for fragment {fragment_id}",
                    context={
                        "fragment_id": fragment_id,
                        "error": str(e),
                        "sensor_cycle": self._check_count,
                    },
                )
    
    async def _handle_drift_alert(
        self,
        fragment_id: str,
        state: EpistemicState,
        drift_flags: list[str],
    ) -> None:
        """Handle a drift alert."""
        alert_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fragment_id": fragment_id,
            "drift_flags": drift_flags,
            "recommendations": [
                {
                    "action": r.action,
                    "risk_tier": r.risk_tier,
                    "confidence": r.confidence,
                }
                for r in state.recommendations
            ],
            "requires_human_review": state.requires_human_review,
        }
        
        self._log.warning(
            "drift_sensor.drift_detected",
            fragment_id=fragment_id,
            drift_flags=drift_flags,
            recommendation_count=len(state.recommendations),
        )
        
        # Custom callback
        if self._config.alert_callback:
            try:
                self._config.alert_callback(alert_data)
            except Exception as e:
                self._log.error("drift_sensor.callback_failed", error=str(e))
                # Do not suppress the error for the caller
                raise
    
    async def _learn_from_experience(self, fragment_id: str, state: EpistemicState) -> None:
        """Learn from experience buffer to improve drift detection."""
        try:
            # Query recent drift alerts for this fragment
            recent_alerts = await get_recent_drift_alerts(
                fragment_id=fragment_id,
                limit=10,
            )
            
            alert_count = recent_alerts.get("count", 0)
            
            if alert_count > 5:
                # Pattern: frequent drift alerts on this fragment
                self._log.info(
                    "drift_sensor.pattern_detected",
                    pattern="frequent_drift",
                    fragment_id=fragment_id,
                    recent_alert_count=alert_count,
                    recommendation="Consider re-validating graph fragment",
                )
                # Emit advisory alert for operators
                if self._config.enable_alert_emission:
                    await self._sensor_alert_emitter.emit(
                        severity=AlertSeverity.MEDIUM,
                        alert_type="PATTERN_FREQUENT_DRIFT",
                        message=f"Frequent drift pattern detected on fragment {fragment_id}",
                        context={
                            "fragment_id": fragment_id,
                            "recent_alert_count": alert_count,
                            "recommendation": "Consider re-validating graph fragment",
                        },
                    )
            
            elif alert_count == 0 and self._check_count > 10:
                # Pattern: stable fragment
                self._log.debug(
                    "drift_sensor.pattern_detected",
                    pattern="stable_fragment",
                    fragment_id=fragment_id,
                )
        
        except Exception as e:
            self._log.debug("drift_sensor.pattern_learning_failed", error=str(e))
    
    async def force_check(self, fragment_id: str | None = None) -> dict[str, Any]:
        """
        Force an immediate drift check.
        
        Args:
            fragment_id: Specific fragment to check (None = check all configured)
        
        Returns:
            Dict with check results
        """
        fragments = [fragment_id] if fragment_id else self._config.fragment_ids
        
        if not fragments:
            return {"checked": False, "reason": "no_fragments_specified"}
        
        results = []
        for fid in fragments:
            state = EpistemicState(
                session_id=f"drift-sensor-force-{fid}",
                agent_id=self._agent.agent_id,
                cynefin_domain=CynefinDomain.COMPLICATED,
                active_graph_fragment_id=fid,
            )
            
            try:
                result_state = await self._agent.process(state)
                drift_flags = [f for f in result_state.flags if "drift" in f.lower()]
                
                results.append({
                    "fragment_id": fid,
                    "drift_detected": len(drift_flags) > 0,
                    "drift_flags": drift_flags,
                    "requires_human_review": result_state.requires_human_review,
                })
            except Exception as e:
                results.append({
                    "fragment_id": fid,
                    "error": str(e),
                })
        
        return {
            "checked": True,
            "fragments_checked": len(fragments),
            "results": results,
        }
    
    async def get_alert_summary(self) -> dict[str, Any]:
        """
        Get a summary of recent drift alerts.
        
        Returns:
            Dict with alert summary statistics
        """
        try:
            all_alerts = await get_recent_drift_alerts(
                limit=100,
                include_acknowledged=False,
            )
            
            alerts = all_alerts.get("alerts", [])
            
            # Count by severity
            severity_counts = {}
            type_counts = {}
            fragment_counts = {}
            
            for alert in alerts:
                severity = alert.get("severity", "UNKNOWN")
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                
                drift_type = alert.get("drift_type", "unknown")
                type_counts[drift_type] = type_counts.get(drift_type, 0) + 1
                
                fragment_id = alert.get("fragment_id", "unknown")
                fragment_counts[fragment_id] = fragment_counts.get(fragment_id, 0) + 1
            
            return {
                "total_unacknowledged_alerts": len(alerts),
                "severity_distribution": severity_counts,
                "type_distribution": type_counts,
                "alerts_by_fragment": fragment_counts,
                "sensor_stats": self.stats,
            }
        
        except Exception as e:
            self._log.error("drift_sensor.summary_failed", error=str(e))
            return {
                "total_unacknowledged_alerts": 0,
                "error": str(e),
                "sensor_stats": self.stats,
            }

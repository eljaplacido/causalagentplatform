"""
Sensors package for continuous monitoring and alerting.

This package provides production-ready sensors for:
- Periodic drift detection
- Alert emission and management
- Health monitoring

All sensors are designed to run asynchronously and integrate with
the agentic harness and experience buffer.
"""

from src.sensors.alert_emit import AlertEmitter, AlertSeverity
from src.sensors.periodic_drift import DriftSensor, DriftSensorConfig

__all__ = [
    "AlertEmitter",
    "AlertSeverity",
    "DriftSensor",
    "DriftSensorConfig",
]

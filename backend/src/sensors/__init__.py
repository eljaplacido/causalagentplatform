"""
Sensors package: alert emission.

AlertEmitter is the in-process alert sink DriftMonitorAgent emits drift alerts
through (logging, callbacks, deduplication, throttling). There is no transport
behind it (no Kafka producer).

A periodic DriftSensor used to live here too. Nothing started it -- the app's
lifespan never did, and the health router that reported on it was never
mounted -- and nothing could feed it: DriftMonitorAgent.process hands its drift
skills a fragment id and no data, so a scheduled run can never raise a drift
alert (register drift-monitor-no-data). It was deleted rather than wired into
the lifespan behind a flag, which would have scheduled a loop that provably
alerts on nothing. A scheduler belongs next to the first real drift data source.
"""

from src.sensors.alert_emit import AlertEmitter, AlertSeverity

__all__ = [
    "AlertEmitter",
    "AlertSeverity",
]

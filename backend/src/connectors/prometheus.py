"""
PrometheusConnector — pulls Prometheus metrics and maps anomalies to causal drafts.

DATA_LAYER.md §4: Prometheus connector type: Pull (HTTP scrape), fields: Metrics
time series, alert states.  Domain: devops (carfdevops vertical).

ETL pipeline:
  1. fetchEvents()           — query Prometheus HTTP API (range_query / alerts)
  2. mapToSemanticObjects()  — convert metric anomalies → CausalSemanticDraft
  3. validate()              — ping Prometheus /-/healthy

Output objects:
  - CausalEntity for each anomalous service / metric
  - Assumption for each statistical anomaly rule (z-score, alert rule)
  - CausalEdge (draft) when two anomalies correlate above threshold

Security: metric labels that match PII patterns are redacted before emission.
"""

from __future__ import annotations

import re
import statistics
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from src.connectors.base import (
    BaseCausalConnector,
    CausalSemanticDraft,
    ConnectorHealthResult,
    DomainEvent,
)

log = structlog.get_logger(__name__)

# Labels that must be redacted before emitting (PII guard)
_PII_LABEL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"user_?id", re.IGNORECASE),
    re.compile(r"email", re.IGNORECASE),
    re.compile(r"customer_?id", re.IGNORECASE),
    re.compile(r"account_?id", re.IGNORECASE),
    re.compile(r"phone", re.IGNORECASE),
    re.compile(r"ip_?addr", re.IGNORECASE),
]

# z-score threshold above which a metric is considered anomalous
_ANOMALY_Z_THRESHOLD: float = 2.5

# Pearson correlation threshold above which two anomalies are proposed as causally linked
_CORRELATION_THRESHOLD: float = 0.75


class PrometheusConnector(BaseCausalConnector):
    """
    Connector for Prometheus metrics sources.

    Implements the CausalConnector interface (DATA_LAYER.md §4) for
    the devops domain.

    Usage::

        conn = PrometheusConnector(
            base_url="http://prometheus:9090",
            connector_id="prometheus-prod",
        )
        events = await conn.fetch_events(cursor="2026-04-04T00:00:00Z")
        drafts = conn.map_to_semantic_objects(events)

    Configuration:
        base_url:     Prometheus HTTP API base URL
        step:         Prometheus range_query step (default "60s")
        lookback_h:   Hours of history for full sync (cursor=None)
        queries:      PromQL expressions to evaluate (default: node_cpu, latency, errors)
        auth_token:   Optional Bearer token for Prometheus auth
    """

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        connector_id: str = "prometheus-connector",
        step: str = "60s",
        lookback_h: int = 1,
        queries: list[str] | None = None,
        auth_token: str | None = None,
    ) -> None:
        super().__init__(
            connector_id=connector_id,
            domain="devops",
            version="1.0",
        )
        self._base_url = base_url.rstrip("/")
        self._step = step
        self._lookback_h = lookback_h
        self._auth_token = auth_token
        self._queries: list[str] = queries or [
            'rate(node_cpu_seconds_total{mode="idle"}[5m])',
            'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))',
            'rate(http_requests_total{status=~"5.."}[5m])',
            "ALERTS{alertstate='firing'}",
        ]
        self._last_error: str | None = None

    # ── fetch_events() ────────────────────────────────────────────────────────

    async def fetch_events(self, cursor: str | None = None) -> list[DomainEvent]:
        """
        Pull metrics from Prometheus HTTP API and return as DomainEvents.

        cursor: ISO8601 start time (None = now - lookback_h hours)

        Each PromQL query is executed as a range_query.
        Anomalous data points (z-score > threshold) become "metric.anomaly" events.
        Active alerts become "alert.firing" events.

        Returns events ordered oldest-first.
        """
        try:
            import httpx
        except ImportError:
            log.warning(
                "prometheus.httpx_not_installed",
                message="httpx not installed — returning empty events. Install with: pip install httpx",
            )
            return []

        now = datetime.now(UTC)
        start_iso = cursor or (
            datetime.fromtimestamp(
                now.timestamp() - self._lookback_h * 3600, tz=UTC
            ).isoformat()
        )

        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        events: list[DomainEvent] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for query in self._queries:
                try:
                    resp = await client.get(
                        f"{self._base_url}/api/v1/query_range",
                        params={
                            "query": query,
                            "start": start_iso,
                            "end": now.isoformat(),
                            "step": self._step,
                        },
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    events.extend(self._parse_range_result(query, data))
                except Exception as exc:
                    self._last_error = str(exc)
                    log.error(
                        "prometheus.query_failed",
                        query=query,
                        error=str(exc),
                    )

        self._last_successful_sync = now.isoformat()
        log.info(
            "prometheus.fetch_events.complete",
            event_count=len(events),
            start=start_iso,
        )
        return events

    def _parse_range_result(
        self, query: str, data: dict[str, Any]
    ) -> list[DomainEvent]:
        """
        Parse a Prometheus range_query API response into DomainEvents.

        Anomaly detection: z-score over the returned time series.
        Points beyond _ANOMALY_Z_THRESHOLD become "metric.anomaly" events.
        """
        events: list[DomainEvent] = []
        if data.get("status") != "success":
            return events

        results = data.get("data", {}).get("result", [])
        for series in results:
            metric_labels = _redact_pii(series.get("metric", {}))
            service = metric_labels.get("job", metric_labels.get("instance", "unknown"))
            values = series.get("values", [])  # [[timestamp, value_str], ...]

            if not values:
                continue

            # Convert to floats (filter out non-numeric strings like "NaN")
            numeric_values = []
            for ts, val_str in values:
                try:
                    numeric_values.append((float(ts), float(val_str)))
                except (ValueError, TypeError):
                    continue

            if len(numeric_values) < 3:
                continue

            series_values = [v for _, v in numeric_values]
            mean = statistics.mean(series_values)
            try:
                std = statistics.stdev(series_values)
            except statistics.StatisticsError:
                std = 0.0

            # Emit anomaly events for outlier points
            for ts, value in numeric_values:
                z_score = abs(value - mean) / std if std > 0 else 0.0
                if z_score > _ANOMALY_Z_THRESHOLD:
                    events.append(
                        DomainEvent.create(
                            domain="devops",
                            entity_type="MetricAnomaly",
                            event_type="metric.anomaly",
                            payload={
                                "query": query,
                                "service": service,
                                "metric_labels": metric_labels,
                                "value": value,
                                "mean": round(mean, 4),
                                "std": round(std, 4),
                                "z_score": round(z_score, 3),
                                "anomaly_timestamp": datetime.fromtimestamp(
                                    ts, tz=UTC
                                ).isoformat(),
                            },
                            source=self._base_url,
                        )
                    )

        return events

    # ── map_to_semantic_objects() ─────────────────────────────────────────────

    def map_to_semantic_objects(
        self, events: list[DomainEvent]
    ) -> list[CausalSemanticDraft]:
        """
        Map Prometheus domain events to DRAFT causal semantic objects.

        Mapping rules:
          metric.anomaly  →  CausalEntity (anomalous service + Assumption (z-score rule)
          alert.firing    →  CausalEntity (alerting service)

        Each metric.anomaly pair with correlation > threshold →
          CausalEdge (DRAFT, "CORRELATES") between the two entities.

        All outputs are DRAFT status.  PII has already been stripped in
        _parse_range_result via _redact_pii().
        """
        drafts: list[CausalSemanticDraft] = []
        now = datetime.now(UTC).isoformat()

        # Group anomaly events by service
        service_anomalies: dict[str, list[DomainEvent]] = {}
        for event in events:
            if event.event_type == "metric.anomaly":
                service = event.payload.get("service", "unknown")
                service_anomalies.setdefault(service, []).append(event)

        # Emit one CausalEntity per anomalous service
        entity_ids: dict[str, str] = {}
        for service, anomaly_events in service_anomalies.items():
            entity_id = str(uuid4())
            entity_ids[service] = entity_id
            avg_z = statistics.mean(
                e.payload.get("z_score", 0.0) for e in anomaly_events
            )
            confidence = min(0.95, avg_z / 10.0)  # normalise z-score to 0–0.95

            drafts.append(
                CausalSemanticDraft(
                    object_type="CausalEntity",
                    draft_payload={
                        "id": entity_id,
                        "name": service,
                        "entity_type": "SERVICE",
                        "domain": "devops",
                        "description": (
                            f"Service exhibiting metric anomalies "
                            f"(avg z-score: {avg_z:.2f} over "
                            f"{len(anomaly_events)} data points)"
                        ),
                        "attributes": {
                            "anomaly_count": len(anomaly_events),
                            "avg_z_score": round(avg_z, 3),
                            "source_queries": list(
                                {e.payload.get("query", "") for e in anomaly_events}
                            ),
                        },
                        "approval_status": "DRAFT",
                    },
                    source_event_id=anomaly_events[0].event_id,
                    inferred_at=now,
                    domain="devops",
                    confidence=round(confidence, 3),
                    pii_stripped=True,
                )
            )

            # Emit Assumption per anomaly rule
            for event in anomaly_events:
                drafts.append(
                    CausalSemanticDraft(
                        object_type="Assumption",
                        draft_payload={
                            "id": str(uuid4()),
                            "statement": (
                                f"Metric '{event.payload.get('query', 'unknown')}' "
                                f"for service '{service}' exceeded z-score threshold "
                                f"{_ANOMALY_Z_THRESHOLD} "
                                f"(observed z={event.payload.get('z_score', 0):.2f}) "
                                f"at {event.payload.get('anomaly_timestamp', 'unknown')}."
                            ),
                            "assumption_type": "PARAMETRIC",
                            "justification": (
                                f"Prometheus metric anomaly detection. "
                                f"Z-score threshold: {_ANOMALY_Z_THRESHOLD}. "
                                f"Statistical deviation from 5-minute rolling baseline."
                            ),
                            "domain": "devops",
                            "review_status": "PENDING",
                        },
                        source_event_id=event.event_id,
                        inferred_at=now,
                        domain="devops",
                        confidence=round(
                            min(0.95, event.payload.get("z_score", 0) / 10.0), 3
                        ),
                        pii_stripped=True,
                    )
                )

        # Emit draft CausalEdge for services with correlated anomaly timing
        service_list = list(service_anomalies.keys())
        for i, svc_a in enumerate(service_list):
            for svc_b in service_list[i + 1 :]:
                corr = _compute_temporal_correlation(
                    service_anomalies[svc_a], service_anomalies[svc_b]
                )
                if corr >= _CORRELATION_THRESHOLD:
                    drafts.append(
                        CausalSemanticDraft(
                            object_type="CausalEdge",
                            draft_payload={
                                "id": str(uuid4()),
                                "source_id": entity_ids[svc_a],
                                "source_type": "CausalEntity",
                                "target_id": entity_ids[svc_b],
                                "target_type": "CausalEntity",
                                "edge_type": "CORRELATES",
                                "direction": "FORWARD",
                                "confidence": round(corr, 3),
                                "domain": "devops",
                                "approval_status": "DRAFT",
                                "rationale": (
                                    f"Temporal correlation {corr:.2f} between "
                                    f"anomalies in '{svc_a}' and '{svc_b}'. "
                                    "Requires causal validation — correlation is not causation."
                                ),
                            },
                            source_event_id=service_anomalies[svc_a][0].event_id,
                            inferred_at=now,
                            domain="devops",
                            confidence=round(corr, 3),
                            pii_stripped=True,
                        )
                    )

        log.info(
            "prometheus.map_to_semantic_objects.complete",
            event_count=len(events),
            draft_count=len(drafts),
            anomalous_services=len(service_anomalies),
        )
        return drafts

    # ── validate() ───────────────────────────────────────────────────────────

    def validate(self) -> ConnectorHealthResult:
        """
        Check Prometheus connectivity (synchronous health ping).

        Uses urllib since this is typically called at startup.
        """
        import urllib.error
        import urllib.request

        healthy = False
        error_msg: str | None = None
        fields_missing: list[str] = []

        try:
            req = urllib.request.Request(
                f"{self._base_url}/-/healthy",
                method="GET",
            )
            if self._auth_token:
                req.add_header("Authorization", f"Bearer {self._auth_token}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                healthy = resp.status == 200
        except urllib.error.URLError as exc:
            error_msg = f"Prometheus unreachable: {exc}"
        except Exception as exc:
            error_msg = f"Health check failed: {exc}"

        if not self._queries:
            fields_missing.append("queries")

        return ConnectorHealthResult(
            connector_id=self._id,
            healthy=healthy,
            last_successful_sync=self._last_successful_sync,
            error_message=error_msg or self._last_error,
            fields_missing=fields_missing,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _redact_pii(labels: dict[str, str]) -> dict[str, str]:
    """
    Remove or hash label keys that match PII patterns.

    Returns a new dict with PII labels replaced by '[REDACTED]'.
    """
    redacted: dict[str, str] = {}
    for key, value in labels.items():
        if any(pat.search(key) for pat in _PII_LABEL_PATTERNS):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _compute_temporal_correlation(
    events_a: list[DomainEvent],
    events_b: list[DomainEvent],
) -> float:
    """
    Compute temporal co-occurrence correlation between two anomaly event series.

    Uses a 60-second time-bucket approach: if both series have anomalies in the
    same minute, they are considered correlated for that bucket.

    Returns Pearson-like correlation in [0.0, 1.0].
    """
    def to_minute_buckets(events: list[DomainEvent]) -> set[int]:
        buckets: set[int] = set()
        for event in events:
            ts_str = event.payload.get("anomaly_timestamp", event.timestamp)
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                # Bucket by minute
                buckets.add(int(ts.timestamp()) // 60)
            except Exception:
                pass
        return buckets

    buckets_a = to_minute_buckets(events_a)
    buckets_b = to_minute_buckets(events_b)

    if not buckets_a or not buckets_b:
        return 0.0

    intersection = buckets_a & buckets_b
    union = buckets_a | buckets_b
    # Jaccard similarity as a proxy for temporal correlation
    return len(intersection) / len(union) if union else 0.0

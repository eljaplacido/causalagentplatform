"""
Unit tests for src/connectors/base.py and src/connectors/prometheus.py.

Invariants tested:
  1.  DomainEvent.create() generates a UUID event_id and current ISO timestamp
  2.  CausalSemanticDraft always has pii_stripped=True by default
  3.  ConnectorHealthResult defaults checked_at to now
  4.  BaseCausalConnector: concrete subclass wires id/domain/version correctly
  5.  _redact_pii() redacts user_id, email, customer_id, account_id, phone, ip_addr labels
  6.  _redact_pii() leaves non-PII labels untouched
  7.  _compute_temporal_correlation() returns 1.0 for identical event sets
  8.  _compute_temporal_correlation() returns 0.0 for non-overlapping event sets
  9.  _parse_range_result() returns empty list on failed/empty Prometheus response
  10. _parse_range_result() emits anomaly events only for z-score > 2.5
  11. map_to_semantic_objects() produces CausalEntity per anomalous service
  12. map_to_semantic_objects() produces Assumption per anomaly event
  13. map_to_semantic_objects() produces CausalEdge for highly correlated services
  14. map_to_semantic_objects() skips edge when correlation < threshold
  15. validate() returns healthy=True when urllib responds 200
  16. validate() returns healthy=False on URLError
  17. PrometheusConnector default queries list is non-empty
  18. subscribe() is a no-op by default (does not raise)
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.base import (
    BaseCausalConnector,
    CausalSemanticDraft,
    ConnectorHealthResult,
    DomainEvent,
)
from src.connectors.prometheus import (
    PrometheusConnector,
    _compute_temporal_correlation,
    _redact_pii,
)

# No module-level asyncio mark needed — connector tests are synchronous


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_event(
    service: str = "api-gateway",
    z_score: float = 3.5,
    anomaly_ts: str | None = None,
) -> DomainEvent:
    """Factory for a minimal metric.anomaly DomainEvent."""
    ts = anomaly_ts or "2026-04-04T12:00:00+00:00"
    return DomainEvent(
        event_id="test-event-001",
        domain="devops",
        entity_type="MetricAnomaly",
        event_type="metric.anomaly",
        payload={
            "query": 'rate(http_requests_total{status=~"5.."}[5m])',
            "service": service,
            "metric_labels": {"job": service},
            "value": 0.95,
            "mean": 0.10,
            "std": 0.05,
            "z_score": z_score,
            "anomaly_timestamp": ts,
        },
        source="http://prometheus:9090",
        timestamp=ts,
    )


def _make_prometheus_range_response(
    metric_labels: dict[str, str],
    values: list[list[Any]],
    status: str = "success",
) -> dict[str, Any]:
    """Build a minimal Prometheus range_query API response."""
    return {
        "status": status,
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": metric_labels,
                    "values": values,
                }
            ],
        },
    }


# ── 1. DomainEvent.create() ───────────────────────────────────────────────────


def test_domain_event_create_sets_uuid_and_timestamp() -> None:
    """DomainEvent.create() must generate a non-empty event_id and ISO timestamp."""
    event = DomainEvent.create(
        domain="devops",
        entity_type="MetricAnomaly",
        event_type="metric.anomaly",
        payload={"key": "value"},
        source="http://prometheus:9090",
    )

    assert event.event_id and len(event.event_id) > 0
    assert event.domain == "devops"
    assert event.entity_type == "MetricAnomaly"
    assert event.event_type == "metric.anomaly"
    assert event.source == "http://prometheus:9090"
    assert event.version == "1.0"
    # Timestamp must be valid ISO8601
    datetime.fromisoformat(event.timestamp)


def test_domain_event_create_custom_version() -> None:
    """DomainEvent.create() must pass through a custom version."""
    event = DomainEvent.create(
        domain="finance",
        entity_type="Transaction",
        event_type="tx.created",
        payload={},
        source="bank-feed",
        version="2.0",
    )
    assert event.version == "2.0"


# ── 2. CausalSemanticDraft always pii_stripped ────────────────────────────────


def test_causal_semantic_draft_pii_stripped_default() -> None:
    """CausalSemanticDraft.pii_stripped must default to True."""
    draft = CausalSemanticDraft(
        object_type="CausalEntity",
        draft_payload={"name": "test-service"},
        source_event_id="event-001",
        inferred_at=datetime.now(timezone.utc).isoformat(),
        domain="devops",
        confidence=0.8,
    )
    assert draft.pii_stripped is True


def test_causal_semantic_draft_fields() -> None:
    """CausalSemanticDraft stores all fields correctly."""
    now_str = datetime.now(timezone.utc).isoformat()
    draft = CausalSemanticDraft(
        object_type="CausalEdge",
        draft_payload={"source_id": "a", "target_id": "b"},
        source_event_id="event-002",
        inferred_at=now_str,
        domain="finance",
        confidence=0.65,
    )
    assert draft.object_type == "CausalEdge"
    assert draft.domain == "finance"
    assert draft.confidence == 0.65
    assert draft.inferred_at == now_str


# ── 3. ConnectorHealthResult defaults ─────────────────────────────────────────


def test_connector_health_result_defaults() -> None:
    """ConnectorHealthResult must auto-populate checked_at and fields_missing."""
    result = ConnectorHealthResult(
        connector_id="prometheus-prod",
        healthy=True,
        last_successful_sync=None,
        error_message=None,
    )
    assert result.connector_id == "prometheus-prod"
    assert result.healthy is True
    assert result.fields_missing == []
    assert result.checked_at  # must be set
    datetime.fromisoformat(result.checked_at)  # valid ISO8601


# ── 4. BaseCausalConnector concrete subclass ──────────────────────────────────


def test_base_connector_properties() -> None:
    """Concrete subclass must expose id, domain, version via properties."""

    class _ConcreteConnector(BaseCausalConnector):
        async def fetch_events(self, cursor=None):
            return []

        def map_to_semantic_objects(self, events):
            return []

        def validate(self):
            return ConnectorHealthResult(
                connector_id=self._id,
                healthy=True,
                last_successful_sync=None,
                error_message=None,
            )

    conn = _ConcreteConnector(
        connector_id="test-connector",
        domain="finance",
        version="2.1",
    )
    assert conn.id == "test-connector"
    assert conn.domain == "finance"
    assert conn.version == "2.1"


def test_base_connector_subscribe_no_op() -> None:
    """subscribe() must not raise and be a no-op by default."""

    class _ConcreteConnector(BaseCausalConnector):
        async def fetch_events(self, cursor=None):
            return []

        def map_to_semantic_objects(self, events):
            return []

        def validate(self):
            return ConnectorHealthResult("x", False, None, None)

    conn = _ConcreteConnector("sub-test", "devops")
    conn.subscribe("test-topic")  # must not raise


# ── 5. _redact_pii() — PII labels redacted ────────────────────────────────────


@pytest.mark.parametrize(
    "pii_key",
    ["user_id", "userid", "email", "EMAIL", "customer_id", "customerId",
     "account_id", "accountid", "phone", "PHONE", "ip_addr", "ipaddr"],
)
def test_redact_pii_replaces_pii_labels(pii_key: str) -> None:
    """_redact_pii() must replace any PII-matching label with '[REDACTED]'."""
    labels = {pii_key: "sensitive-value", "job": "api-gateway"}
    result = _redact_pii(labels)
    assert result[pii_key] == "[REDACTED]", (
        f"Expected '[REDACTED]' for key '{pii_key}', got '{result[pii_key]}'"
    )
    assert result["job"] == "api-gateway"


def test_redact_pii_preserves_non_pii_labels() -> None:
    """_redact_pii() must not modify labels that do not match PII patterns."""
    labels = {"job": "api-gateway", "instance": "10.0.0.1:8080", "namespace": "prod"}
    result = _redact_pii(labels)
    assert result == labels


def test_redact_pii_empty_dict() -> None:
    """_redact_pii() must handle empty label dict."""
    assert _redact_pii({}) == {}


# ── 6. _compute_temporal_correlation() ───────────────────────────────────────


def test_temporal_correlation_identical_events() -> None:
    """Identical event sets must produce correlation 1.0."""
    event = _make_event(anomaly_ts="2026-04-04T12:00:00+00:00")
    result = _compute_temporal_correlation([event], [event])
    assert result == 1.0


def test_temporal_correlation_no_overlap() -> None:
    """Non-overlapping event sets must produce correlation 0.0."""
    event_a = _make_event(service="svc-a", anomaly_ts="2026-04-04T12:00:00+00:00")
    event_b = _make_event(service="svc-b", anomaly_ts="2026-04-04T13:00:00+00:00")
    result = _compute_temporal_correlation([event_a], [event_b])
    assert result == 0.0


def test_temporal_correlation_partial_overlap() -> None:
    """Partial overlap must produce a correlation in (0.0, 1.0)."""
    shared_ts = "2026-04-04T12:00:00+00:00"
    only_a_ts = "2026-04-04T12:01:00+00:00"
    event_shared_a = _make_event(service="svc-a", anomaly_ts=shared_ts)
    event_only_a = _make_event(service="svc-a", anomaly_ts=only_a_ts)
    event_shared_b = _make_event(service="svc-b", anomaly_ts=shared_ts)

    result = _compute_temporal_correlation(
        [event_shared_a, event_only_a], [event_shared_b]
    )
    assert 0.0 < result < 1.0


def test_temporal_correlation_empty_lists() -> None:
    """Empty event lists must produce 0.0 without raising."""
    assert _compute_temporal_correlation([], []) == 0.0
    assert _compute_temporal_correlation([_make_event()], []) == 0.0


# ── 7-8. _parse_range_result() ────────────────────────────────────────────────


def test_parse_range_result_empty_on_failed_status() -> None:
    """Failed Prometheus status must return no events."""
    conn = PrometheusConnector()
    result = conn._parse_range_result(
        "some_query",
        {"status": "error", "error": "execution error"},
    )
    assert result == []


def test_parse_range_result_empty_on_no_values() -> None:
    """Series with no values must produce no events."""
    conn = PrometheusConnector()
    data = _make_prometheus_range_response({"job": "api-gateway"}, [])
    result = conn._parse_range_result("query", data)
    assert result == []


def test_parse_range_result_emits_anomaly_for_outlier() -> None:
    """
    A time series with one extreme outlier must produce a metric.anomaly event
    for that data point (z-score > _ANOMALY_Z_THRESHOLD=2.5).
    """
    conn = PrometheusConnector()
    ts_base = 1743764400  # 2026-04-04T12:00:00Z
    # 9 normal values + 1 extreme outlier
    values = [[ts_base + i * 60, "0.10"] for i in range(9)]
    values.append([ts_base + 9 * 60, "10.0"])  # z-score >> 2.5

    data = _make_prometheus_range_response({"job": "api-gateway"}, values)
    result = conn._parse_range_result("rate(requests[5m])", data)

    assert len(result) >= 1, "At least one anomaly event must be emitted for the outlier"
    assert result[0].event_type == "metric.anomaly"
    assert result[0].payload["service"] == "api-gateway"
    assert result[0].payload["z_score"] > 2.5


def test_parse_range_result_no_events_for_normal_series() -> None:
    """A uniform time series (no outliers) must produce no anomaly events."""
    conn = PrometheusConnector()
    ts_base = 1743764400
    values = [[ts_base + i * 60, "0.10"] for i in range(15)]  # perfectly flat

    data = _make_prometheus_range_response({"job": "stable-svc"}, values)
    result = conn._parse_range_result("rate(requests[5m])", data)

    # A completely flat series has std=0, so z_score is always 0 → no anomalies
    assert result == []


def test_parse_range_result_redacts_pii_labels() -> None:
    """_parse_range_result() must redact PII labels from metric payloads."""
    conn = PrometheusConnector()
    ts_base = 1743764400
    values = [[ts_base + i * 60, "0.10"] for i in range(9)]
    values.append([ts_base + 9 * 60, "10.0"])

    data = _make_prometheus_range_response(
        {"job": "api-gateway", "user_id": "user-123"},
        values,
    )
    result = conn._parse_range_result("query", data)

    if result:
        labels = result[0].payload["metric_labels"]
        assert labels.get("user_id") == "[REDACTED]", (
            "user_id must be redacted in emitted event"
        )
        assert labels["job"] == "api-gateway"


# ── 9-11. map_to_semantic_objects() ──────────────────────────────────────────


def test_map_to_semantic_objects_empty_events() -> None:
    """Empty event list must produce no semantic objects."""
    conn = PrometheusConnector()
    result = conn.map_to_semantic_objects([])
    assert result == []


def test_map_to_semantic_objects_entity_per_service() -> None:
    """One CausalEntity must be emitted per unique anomalous service."""
    conn = PrometheusConnector()
    events = [
        _make_event(service="svc-a"),
        _make_event(service="svc-a"),  # same service → single entity
        _make_event(service="svc-b"),
    ]
    drafts = conn.map_to_semantic_objects(events)

    entities = [d for d in drafts if d.object_type == "CausalEntity"]
    assert len(entities) == 2, "Must emit one CausalEntity per unique service"
    services = {e.draft_payload["name"] for e in entities}
    assert services == {"svc-a", "svc-b"}


def test_map_to_semantic_objects_assumption_per_event() -> None:
    """One Assumption must be emitted per anomaly event."""
    conn = PrometheusConnector()
    events = [_make_event(service="svc-a"), _make_event(service="svc-a")]
    drafts = conn.map_to_semantic_objects(events)

    assumptions = [d for d in drafts if d.object_type == "Assumption"]
    assert len(assumptions) == len(events), (
        "Must emit one Assumption per anomaly event"
    )
    for assumption in assumptions:
        assert assumption.pii_stripped is True


def test_map_to_semantic_objects_edge_for_correlated_services() -> None:
    """
    Two services with 100% temporal overlap (same minute bucket) must produce
    a CausalEdge draft.  The correlation threshold is 0.75.
    """
    conn = PrometheusConnector()
    shared_ts = "2026-04-04T12:00:00+00:00"
    events = [
        _make_event(service="svc-a", anomaly_ts=shared_ts),
        _make_event(service="svc-b", anomaly_ts=shared_ts),
    ]
    drafts = conn.map_to_semantic_objects(events)

    edges = [d for d in drafts if d.object_type == "CausalEdge"]
    assert len(edges) >= 1, (
        "Two services with identical anomaly timestamps must produce a CausalEdge"
    )
    edge = edges[0]
    assert edge.draft_payload["edge_type"] == "CORRELATES"
    assert edge.draft_payload["approval_status"] == "DRAFT"
    assert edge.pii_stripped is True


def test_map_to_semantic_objects_no_edge_for_uncorrelated_services() -> None:
    """
    Two services with non-overlapping anomaly timestamps must NOT produce an edge.
    """
    conn = PrometheusConnector()
    events = [
        _make_event(service="svc-a", anomaly_ts="2026-04-04T12:00:00+00:00"),
        _make_event(service="svc-b", anomaly_ts="2026-04-04T13:00:00+00:00"),
    ]
    drafts = conn.map_to_semantic_objects(events)

    edges = [d for d in drafts if d.object_type == "CausalEdge"]
    assert len(edges) == 0, (
        "Non-correlated services must not produce a CausalEdge"
    )


def test_map_to_semantic_objects_all_drafts_pii_stripped() -> None:
    """All outputs from map_to_semantic_objects() must have pii_stripped=True."""
    conn = PrometheusConnector()
    events = [_make_event("svc-a"), _make_event("svc-b")]
    drafts = conn.map_to_semantic_objects(events)

    for draft in drafts:
        assert draft.pii_stripped is True, (
            f"Object type {draft.object_type} must have pii_stripped=True"
        )


# ── 12. validate() ───────────────────────────────────────────────────────────


def test_validate_healthy_when_prometheus_responds_200() -> None:
    """validate() must return healthy=True when Prometheus /-/healthy returns 200."""
    conn = PrometheusConnector(base_url="http://prometheus:9090")

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = conn.validate()

    assert result.healthy is True
    assert result.connector_id == "prometheus-connector"
    assert result.error_message is None


def test_validate_unhealthy_on_url_error() -> None:
    """validate() must return healthy=False when Prometheus is unreachable."""
    conn = PrometheusConnector(base_url="http://unreachable:9090")

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = conn.validate()

    assert result.healthy is False
    assert result.error_message is not None
    assert "unreachable" in result.error_message.lower() or "connection" in result.error_message.lower()


# ── 13. PrometheusConnector defaults ─────────────────────────────────────────


def test_prometheus_connector_default_queries() -> None:
    """PrometheusConnector must have a non-empty default query list."""
    conn = PrometheusConnector()
    assert len(conn._queries) > 0, "Default queries must be non-empty"


def test_prometheus_connector_custom_config() -> None:
    """PrometheusConnector must accept all constructor parameters."""
    conn = PrometheusConnector(
        base_url="http://custom-prom:9090",
        connector_id="prom-custom",
        step="30s",
        lookback_h=6,
        queries=["up"],
        auth_token="test-bearer-token",
    )
    assert conn._base_url == "http://custom-prom:9090"
    assert conn.id == "prom-custom"
    assert conn._step == "30s"
    assert conn._lookback_h == 6
    assert conn._queries == ["up"]
    assert conn._auth_token == "test-bearer-token"
    assert conn.domain == "devops"

"""
Integration tests for Platform Ops API endpoints.

  GET /api/v1/platform/connectors  — real-time backend component health
  GET /api/v1/platform/metrics     — computed metrics from audit_log + semantic layer
  GET /api/v1/platform/drift       — drift alerts from recent rejected verdicts

All tests use the shared db_engine fixture (in-memory SQLite) and bypass
Firebase auth by patching _verify_firebase_token.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app

pytestmark = pytest.mark.asyncio

_MOCK_USER: dict[str, Any] = {
    "uid": "platform-test-user",
    "email": "platform@test.com",
    "name": "Platform Test User",
    "roles": ["platform_engineer"],
}


@pytest_asyncio.fixture
async def client(db_engine: object):
    """Auth client for platform API tests."""
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_MOCK_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-platform-token"},
        ) as ac:
            yield ac


# ── Helper: seed audit_log entries ───────────────────────────────────────────


def _utc_iso(dt: datetime) -> str:
    """Return UTC datetime as ISO string WITHOUT timezone suffix (SQLite-compatible)."""
    return dt.replace(tzinfo=None).isoformat()


async def _seed_audit_log(db_engine: object, entries: list[dict[str, Any]]) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as session:
        for e in entries:
            await session.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, agent_id, action_type, payload_hash, guardian_verdict, "
                    " policy_rules_cited, session_id, created_at) "
                    "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts)"
                ),
                e,
            )
        await session.commit()


# ── 1. GET /connectors ────────────────────────────────────────────────────────


async def test_connectors_returns_list(client: AsyncClient, db_engine: object) -> None:
    """Endpoint returns a JSON list of connector status objects."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


async def test_connectors_schema(client: AsyncClient, db_engine: object) -> None:
    """Each connector object has required fields with correct types."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    for item in resp.json():
        assert "connector_id" in item
        assert "connector_name" in item
        assert "connector_type" in item
        assert isinstance(item["healthy"], bool)
        assert "last_attempt_at" in item
        assert "objects_mapped" in item
        assert "raw_output" in item


async def test_connectors_includes_db(client: AsyncClient, db_engine: object) -> None:
    """SQLite database connector is always included."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    ids = [c["connector_id"] for c in resp.json()]
    assert "db-sqlite" in ids


async def test_connectors_includes_semantic_registry(
    client: AsyncClient, db_engine: object
) -> None:
    """Semantic registry connector is always included."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    ids = [c["connector_id"] for c in resp.json()]
    assert "semantic-registry" in ids


async def test_connectors_includes_audit_log(
    client: AsyncClient, db_engine: object
) -> None:
    """Audit log connector is always included."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    ids = [c["connector_id"] for c in resp.json()]
    assert "audit-log" in ids


async def test_connectors_includes_agent_pipeline(
    client: AsyncClient, db_engine: object
) -> None:
    """LangGraph agent pipeline connector is always included."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    ids = [c["connector_id"] for c in resp.json()]
    assert "agent-pipeline" in ids


async def test_connectors_db_healthy_with_empty_db(
    client: AsyncClient, db_engine: object
) -> None:
    """DB connector is healthy even when tables are empty."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    db_conn = next(c for c in resp.json() if c["connector_id"] == "db-sqlite")
    assert db_conn["healthy"] is True
    assert db_conn["error_message"] is None


async def test_connectors_db_reports_row_counts(
    client: AsyncClient, db_engine: object
) -> None:
    """DB connector raw_output contains actual row counts for all tables."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    db_conn = next(c for c in resp.json() if c["connector_id"] == "db-sqlite")
    raw = db_conn["raw_output"]
    assert "audit_log" in raw
    assert "causal_entities" in raw
    assert "graph_fragments" in raw


async def test_connectors_db_counts_seeded_data(
    client: AsyncClient, db_engine: object
) -> None:
    """After seeding audit_log, DB connector reflects updated row counts."""
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "test",
                "action_type": "TEST",
                "hash": "abc",
                "verdict": "APPROVED",
                "rules": "[]",
                "sid": "sess-1",
                "ts": datetime.now(UTC).isoformat(),
            }
        ],
    )
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    db_conn = next(c for c in resp.json() if c["connector_id"] == "db-sqlite")
    assert db_conn["raw_output"]["audit_log"] >= 1


async def test_connectors_sync_duration_ms_is_int(
    client: AsyncClient, db_engine: object
) -> None:
    """sync_duration_ms is an integer (measured latency, not null)."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    for conn in resp.json():
        if conn["sync_duration_ms"] is not None:
            assert isinstance(conn["sync_duration_ms"], int)


async def test_connectors_requires_auth(db_engine: object) -> None:
    """Endpoint returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/platform/connectors")
    assert resp.status_code == 401


# ── 2. GET /metrics ───────────────────────────────────────────────────────────


async def test_metrics_returns_200(client: AsyncClient, db_engine: object) -> None:
    """Metrics endpoint returns HTTP 200."""
    resp = await client.get("/api/v1/platform/metrics")
    assert resp.status_code == 200


async def test_metrics_schema(client: AsyncClient, db_engine: object) -> None:
    """Response has all required top-level fields."""
    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    assert "weekly_sessions" in body
    assert "policy_patterns" in body
    assert "fragment_stats" in body
    assert "total_audit_entries" in body
    assert "total_sessions_all_time" in body
    assert "overall_approval_rate" in body
    assert "last_activity_at" in body


async def test_metrics_weekly_sessions_padded_to_4(
    client: AsyncClient, db_engine: object
) -> None:
    """weekly_sessions is always padded to 4 entries."""
    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    assert len(body["weekly_sessions"]) == 4


async def test_metrics_weekly_session_fields(
    client: AsyncClient, db_engine: object
) -> None:
    """Each weekly entry has the required fields."""
    resp = await client.get("/api/v1/platform/metrics")
    for week in resp.json()["weekly_sessions"]:
        assert "week" in week
        assert "week_start" in week
        assert "session_count" in week
        assert "approval_rate" in week
        assert "escalation_count" in week


async def test_metrics_fragment_stats_fields(
    client: AsyncClient, db_engine: object
) -> None:
    """fragment_stats has all status breakdown fields."""
    resp = await client.get("/api/v1/platform/metrics")
    fs = resp.json()["fragment_stats"]
    assert "total" in fs
    assert "approved" in fs
    assert "under_review" in fs
    assert "draft" in fs
    assert "deprecated" in fs


async def test_metrics_empty_db_zero_totals(
    client: AsyncClient, db_engine: object
) -> None:
    """With empty DB, total_audit_entries and total_sessions_all_time are 0."""
    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    assert body["total_audit_entries"] == 0
    assert body["total_sessions_all_time"] == 0
    assert body["overall_approval_rate"] == 0.0
    assert body["last_activity_at"] is None


async def test_metrics_counts_seeded_entries(
    client: AsyncClient, db_engine: object
) -> None:
    """After seeding audit entries, totals reflect actual data."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(b"entry1").hexdigest(),
                "verdict": "APPROVED",
                "rules": json.dumps(["PR-001"]),
                "sid": "sess-metrics-1",
                "ts": _utc_iso(now),
            },
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(b"entry2").hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-002"]),
                "sid": "sess-metrics-2",
                "ts": (now - timedelta(minutes=5)).isoformat(),
            },
        ],
    )
    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    assert body["total_audit_entries"] == 2
    assert body["total_sessions_all_time"] == 2
    assert body["overall_approval_rate"] == 0.5
    assert body["last_activity_at"] is not None


async def test_metrics_policy_patterns_from_seeded_data(
    client: AsyncClient, db_engine: object
) -> None:
    """Policy patterns reflect the most-cited rules from audit_log."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"e{i}".encode()).hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-001"]),
                "sid": f"sess-pattern-{i}",
                "ts": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(3)
        ],
    )
    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    patterns = body["policy_patterns"]
    assert len(patterns) > 0
    rule_ids = [p["rule_id"] for p in patterns]
    assert "PR-001" in rule_ids


async def test_metrics_approval_rate_calculation(
    client: AsyncClient, db_engine: object
) -> None:
    """overall_approval_rate is approved/total, rounded to 3 decimal places."""
    now = datetime.now(UTC)
    entries = []
    for i in range(3):
        entries.append({
            "id": str(uuid.uuid4()),
            "agent_id": "GovernanceAgent",
            "action_type": "GUARDIAN_CHECK",
            "hash": hashlib.sha256(f"approved-{i}".encode()).hexdigest(),
            "verdict": "APPROVED",
            "rules": "[]",
            "sid": f"sess-apr-{i}",
            "ts": (now - timedelta(minutes=i)).isoformat(),
        })
    entries.append({
        "id": str(uuid.uuid4()),
        "agent_id": "GovernanceAgent",
        "action_type": "GUARDIAN_CHECK",
        "hash": hashlib.sha256(b"rejected-rate").hexdigest(),
        "verdict": "REJECTED",
        "rules": "[]",
        "sid": "sess-apr-rej",
        "ts": (now - timedelta(minutes=10)).isoformat(),
    })
    await _seed_audit_log(db_engine, entries)

    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    # 3 approved out of 4 = 0.75
    assert abs(body["overall_approval_rate"] - 0.75) < 0.001


async def test_metrics_requires_auth(db_engine: object) -> None:
    """Metrics endpoint returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/platform/metrics")
    assert resp.status_code == 401


# ── 3. GET /drift ─────────────────────────────────────────────────────────────


async def test_drift_returns_list(client: AsyncClient, db_engine: object) -> None:
    """Drift endpoint returns a JSON list."""
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_drift_empty_when_no_rejections(
    client: AsyncClient, db_engine: object
) -> None:
    """No drift alerts when audit_log is empty."""
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_drift_no_alert_for_single_rejection(
    client: AsyncClient, db_engine: object
) -> None:
    """A single REJECTED verdict does not create a cluster-based drift alert."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(b"single-reject").hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-001"]),
                "sid": "sess-single",
                "ts": _utc_iso(now),
            }
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    # No cluster (only 1 occurrence of PR-001), so no alert
    body = resp.json()
    cluster_alerts = [a for a in body if a.get("domain") == "governance"]
    assert len(cluster_alerts) == 0


async def test_drift_alert_for_two_rejections_same_rule(
    client: AsyncClient, db_engine: object
) -> None:
    """Two REJECTED verdicts for the same rule within 24h trigger a drift alert."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"rej-{i}".encode()).hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-002"]),
                "sid": f"sess-drift-{i}",
                "ts": (now - timedelta(minutes=i * 10)).isoformat(),
            }
            for i in range(2)
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    alerts = resp.json()
    assert len(alerts) >= 1
    rule_alerts = [a for a in alerts if "PR-002" in a["message"]]
    assert len(rule_alerts) >= 1


async def test_drift_escalation_always_surfaces(
    client: AsyncClient, db_engine: object
) -> None:
    """A single REQUIRES_ESCALATION verdict always creates a drift alert."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(b"escalation").hexdigest(),
                "verdict": "REQUIRES_ESCALATION",
                "rules": json.dumps(["PR-004"]),
                "sid": "sess-escalation",
                "ts": _utc_iso(now),
            }
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    alerts = resp.json()
    escalation_alerts = [a for a in alerts if a["severity"] == "HIGH" and "REQUIRES_ESCALATION" in a["message"]]
    assert len(escalation_alerts) >= 1


async def test_drift_alert_schema(client: AsyncClient, db_engine: object) -> None:
    """Drift alert objects have all required fields."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"schema-{i}".encode()).hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-003"]),
                "sid": f"sess-schema-{i}",
                "ts": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(2)
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    for alert in resp.json():
        assert "id" in alert
        assert "domain" in alert
        assert "entity_type" in alert
        assert "severity" in alert
        assert "message" in alert
        assert "detected_at" in alert
        assert "affected_fragment_ids" in alert
        assert isinstance(alert["affected_fragment_ids"], list)


async def test_drift_excludes_approved_verdicts(
    client: AsyncClient, db_engine: object
) -> None:
    """APPROVED verdicts do not trigger drift alerts."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"approved-drift-{i}".encode()).hexdigest(),
                "verdict": "APPROVED",
                "rules": json.dumps(["PR-001"]),
                "sid": f"sess-approved-{i}",
                "ts": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(5)
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_drift_deduplicates_alerts(
    client: AsyncClient, db_engine: object
) -> None:
    """Duplicate alert IDs are removed from the response."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"dedup-{i}".encode()).hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-005"]),
                "sid": f"sess-dedup-{i}",
                "ts": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(3)
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    alerts = resp.json()
    ids = [a["id"] for a in alerts]
    assert len(ids) == len(set(ids)), "Duplicate alert IDs found in response"


async def test_drift_requires_auth(db_engine: object) -> None:
    """Drift endpoint returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/platform/drift")
    assert resp.status_code == 401


async def test_drift_severity_critical_for_five_rejections(
    client: AsyncClient, db_engine: object
) -> None:
    """5+ rejections for same rule within 24h → CRITICAL severity."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(f"critical-{i}".encode()).hexdigest(),
                "verdict": "REJECTED",
                "rules": json.dumps(["PR-007"]),
                "sid": f"sess-critical-{i}",
                "ts": (now - timedelta(minutes=i)).isoformat(),
            }
            for i in range(5)
        ],
    )
    resp = await client.get("/api/v1/platform/drift")
    assert resp.status_code == 200
    alerts = resp.json()
    pr007_alerts = [a for a in alerts if "PR-007" in a["message"]]
    assert len(pr007_alerts) >= 1
    assert pr007_alerts[0]["severity"] == "CRITICAL"

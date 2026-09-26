"""
Integration tests for Platform Ops API endpoints.

  GET /api/v1/platform/connectors  — real-time backend component health
  GET /api/v1/platform/metrics     — computed metrics from audit_log + semantic layer
  GET /api/v1/platform/drift       — drift alerts from recent rejected verdicts

All tests use the shared db_engine fixture (in-memory SQLite, or PostgreSQL when
TEST_DATABASE_URL is set) and bypass Firebase auth by patching
_verify_firebase_token.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.types import TEXT_ARRAY, TIMESTAMP, typed_text
from tests.conftest import TEST_DB_BACKEND

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The database connector's id: it reports the database it is connected to.
DB_CONNECTOR = f"db-{TEST_DB_BACKEND}"

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
    """Return UTC datetime as ISO string WITHOUT timezone suffix (a naive UTC time)."""
    return dt.replace(tzinfo=None).isoformat()


async def _seed_audit_log(db_engine: object, entries: list[dict[str, Any]]) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as session:
        for e in entries:
            await session.execute(
                typed_text(
                    "INSERT INTO audit_log "
                    "(id, agent_id, action_type, payload_hash, guardian_verdict, "
                    " policy_rules_cited, session_id, created_at) "
                    "VALUES (:id, :agent_id, :action_type, :hash, :verdict, :rules, :sid, :ts)",
                    rules=TEXT_ARRAY,
                    ts=TIMESTAMP,
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
    """The database connector is always included, named for the database in use."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    ids = [c["connector_id"] for c in resp.json()]
    assert DB_CONNECTOR in ids


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
    db_conn = next(c for c in resp.json() if c["connector_id"] == DB_CONNECTOR)
    assert db_conn["healthy"] is True
    assert db_conn["error_message"] is None


async def test_connectors_db_reports_row_counts(
    client: AsyncClient, db_engine: object
) -> None:
    """DB connector raw_output contains actual row counts for all tables."""
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    db_conn = next(c for c in resp.json() if c["connector_id"] == DB_CONNECTOR)
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
                "rules": [],
                "sid": "sess-1",
                "ts": datetime.now(UTC).isoformat(),
            }
        ],
    )
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    db_conn = next(c for c in resp.json() if c["connector_id"] == DB_CONNECTOR)
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


def _monday(weeks_ago: int) -> datetime:
    """00:00 UTC on the Monday of the week `weeks_ago` weeks before this one."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday(), weeks=weeks_ago)


def _audit_row(at: datetime) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "agent_id": "a",
        "action_type": "X",
        "hash": "h",
        "verdict": "APPROVED",
        "rules": [],
        "sid": f"s-{uuid.uuid4()}",
        "ts": at,
    }


async def test_metrics_weeks_are_the_four_ending_this_week_oldest_first(
    client: AsyncClient, db_engine: object
) -> None:
    """Known truth: k+1 sessions in the week k weeks ago, k = 0..3, and some five
    weeks ago that fall outside. Before, padding was appended after the real
    weeks (so the last entry, which the executive page reads as "this week", was
    padding), and sorted(weeks)[:4] of a 28-day window's five week keys dropped
    the current week."""
    rows = [_audit_row(_monday(k) + timedelta(hours=1)) for k in range(4) for _ in range(k + 1)]
    rows += [_audit_row(_monday(5) + timedelta(hours=1)) for _ in range(7)]
    # The minute before the window: inside a 28-day cutoff on every day but the
    # last minute of a Sunday, and in a fifth week key.
    rows += [_audit_row(_monday(3) - timedelta(minutes=1)) for _ in range(5)]
    await _seed_audit_log(db_engine, rows)
    weeks = (await client.get("/api/v1/platform/metrics")).json()["weekly_sessions"]
    assert [w["session_count"] for w in weeks] == [4, 3, 2, 1]
    assert [w["week_start"][:10] for w in weeks] == [
        _monday(k).date().isoformat() for k in (3, 2, 1, 0)
    ]


async def test_metrics_this_weeks_sessions_are_the_last_entry(
    client: AsyncClient, db_engine: object
) -> None:
    now = datetime.now(UTC)
    await _seed_audit_log(db_engine, [_audit_row(now - timedelta(seconds=5)) for _ in range(3)])
    weeks = (await client.get("/api/v1/platform/metrics")).json()["weekly_sessions"]
    assert [w["session_count"] for w in weeks] == [0, 0, 0, 3]
    assert weeks[-1]["week_start"][:10] == _monday(0).date().isoformat()


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
                "rules": ["PR-001"],
                "sid": "sess-metrics-1",
                "ts": _utc_iso(now),
            },
            {
                "id": str(uuid.uuid4()),
                "agent_id": "GovernanceAgent",
                "action_type": "GUARDIAN_CHECK",
                "hash": hashlib.sha256(b"entry2").hexdigest(),
                "verdict": "REJECTED",
                "rules": ["PR-002"],
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
                "rules": ["PR-001"],
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
            "rules": [],
            "sid": f"sess-apr-{i}",
            "ts": (now - timedelta(minutes=i)).isoformat(),
        })
    entries.append({
        "id": str(uuid.uuid4()),
        "agent_id": "GovernanceAgent",
        "action_type": "GUARDIAN_CHECK",
        "hash": hashlib.sha256(b"rejected-rate").hexdigest(),
        "verdict": "REJECTED",
        "rules": [],
        "sid": "sess-apr-rej",
        "ts": (now - timedelta(minutes=10)).isoformat(),
    })
    await _seed_audit_log(db_engine, entries)

    resp = await client.get("/api/v1/platform/metrics")
    body = resp.json()
    # 3 approved out of 4 = 0.75
    assert abs(body["overall_approval_rate"] - 0.75) < 0.001


async def test_metrics_weekly_verdicts_count_every_audit_row_as_the_overall_rate_does(
    client: AsyncClient, db_engine: object
) -> None:
    """Known truth, from seeded rows all in this week: 2 APPROVED rows of two
    sessions, and 1 REQUIRES_ESCALATION and 1 REJECTED row that carry no session
    (as the seeder's and the governance API's rows do). Verdicts: 2 approved of 4,
    1 escalation; sessions: 2.

    The weekly figures used to count only rows with a session_id while the
    overall rate counted every row, so the executive page showed 76% beside 74%
    and "Escalations (4w) 0" while the audit trail listed escalations.
    """
    now = datetime.now(UTC) - timedelta(seconds=5)
    rows = [
        {**_audit_row(now), "verdict": "APPROVED", "sid": "sess-a"},
        {**_audit_row(now), "verdict": "APPROVED", "sid": "sess-b"},
        {**_audit_row(now), "verdict": "REQUIRES_ESCALATION", "sid": None},
        {**_audit_row(now), "verdict": "REJECTED", "sid": None},
    ]
    await _seed_audit_log(db_engine, rows)
    body = (await client.get("/api/v1/platform/metrics")).json()
    this_week = body["weekly_sessions"][-1]
    assert this_week["session_count"] == 2
    assert this_week["approval_rate"] == 0.5
    assert this_week["escalation_count"] == 1
    # Every row is in this week, so the two rates are over the same rows.
    assert body["overall_approval_rate"] == this_week["approval_rate"]


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
                "rules": ["PR-001"],
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
                "rules": ["PR-002"],
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
                "rules": ["PR-004"],
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


async def test_drift_escalation_message_names_a_session_only_when_there_is_one(
    client: AsyncClient, db_engine: object
) -> None:
    """An escalation recorded without a session (the governance API's, the seeder's)
    read "Session unknown triggered ...". The message names the session when the row
    has one, and otherwise names none; it says which agent and rules the row carries."""
    now = datetime.now(UTC)
    await _seed_audit_log(
        db_engine,
        [
            {**_audit_row(now), "agent_id": "seeder", "verdict": "REQUIRES_ESCALATION",
             "rules": ["PR-001", "PR-007"], "sid": None},
            {**_audit_row(now), "agent_id": "guardian-check-node", "verdict": "REQUIRES_ESCALATION",
             "rules": ["PR-003"], "sid": "sess-chaotic-1"},
        ],
    )
    alerts = (await client.get("/api/v1/platform/drift")).json()
    messages = [a["message"] for a in alerts if a["entity_type"] == "Session"]
    assert len(messages) == 2
    for message in messages:
        assert "unknown" not in message
        assert "None" not in message
    without = next(m for m in messages if "seeder" in m)
    assert "session" not in without.lower()
    assert "PR-001, PR-007" in without
    with_session = next(m for m in messages if "guardian-check-node" in m)
    assert "sess-chaotic-1" in with_session
    assert "PR-003" in with_session


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
                "rules": ["PR-003"],
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
                "rules": ["PR-001"],
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
                "rules": ["PR-005"],
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
                "rules": ["PR-007"],
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


# ── GET /connectors: the Neo4j graph store ────────────────────────────────────
#
# Neo4j is optional (NEO4J_URI unset -> the graph paths fall back to SQL), so the
# connector reports one of three states: unconfigured, configured and reachable,
# configured and unreachable. Only the last is unhealthy. Whatever the driver
# raised stays in the server log: the response carries a fixed message, never the
# driver's error text (which names hosts, ports and sometimes credentials).

_NEO4J_SECRET_ERROR = (
    "Couldn't connect to bolt://neo4j-secret-host.internal:7687 "
    "(auth user=neo4j password=hunter2-leaked)"
)


def _neo4j_connector(body: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [c for c in body if c["connector_id"] == "graph-neo4j"]
    assert len(matches) == 1, [c["connector_id"] for c in body]
    return matches[0]


def _fake_driver(verify: AsyncMock) -> object:
    from unittest.mock import MagicMock

    driver = MagicMock()
    driver.verify_connectivity = verify
    return driver


@pytest.fixture
def neo4j_factory() -> Iterator[Any]:
    from src.db.neo4j_factory import Neo4jFactory

    Neo4jFactory.reset()
    yield Neo4jFactory
    Neo4jFactory.reset()


async def test_connectors_neo4j_unconfigured(
    client: AsyncClient, neo4j_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEO4J_URI", raising=False)
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    neo = _neo4j_connector(resp.json())
    assert neo["healthy"] is True
    assert neo["error_message"] is None
    assert neo["raw_output"] == {"configured": False, "reachable": None, "status": "unconfigured"}


async def test_connectors_neo4j_reachable(
    client: AsyncClient, neo4j_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.test:7687")
    verify = AsyncMock(return_value=None)
    monkeypatch.setattr(
        neo4j_factory, "get_driver", classmethod(lambda cls: _fake_driver(verify))
    )
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    neo = _neo4j_connector(resp.json())
    verify.assert_awaited_once()
    assert neo["healthy"] is True
    assert neo["error_message"] is None
    assert neo["raw_output"] == {"configured": True, "reachable": True, "status": "reachable"}


async def test_connectors_neo4j_unreachable_does_not_leak_the_driver_error(
    client: AsyncClient, neo4j_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j-secret-host.internal:7687")
    verify = AsyncMock(side_effect=OSError(_NEO4J_SECRET_ERROR))
    monkeypatch.setattr(
        neo4j_factory, "get_driver", classmethod(lambda cls: _fake_driver(verify))
    )
    resp = await client.get("/api/v1/platform/connectors")
    assert resp.status_code == 200
    neo = _neo4j_connector(resp.json())
    assert neo["healthy"] is False
    assert neo["raw_output"] == {"configured": True, "reachable": False, "status": "unreachable"}
    assert neo["error_message"]
    for secret in ("neo4j-secret-host", "7687", "hunter2", "Couldn't connect"):
        assert secret not in resp.text, secret


async def test_connectors_neo4j_driver_not_created_is_unreachable(
    client: AsyncClient, neo4j_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.test:7687")
    monkeypatch.setattr(neo4j_factory, "get_driver", classmethod(lambda cls: None))
    resp = await client.get("/api/v1/platform/connectors")
    neo = _neo4j_connector(resp.json())
    assert neo["healthy"] is False
    assert neo["raw_output"]["status"] == "unreachable"


async def test_connectors_neo4j_check_is_bounded_in_time(
    client: AsyncClient, neo4j_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from src.api import platform as platform_module

    async def hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.test:7687")
    monkeypatch.setattr(platform_module, "NEO4J_CHECK_TIMEOUT_SECONDS", 0.05)
    hanging = _fake_driver(AsyncMock(side_effect=hang))
    monkeypatch.setattr(neo4j_factory, "get_driver", classmethod(lambda cls: hanging))
    resp = await asyncio.wait_for(client.get("/api/v1/platform/connectors"), timeout=10)
    neo = _neo4j_connector(resp.json())
    assert neo["healthy"] is False
    assert neo["raw_output"]["status"] == "unreachable"


async def test_connectors_requires_authentication(db_engine: object) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/platform/connectors")
    assert resp.status_code == 401
    assert "neo4j" not in resp.text.lower()

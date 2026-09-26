"""
Unit tests for intervention outcome feedback loop (src/api/interventions.py).

Tests:
  1. Feedback closure computes prediction error correctly
  2. Error severity classification
  3. Pending interventions listing
  4. Prediction accuracy computation
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.interventions import (
    FeedbackRequest,
    FeedbackResponse,
    PendingIntervention,
    _compute_error_severity,
)
from src.api.main import app
from src.db.factory import CloudSqlFactory
from src.db.types import TEXT_ARRAY, TIMESTAMP, typed_text
from tests.conftest import postgres_only

pytestmark = pytest.mark.asyncio

# Seed rows are bound with the column types (src/db/types.py): initiated_by and
# approved_by are JSONB UserRefs in 001_initial.sql, the times TIMESTAMPTZ, and
# feedback_loop_closed a BOOLEAN, so the same seeds insert on SQLite and PostgreSQL.
_TESTER = json.dumps({"id": "tester", "email": "tester@test.com", "name": "Tester"})


_MOCK_USER: dict[str, Any] = {
    "uid": "intervention-test-user",
    "email": "intervention@test.com",
    "name": "Test User",
    # Closing a feedback loop is a DECISION_ROLES act (src/middleware/rbac.py).
    "roles": ["sre"],
}


@pytest_asyncio.fixture
async def intv_client(db_engine: object):
    """Auth client for intervention API tests."""
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_MOCK_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac:
            yield ac


# ── Error severity classification ────────────────────────────────────────────


def test_error_severity_both_zero() -> None:
    """Zero expected and zero actual = LOW."""
    assert _compute_error_severity(0.0, 0.0) == "LOW"


def test_error_severity_low() -> None:
    """Small error = LOW."""
    assert _compute_error_severity(1.0, 1.1) == "LOW"  # 10% error


def test_error_severity_medium() -> None:
    """Moderate error = MEDIUM."""
    assert _compute_error_severity(1.0, 0.7) == "MEDIUM"  # 30% error


def test_error_severity_high() -> None:
    """Large error = HIGH."""
    assert _compute_error_severity(1.0, 0.3) == "HIGH"  # 70% error


def test_error_severity_critical() -> None:
    """Very large error = CRITICAL."""
    assert _compute_error_severity(0.1, 1.0) == "CRITICAL"  # 10x error


def test_error_severity_boundary() -> None:
    """Boundary values for error severity classification."""
    assert _compute_error_severity(1.0, 1.2) == "LOW"   # exactly 20% → LOW (threshold > 0.2)
    assert _compute_error_severity(1.0, 1.21) == "MEDIUM"  # 21% error → MEDIUM
    assert _compute_error_severity(0.1, 0.19) == "HIGH"  # 90% error → HIGH
    assert _compute_error_severity(-0.5, 0.5) == "CRITICAL"  # opposite sign, large diff
    """Zero expected with actual nonzero."""
    assert _compute_error_severity(0.0, 0.5) == "MEDIUM"
    assert _compute_error_severity(0.0, 1.5) == "CRITICAL"  # > 1.0


# ── FeedbackRequest model ────────────────────────────────────────────────────


def test_feedback_request_valid() -> None:
    """Valid feedback request."""
    iid = uuid.uuid4()
    req = FeedbackRequest(
        intervention_id=str(iid),
        actual_effect=0.75,
        measurement_method="ab_test",
        notes="Statistically significant",
    )
    assert req.intervention_id == iid
    assert req.actual_effect == 0.75
    assert req.measurement_method == "ab_test"


def test_feedback_request_defaults() -> None:
    """Feedback request with minimum fields."""
    req = FeedbackRequest(intervention_id=str(uuid.uuid4()), actual_effect=-0.2)
    assert req.measurement_method == "observed"
    assert req.notes is None


# ── FeedbackResponse model ────────────────────────────────────────────────────


def test_feedback_response_values() -> None:
    """Feedback response carries all computed fields."""
    resp = FeedbackResponse(
        intervention_id="intv-001",
        expected_effect=0.5,
        actual_effect=0.3,
        prediction_error=0.2,
        error_severity="MEDIUM",
        feedback_loop_closed=True,
        re_validation_triggered=False,
        drift_alert_emitted=False,
        closed_at="2026-05-05T12:00:00Z",
    )
    assert resp.feedback_loop_closed is True
    assert resp.re_validation_triggered is False
    assert resp.error_severity == "MEDIUM"


# ── PendingIntervention model ─────────────────────────────────────────────────


def test_pending_intervention() -> None:
    """Pending intervention carries open-time metadata."""
    pending = PendingIntervention(
        intervention_id="intv-001",
        name="Rollback deployment",
        initiated_at="2026-05-04T10:00:00Z",
        expected_effect=-0.3,
        hours_open=24.5,
    )
    assert pending.hours_open == 24.5
    assert pending.name == "Rollback deployment"


# ── Integration tests ─────────────────────────────────────────────────────────


async def test_close_feedback_missing_intervention(intv_client: AsyncClient) -> None:
    """Feedback for non-existent intervention returns 404."""
    resp = await intv_client.post(
        "/api/v1/interventions/feedback",
        json={"intervention_id": str(uuid.uuid4()), "actual_effect": 0.5},
    )
    assert resp.status_code == 404


async def test_list_pending_empty(intv_client: AsyncClient) -> None:
    """Pending interventions list returns empty when no interventions exist."""
    resp = await intv_client.get("/api/v1/interventions/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0  # Empty database


async def test_prediction_accuracy_empty(intv_client: AsyncClient) -> None:
    """Prediction accuracy returns zeros when no closed feedback loops."""
    resp = await intv_client.get("/api/v1/interventions/accuracy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_count"] == 0
    assert data["MAE"] == 0.0


async def test_close_feedback_success(intv_client: AsyncClient, db_engine: object) -> None:
    """Close feedback loop for an existing intervention."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        # Seed audit entry first (FOREIGN KEY requirement)
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        # Seed intervention
        await session.execute(
            typed_text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "Test Rollback", "by": _TESTER,
                "iat": datetime.now(UTC).isoformat(),
                "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
                "gvr": verdict_id,
                "ee": json.dumps({"ate": 0.5, "expected_effect": 0.5}),
                "ca": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    resp = await intv_client.post(
        "/api/v1/interventions/feedback",
        json={"intervention_id": intv_id, "actual_effect": 0.35, "notes": "Minor deviation"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_loop_closed"] is True
    assert data["expected_effect"] == 0.5
    assert data["actual_effect"] == 0.35
    assert data["prediction_error"] == 0.15
    assert data["error_severity"] == "MEDIUM"


async def test_close_feedback_already_closed(intv_client: AsyncClient, db_engine: object) -> None:
    """Closing an already-closed feedback loop returns 409."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, actual_effect, outcome_observed_at, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, TRUE, :ae, :now, :ca)",
                iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP, now=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "Already Closed", "by": _TESTER,
                "iat": datetime.now(UTC).isoformat(),
                "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
                "gvr": verdict_id, "ee": json.dumps({"ate": 0.3}),
                "ae": json.dumps({"observed_value": 0.3}),
                "now": datetime.now(UTC).isoformat(),
                "ca": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    resp = await intv_client.post(
        "/api/v1/interventions/feedback",
        json={"intervention_id": intv_id, "actual_effect": 0.3},
    )
    assert resp.status_code == 409


async def test_close_feedback_critical_deviation(intv_client: AsyncClient, db_engine: object) -> None:
    """Critical prediction error triggers re-validation and drift alert."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "Critical Deviation Test", "by": _TESTER,
                "iat": datetime.now(UTC).isoformat(),
                "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
                "gvr": verdict_id,
                "ee": json.dumps({"ate": 0.1, "expected_effect": 0.1}),
                "ca": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    resp = await intv_client.post(
        "/api/v1/interventions/feedback",
        json={"intervention_id": intv_id, "actual_effect": -0.5, "notes": "Opposite direction!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["error_severity"] == "CRITICAL"
    assert data["re_validation_triggered"] is True
    assert data["drift_alert_emitted"] is True

    # The route records what it saw; it does not rule on it. It used to write
    # guardian_verdict=APPROVED rows the Guardian never issued.
    async with CloudSqlFactory.get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action_type, guardian_verdict, payload_hash, signature "
                    "FROM audit_log WHERE subject_id = :sid ORDER BY action_type"
                ),
                {"sid": intv_id},
            )
        ).mappings().all()
    assert [r["action_type"] for r in rows] == [
        "DRIFT_ALERT_OUTCOME_DEVIATION",
        "FEEDBACK_LOOP_CLOSED",
    ]
    assert {r["guardian_verdict"] for r in rows} == {"PENDING"}
    assert all(r["signature"] is None for r in rows)
    assert all(r["payload_hash"].startswith("sha256:") for r in rows)


async def test_close_feedback_high_deviation(intv_client: AsyncClient, db_engine: object) -> None:
    """High prediction error triggers re-validation but not drift alert."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "High Deviation Test", "by": _TESTER,
                "iat": datetime.now(UTC).isoformat(),
                "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
                "gvr": verdict_id,
                "ee": json.dumps({"ate": 1.0}),
                "ca": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    resp = await intv_client.post(
        "/api/v1/interventions/feedback",
        json={"intervention_id": intv_id, "actual_effect": 0.42, "notes": "Significant deviation"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["error_severity"] == "HIGH"
    assert data["re_validation_triggered"] is True
    assert data["drift_alert_emitted"] is False  # Not critical


async def test_list_pending_with_data(intv_client: AsyncClient, db_engine: object) -> None:
    """Pending interventions list returns seeded interventions."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                 rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "feedback_loop_closed, created_at) "
                 "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                 iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "Pending Test", "by": _TESTER,
                "iat": datetime.now(UTC).isoformat(),
                "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
                "gvr": verdict_id, "ee": json.dumps({"ate": 0.5}),
                "ca": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    resp = await intv_client.get("/api/v1/interventions/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


async def test_accuracy_with_closed_loops(intv_client: AsyncClient, db_engine: object) -> None:
    """Prediction accuracy returns metric when closed feedback loops exist."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                 rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        now = datetime.now(UTC).isoformat()
        await session.execute(
            typed_text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "actual_effect, feedback_loop_closed, outcome_observed_at, created_at) "
                 "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, :ae, TRUE, :now, :ca)",
                 iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP, now=TIMESTAMP
            ),
            {
                "id": intv_id, "name": "Accuracy Test", "by": _TESTER,
                "iat": now, "ab": _TESTER, "at": now, "gvr": verdict_id,
                "ee": json.dumps({"ate": 0.5}), "ae": json.dumps({"observed_value": 0.45}),
                "now": now, "ca": now,
            },
        )
        await session.commit()

    resp = await intv_client.get("/api/v1/interventions/accuracy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_count"] >= 1
    assert data["MAE"] > 0.0


# ── Direct endpoint handler tests (for coverage of ASGI-untracked code) ────────


async def test_close_feedback_handler_standalone(db_engine: object) -> None:
    """Test the close_feedback_loop handler directly (not via HTTP)."""
    from src.api.interventions import FeedbackRequest, close_feedback_loop

    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                 rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [],
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "feedback_loop_closed, created_at) "
                 "VALUES (:id, :n, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                 iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {"id": intv_id, "n": "Handler Test", "by": _TESTER,
             "iat": datetime.now(UTC).isoformat(),
             "ab": _TESTER, "at": datetime.now(UTC).isoformat(),
             "gvr": verdict_id, "ee": json.dumps({"ate": 0.8}),
             "ca": datetime.now(UTC).isoformat()},
        )
        await session.commit()

    body = FeedbackRequest(intervention_id=intv_id, actual_effect=0.7)
    result = await close_feedback_loop(body, _MOCK_USER)
    assert result.feedback_loop_closed is True
    assert result.expected_effect == 0.8


async def test_accuracy_handler_standalone(db_engine: object) -> None:
    """Test the get_prediction_accuracy handler directly."""
    from src.api.interventions import get_prediction_accuracy

    result = await get_prediction_accuracy(period_days=30, user=_MOCK_USER)
    assert result["sample_count"] >= 0
    assert "MAE" in result


@pytest.mark.asyncio
async def test_close_feedback_refuses_a_non_uuid_id(intv_client: AsyncClient) -> None:
    """An executed intervention's id is a uuid (interventions.id). A proposal id like
    `int-1-<session>` or any other string is a 422 at the API: on PostgreSQL it used to
    reach the uuid column and fail as a 500 (septa-54)."""
    for bad in ("int-1-session-abc", "iv-1", "1; DROP TABLE interventions"):
        resp = await intv_client.post(
            "/api/v1/interventions/feedback",
            json={"intervention_id": bad, "actual_effect": 0.5},
        )
        assert resp.status_code == 422, (bad, resp.status_code, resp.text)


@postgres_only
async def test_concurrent_closures_close_once(intv_client: AsyncClient, db_engine: object) -> None:
    """Three closures at once: one wins, two get 409, one closure row in the trail.

    PostgreSQL only: the in-memory SQLite test engine is one connection shared by
    every session, so concurrent requests there are not concurrent transactions.

    The route read feedback_loop_closed, then updated unconditionally: concurrent
    closures all passed the read, each wrote a FEEDBACK_LOOP_CLOSED audit row, and
    the stored actual effect was whichever wrote last.
    """
    import asyncio

    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                rules=TEXT_ARRAY, ts=TIMESTAMP
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": [], "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            typed_text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, FALSE, :ca)",
                iat=TIMESTAMP, at=TIMESTAMP, ca=TIMESTAMP
            ),
            {"id": intv_id, "name": "Race", "by": _TESTER,
             "iat": datetime.now(UTC).isoformat(), "ab": _TESTER,
             "at": datetime.now(UTC).isoformat(), "gvr": verdict_id,
             "ee": json.dumps({"ate": 0.5}), "ca": datetime.now(UTC).isoformat()},
        )
        await session.commit()

    # Every request waits, just after reading feedback_loop_closed, until all three
    # have read it: the interleaving the race needs, every run rather than by luck.
    from sqlalchemy.ext.asyncio import AsyncSession

    barrier = asyncio.Barrier(3)
    real_execute = AsyncSession.execute

    async def execute_then_wait(self: AsyncSession, statement: Any, *a: Any, **kw: Any) -> Any:
        result = await real_execute(self, statement, *a, **kw)
        if "feedback_loop_closed FROM interventions" in str(statement):
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return result

    with patch.object(AsyncSession, "execute", execute_then_wait):
        responses = await asyncio.gather(*(
            intv_client.post(
                "/api/v1/interventions/feedback",
                json={"intervention_id": intv_id, "actual_effect": value},
            )
            for value in (0.1, 0.2, 0.3)
        ))
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409, 409], [r.text for r in responses]
    winner = next(r.json() for r in responses if r.status_code == 200)

    async with CloudSqlFactory.get_session_factory()() as session:
        closures = (await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action_type = 'FEEDBACK_LOOP_CLOSED' "
                 "AND session_id = :s"),
            {"s": f"feedback-{intv_id}"},
        )).scalar()
        stored = (await session.execute(
            text("SELECT actual_effect FROM interventions WHERE id = :id"), {"id": intv_id}
        )).scalar()
    assert closures == 1
    stored = json.loads(stored) if isinstance(stored, str) else stored
    assert stored["observed_value"] == winner["actual_effect"]

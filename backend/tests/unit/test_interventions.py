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

pytestmark = pytest.mark.asyncio


_MOCK_USER: dict[str, Any] = {
    "uid": "intervention-test-user",
    "email": "intervention@test.com",
    "name": "Test User",
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
    req = FeedbackRequest(
        intervention_id="intv-001",
        actual_effect=0.75,
        measurement_method="ab_test",
        notes="Statistically significant",
    )
    assert req.intervention_id == "intv-001"
    assert req.actual_effect == 0.75
    assert req.measurement_method == "ab_test"


def test_feedback_request_defaults() -> None:
    """Feedback request with minimum fields."""
    req = FeedbackRequest(intervention_id="intv-002", actual_effect=-0.2)
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
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        # Seed intervention
        await session.execute(
            text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, 0, :ca)"
            ),
            {
                "id": intv_id, "name": "Test Rollback", "by": "tester",
                "iat": datetime.now(UTC).isoformat(),
                "ab": "tester", "at": datetime.now(UTC).isoformat(),
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
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, actual_effect, outcome_observed_at, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, 1, :ae, :now, :ca)"
            ),
            {
                "id": intv_id, "name": "Already Closed", "by": "tester",
                "iat": datetime.now(UTC).isoformat(),
                "ab": "tester", "at": datetime.now(UTC).isoformat(),
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
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, 0, :ca)"
            ),
            {
                "id": intv_id, "name": "Critical Deviation Test", "by": "tester",
                "iat": datetime.now(UTC).isoformat(),
                "ab": "tester", "at": datetime.now(UTC).isoformat(),
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


async def test_close_feedback_high_deviation(intv_client: AsyncClient, db_engine: object) -> None:
    """High prediction error triggers re-validation but not drift alert."""
    intv_id = str(uuid.uuid4())
    verdict_id = str(uuid.uuid4())
    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"
            ),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                "feedback_loop_closed, created_at) "
                "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, 0, :ca)"
            ),
            {
                "id": intv_id, "name": "High Deviation Test", "by": "tester",
                "iat": datetime.now(UTC).isoformat(),
                "ab": "tester", "at": datetime.now(UTC).isoformat(),
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
            text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "feedback_loop_closed, created_at) "
                 "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, 0, :ca)"),
            {
                "id": intv_id, "name": "Pending Test", "by": "tester",
                "iat": datetime.now(UTC).isoformat(),
                "ab": "tester", "at": datetime.now(UTC).isoformat(),
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
            text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        now = datetime.now(UTC).isoformat()
        await session.execute(
            text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "actual_effect, feedback_loop_closed, outcome_observed_at, created_at) "
                 "VALUES (:id, :name, :by, :iat, :ab, :at, :gvr, :ee, :ae, 1, :now, :ca)"),
            {
                "id": intv_id, "name": "Accuracy Test", "by": "tester",
                "iat": now, "ab": "tester", "at": now, "gvr": verdict_id,
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
            text("INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                 "guardian_verdict, policy_rules_cited, created_at) "
                 "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)"),
            {"id": verdict_id, "agent": "tester", "action": "TEST", "hash": "abc",
             "verdict": "APPROVED", "rules": "[]",
             "ts": datetime.now(UTC).isoformat()},
        )
        await session.execute(
            text("INSERT INTO interventions (id, name, initiated_by, initiated_at, "
                 "approved_by, approval_timestamp, guardian_verdict_ref, expected_effect, "
                 "feedback_loop_closed, created_at) "
                 "VALUES (:id, :n, :by, :iat, :ab, :at, :gvr, :ee, 0, :ca)"),
            {"id": intv_id, "n": "Handler Test", "by": "tester",
             "iat": datetime.now(UTC).isoformat(),
             "ab": "tester", "at": datetime.now(UTC).isoformat(),
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

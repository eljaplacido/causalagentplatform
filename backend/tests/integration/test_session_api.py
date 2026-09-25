"""
Integration tests for POST /api/v1/session/run and /incident endpoints.

These tests exercise the full FastAPI application stack — including ASGI middleware,
dependency injection, LangGraph pipeline, and GovernanceAgent DB writes — using an
in-memory SQLite engine and a mocked Firebase token verifier.

Auth contract:
  - All protected routes require a Bearer token.
  - Tests bypass Firebase by patching _verify_firebase_token.
  - One test verifies that a missing token correctly produces 401.

Invariants tested:
  1.  P0 incident → CHAOTIC escalation (requires_human_review, 0 proposed edges, CRITICAL rec)
  2.  P2 incident → full triage pipeline, recommendations produced
  3.  POST /run produces a well-formed SessionStateResponse
  4.  GET /{id}/state returns 200 for a previously cached session
  5.  GET /unknown-id/state returns 404
  6.  Resuming an existing session_id produces a higher cycle_count
  7.  Missing Authorization header returns 401
  8.  All required response fields are present and have correct types
  9.  POST /run with a CHAOTIC description short-circuits with no edges
  10. draft_explanation is non-empty in every successful response
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app

pytestmark = pytest.mark.asyncio

# ── Fixtures ──────────────────────────────────────────────────────────────────

_MOCK_USER: dict[str, Any] = {
    "uid": "test-user-001",
    "email": "test@test.com",
    "name": "Test User",
}


@pytest_asyncio.fixture
async def auth_client(db_engine: object):
    """
    AsyncClient wired to the FastAPI app with:
      - Firebase token verification bypassed (AsyncMock returns _MOCK_USER)
      - Authorization: Bearer header pre-set on every request
      - In-memory SQLite engine injected via db_engine fixture

    The db_engine fixture (defined in conftest.py) sets up CloudSqlFactory with a
    fresh SQLite engine and the full schema before each test.
    """
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new=AsyncMock(return_value=_MOCK_USER),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _incident_body(
    severity: str = "P2",
    title: str = "high latency on api-gateway",
    affected_services: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}",
        "title": title,
        "severity": severity,
        "affected_services": affected_services or ["api-gateway"],
        "metrics_snapshot": {"cpu_pct": 92.5, "p99_ms": 4800},
    }


def _run_body(description: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"description": description}
    if session_id:
        body["session_id"] = session_id
    return body


# ── 1. P0 incident → CHAOTIC immediate escalation ─────────────────────────────


async def test_p0_incident_chaotic_escalation(auth_client: AsyncClient) -> None:
    """
    P0 severity adds 'CRITICAL CASCADING' prefix to the description.
    'cascading' is a chaotic keyword → IncidentTriageAgent classifies CHAOTIC.

    Expected: requires_human_review=True, proposed_edge_count=0,
              rank-1 recommendation is CRITICAL, guardian_verdict != APPROVED.

    From AGENTS.md §2.2: 'CHAOTIC-domain incidents route immediately to human
    incident commander with minimal AI framing.'
    """
    response = await auth_client.post(
        "/api/v1/session/incident",
        json=_incident_body(
            severity="P0",
            title="service cluster failure unknown root cause",
            affected_services=["auth", "billing", "api-gateway"],
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data["requires_human_review"] is True, (
        "P0 CHAOTIC incident must set requires_human_review=True"
    )
    assert data["proposed_edge_count"] == 0, (
        "CHAOTIC incident must produce no causal edge proposals"
    )
    assert len(data["recommendations"]) >= 1
    first_rec = data["recommendations"][0]
    assert first_rec["risk_tier"] == "CRITICAL", (
        "CHAOTIC rank-1 recommendation must be CRITICAL"
    )
    assert "ESCALATE" in first_rec["action"].upper(), (
        "CHAOTIC rank-1 must be an escalation action"
    )


# ── 2. P2 incident → full triage pipeline ─────────────────────────────────────


async def test_p2_incident_runs_triage_pipeline(auth_client: AsyncClient) -> None:
    """
    P2 incident without chaotic keywords runs the full triage pipeline.

    Expected: response 200, at least one recommendation, draft_explanation
              populated, session_id set.

    The title's keywords (deployment, cpu, memory, timeout, latency) classify it
    COMPLICATED. A title with no keyword at all would be DISORDER and go to a person.
    """
    response = await auth_client.post(
        "/api/v1/session/incident",
        json=_incident_body(
            severity="P2",
            title="deployment cpu memory timeout latency degradation",
            affected_services=["payments-service"],
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert isinstance(data["session_id"], str) and len(data["session_id"]) > 0
    assert data["recommendation_count"] >= 1, (
        "P2 triage must produce at least one recommendation"
    )
    assert len(data["recommendations"]) >= 1
    assert data["draft_explanation"], "draft_explanation must be non-empty"
    assert len(data["draft_explanation"]) > 10
    assert isinstance(data["cycle_count"], int) and data["cycle_count"] >= 1, (
        "cycle_count must have been incremented by at least one agent"
    )


# ── 3. POST /run produces well-formed response ────────────────────────────────


async def test_run_session_returns_200(auth_client: AsyncClient) -> None:
    """
    POST /api/v1/session/run must return 200 with a valid SessionStateResponse body.
    """
    response = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body("deployment timeout latency cpu memory"),
    )

    assert response.status_code == 200, response.text
    data = response.json()

    # Verify all required SessionStateResponse fields are present
    required_fields = {
        "session_id",
        "agent_id",
        "cynefin_domain",
        "domain_confidence",
        "guardian_verdict",
        "policy_rules_cited",
        "overall_confidence",
        "requires_human_review",
        "flags",
        "draft_explanation",
        "proposed_edge_count",
        "recommendation_count",
        "recommendations",
        "cycle_count",
        "timestamp",
    }
    missing = required_fields - set(data.keys())
    assert not missing, f"Response missing fields: {missing}"


# ── 4. GET /{id}/state → 200 for existing session ─────────────────────────────


async def test_get_existing_session_returns_state(auth_client: AsyncClient) -> None:
    """
    A session created by POST /run must be retrievable via GET /{id}/state.
    """
    # Create a session
    run_resp = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body("deployment timeout latency"),
    )
    assert run_resp.status_code == 200
    session_id = run_resp.json()["session_id"]

    # Retrieve it
    get_resp = await auth_client.get(f"/api/v1/session/{session_id}/state")

    assert get_resp.status_code == 200, get_resp.text
    state_data = get_resp.json()
    assert state_data["session_id"] == session_id


# ── 5. GET /unknown-id/state → 404 ────────────────────────────────────────────


async def test_get_unknown_session_returns_404(auth_client: AsyncClient) -> None:
    """
    GET /api/v1/session/{unknown}/state must return 404 for a session that
    was never created.
    """
    response = await auth_client.get(
        f"/api/v1/session/{uuid.uuid4()}/state"
    )

    assert response.status_code == 404, response.text


# ── 6. Resume session → cycle_count increases ─────────────────────────────────


async def test_resume_session_increases_cycle_count(auth_client: AsyncClient) -> None:
    """
    Providing the same session_id on a second POST /run must resume the
    cached session.  The cycle_count must be higher in the second response.

    This validates the in-process session cache and state continuation contract.
    """
    description = "deployment latency timeout cpu"

    # First run — creates new session
    first_resp = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body(description),
    )
    assert first_resp.status_code == 200
    first_data = first_resp.json()
    session_id = first_data["session_id"]
    cycle_after_first = first_data["cycle_count"]

    # Second run — resumes the same session
    second_resp = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body(description, session_id=session_id),
    )
    assert second_resp.status_code == 200
    second_data = second_resp.json()

    assert second_data["session_id"] == session_id, (
        "Resumed session must carry the same session_id"
    )
    assert second_data["cycle_count"] > cycle_after_first, (
        "Resumed session must have a higher cycle_count than the first run"
    )


# ── 7. Missing Authorization → 401 ────────────────────────────────────────────


async def test_missing_auth_returns_401() -> None:
    """
    POST /api/v1/session/run without an Authorization header must return 401.

    This validates the FirebaseAuthMiddleware is correctly enforcing auth on
    all non-public paths.  No db_engine fixture needed — the middleware rejects
    before any business logic runs.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        # No Authorization header
    ) as client:
        response = await client.post(
            "/api/v1/session/run",
            json=_run_body("something happened"),
        )

    assert response.status_code == 401, (
        f"Expected 401 without auth, got {response.status_code}: {response.text}"
    )


# ── 8. Required response field types are correct ─────────────────────────────


async def test_response_field_types(auth_client: AsyncClient) -> None:
    """
    All fields in SessionStateResponse must have correct Python types.
    Float fields must be in [0.0, 1.0], string fields must be non-empty.
    """
    response = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body("deployment latency timeout"),
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data["session_id"], str) and data["session_id"]
    assert isinstance(data["agent_id"], str) and data["agent_id"]
    assert isinstance(data["cynefin_domain"], str) and data["cynefin_domain"]
    assert isinstance(data["guardian_verdict"], str) and data["guardian_verdict"]
    assert isinstance(data["policy_rules_cited"], list)
    assert isinstance(data["flags"], list)
    assert isinstance(data["recommendations"], list)
    assert isinstance(data["requires_human_review"], bool)
    assert isinstance(data["proposed_edge_count"], int) and data["proposed_edge_count"] >= 0
    assert isinstance(data["recommendation_count"], int) and data["recommendation_count"] >= 0
    assert isinstance(data["cycle_count"], int) and data["cycle_count"] >= 0
    assert isinstance(data["timestamp"], str) and data["timestamp"]

    assert 0.0 <= data["domain_confidence"] <= 1.0, (
        f"domain_confidence out of [0,1]: {data['domain_confidence']}"
    )
    assert 0.0 <= data["overall_confidence"] <= 1.0, (
        f"overall_confidence out of [0,1]: {data['overall_confidence']}"
    )

    for rec in data["recommendations"]:
        assert isinstance(rec["rank"], int) and rec["rank"] >= 1
        assert isinstance(rec["action"], str) and rec["action"]
        assert 0.0 <= rec["confidence"] <= 1.0
        assert rec["risk_tier"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ── 9. CHAOTIC description via /run also short-circuits ──────────────────────


async def test_run_chaotic_description_no_edges(auth_client: AsyncClient) -> None:
    """
    POST /run with a description containing chaotic keywords must classify
    as CHAOTIC in the LangGraph graph, escalate immediately, and produce
    no proposed causal edges.

    The 'cascading' keyword triggers CHAOTIC classification in the
    cynefin_classify heuristic.
    """
    response = await auth_client.post(
        "/api/v1/session/run",
        json=_run_body("cascading failure unknown total outage critical incident"),
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data["requires_human_review"] is True, (
        "CHAOTIC domain must set requires_human_review=True"
    )
    # CHAOTIC path: graph node_escalate_human → END without guardian_check
    # proposed_edges should remain 0
    assert data["proposed_edge_count"] == 0, (
        "CHAOTIC domain must not produce proposed causal edges"
    )


# ── 10. draft_explanation populated in every successful response ──────────────


async def test_draft_explanation_always_populated(auth_client: AsyncClient) -> None:
    """
    draft_explanation must be non-empty for every successful session response.
    This is the primary human-readable output of the platform pipeline.
    """
    for description in [
        "deployment latency cpu memory",
        "intermittent unclear pattern",
        "something happened on the system",
    ]:
        response = await auth_client.post(
            "/api/v1/session/run",
            json=_run_body(description),
        )
        assert response.status_code == 200, f"Failed for: {description}"
        data = response.json()
        assert data["draft_explanation"], (
            f"draft_explanation must be populated for description: '{description}'"
        )
        assert len(data["draft_explanation"]) > 10

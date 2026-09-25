"""
Integration tests for Governance API endpoints.

  POST /api/governance/check        — invoke Guardian policy check
  GET  /api/governance/review-queue — list pending drafts
  GET  /api/governance/audit-log    — paginated audit log

Uses in-memory SQLite via db_engine fixture and bypasses Firebase auth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.main import app

pytestmark = pytest.mark.asyncio

_MOCK_GOVERNANCE_USER: dict[str, Any] = {
    "uid": "governance-test-001",
    "email": "governance@test.com",
    "name": "Governance Test User",
    "roles": ["governance_officer", "platform_engineer"],
}


@pytest_asyncio.fixture
async def gov_client(db_engine: object):
    """Auth client for governance API tests."""
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_MOCK_GOVERNANCE_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer governance-test-token"},
        ) as ac:
            yield ac


def _utc_naive() -> str:
    """UTC datetime as naive ISO string (no timezone suffix)."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


# ── POST /check ───────────────────────────────────────────────────────────────


async def test_guardian_check_returns_200(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Guardian check returns HTTP 200."""
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "EDGE_PROPOSAL",
            "payload": {
                "source": "deployment-event",
                "target": "latency-increase",
                "confidence": 0.75,
            },
        },
    )
    assert resp.status_code == 200


async def test_guardian_check_response_schema(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Guardian check response has required fields."""
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "EDGE_PROPOSAL",
            "payload": {"confidence": 0.8, "source": "a", "target": "b"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "verdict" in body
    assert "reason" in body
    assert "policy_rules_cited" in body
    assert "audit_entry_id" in body
    assert isinstance(body["policy_rules_cited"], list)


async def test_guardian_check_verdict_is_valid(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Guardian check verdict is one of the valid values."""
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "EDGE_PROPOSAL",
            "payload": {"confidence": 0.9},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] in ("APPROVED", "REJECTED", "REQUIRES_ESCALATION")


async def test_guardian_check_with_session_id(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Guardian check with explicit session_id returns it in the response."""
    session_id = f"test-session-{uuid.uuid4()}"
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "FRAGMENT_APPROVAL",
            "payload": {"fragment_id": "frag-test-001"},
            "session_id": session_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id


async def test_guardian_check_writes_audit_entry(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Guardian check writes an audit_log entry."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "TEST_AUDIT",
            "payload": {"test": True},
        },
    )
    assert resp.status_code == 200
    audit_id = resp.json()["audit_entry_id"]
    assert audit_id is not None

    # Verify audit entry exists in DB
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as s:
        r = await s.execute(text("SELECT COUNT(*) FROM audit_log WHERE id = :id"), {"id": audit_id})
        assert r.scalar() == 1


async def test_guardian_check_requires_auth(db_engine: object) -> None:
    """Guardian check returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/governance/check",
            json={"action_type": "TEST", "payload": {}},
        )
    assert resp.status_code == 401


async def test_guardian_check_high_risk_confidence(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """An ungrounded narrative must be refused *by the grounding rule*, PR-002.

    This test used to put `hallucination_risk` inside `payload`, where no rule
    reads it, and assert the verdict was one of two values. It passed because
    every call was rejected by PR-006 for having no Cynefin domain -- a green test
    that could not tell PR-002 working from PR-002 missing. Now it names the rule
    it is testing and classifies the domain so that rule is the one reached.
    """
    resp = await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "EDGE_PROPOSAL",
            "payload": {"confidence": 0.3, "source": "unknown", "target": "unknown"},
            "cynefin_domain": "COMPLICATED",
            "narrative": "Confidence 0.3 edge from unknown source: will reduce latency.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "REJECTED"
    assert resp.json()["policy_rules_cited"] == ["PR-002"]


# ── GET /review-queue ─────────────────────────────────────────────────────────


async def test_review_queue_returns_200(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Review queue endpoint returns HTTP 200."""
    resp = await gov_client.get("/api/governance/review-queue")
    assert resp.status_code == 200


async def test_review_queue_returns_list(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Review queue returns a JSON list."""
    resp = await gov_client.get("/api/governance/review-queue")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_review_queue_empty_when_no_drafts(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Review queue is empty when no draft objects exist."""
    resp = await gov_client.get("/api/governance/review-queue")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_review_queue_requires_role(db_engine: object) -> None:
    """Review queue requires governance_officer or platform_engineer role."""
    no_role_user = {
        "uid": "no-role-user",
        "email": "norole@test.com",
        "name": "No Role",
        "roles": [],
    }
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=no_role_user,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer no-role-token"},
        ) as ac:
            resp = await ac.get("/api/governance/review-queue")
    assert resp.status_code == 403


async def test_review_queue_accepts_domain_filter(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Review queue accepts domain query parameter without error."""
    resp = await gov_client.get("/api/governance/review-queue?domain=devops")
    assert resp.status_code == 200


async def test_review_queue_requires_auth(db_engine: object) -> None:
    """Review queue returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/governance/review-queue")
    assert resp.status_code == 401


# ── GET /audit-log ────────────────────────────────────────────────────────────


async def test_audit_log_returns_200(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log endpoint returns HTTP 200."""
    resp = await gov_client.get("/api/governance/audit-log")
    assert resp.status_code == 200


async def test_audit_log_response_schema(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log response has required fields."""
    resp = await gov_client.get("/api/governance/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body
    assert "total" in body
    assert "page" in body
    assert "page_size" in body
    assert isinstance(body["entries"], list)


async def test_audit_log_empty_when_no_entries(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log is empty when no entries exist."""
    resp = await gov_client.get("/api/governance/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["entries"] == []
    assert body["page"] == 1


async def test_audit_log_shows_entries_after_check(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log contains entries written by guardian_check."""
    # First create an entry via /check
    await gov_client.post(
        "/api/governance/check",
        json={
            "action_type": "AUDIT_LOG_TEST",
            "payload": {"test": "audit_visibility"},
        },
    )

    resp = await gov_client.get("/api/governance/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert len(body["entries"]) >= 1


async def test_audit_log_pagination(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log respects page_size parameter."""
    # Create 3 audit entries
    for i in range(3):
        await gov_client.post(
            "/api/governance/check",
            json={
                "action_type": f"PAGINATION_TEST_{i}",
                "payload": {"index": i},
            },
        )

    resp = await gov_client.get("/api/governance/audit-log?page_size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page_size"] == 2
    assert len(body["entries"]) <= 2


async def test_audit_log_verdict_filter(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log accepts verdict filter query parameter."""
    resp = await gov_client.get("/api/governance/audit-log?verdict=APPROVED")
    assert resp.status_code == 200


async def test_audit_log_agent_id_filter(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log accepts agent_id filter query parameter."""
    resp = await gov_client.get("/api/governance/audit-log?agent_id=governance-agent-api")
    assert resp.status_code == 200


async def test_audit_log_requires_auth(db_engine: object) -> None:
    """Audit log returns 401 without auth token."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/governance/audit-log")
    assert resp.status_code == 401


async def test_audit_log_requires_role(db_engine: object) -> None:
    """Audit log requires governance role."""
    analyst_user = {
        "uid": "analyst-only",
        "email": "analyst@test.com",
        "name": "Analyst",
        "roles": ["analyst"],
    }
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=analyst_user,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer analyst-token"},
        ) as ac:
            resp = await ac.get("/api/governance/audit-log")
    assert resp.status_code == 403


async def test_audit_log_page_param(
    gov_client: AsyncClient, db_engine: object
) -> None:
    """Audit log accepts page parameter and returns page field."""
    resp = await gov_client.get("/api/governance/audit-log?page=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2


# ── Middleware auth coverage ──────────────────────────────────────────────────


async def test_health_public_path_no_auth(db_engine: object) -> None:
    """GET /health succeeds without any auth token (public path bypass)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_dev_auth_bypass_mode(db_engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """DEV_AUTH_BYPASS=true allows 'dev-local-token' to authenticate without Firebase."""
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer dev-local-token"},
    ) as ac:
        resp = await ac.get("/api/governance/review-queue")
    assert resp.status_code == 200


async def test_invalid_token_returns_401(db_engine: object) -> None:
    """A malformed / non-Firebase token returns 401 (token verification fails)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer this-is-not-a-valid-firebase-token"},
    ) as ac:
        resp = await ac.get("/api/governance/review-queue")
    assert resp.status_code == 401


async def test_docs_public_path_no_auth(db_engine: object) -> None:
    """GET /docs returns 200 without auth (Swagger UI is public)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/docs")
    assert resp.status_code == 200

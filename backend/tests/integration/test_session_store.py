"""Integration tests for durable session-state persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.api import session as session_api

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def auth_client(db_engine: object):
    """Authenticated test client with Firebase verification mocked out."""
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new=AsyncMock(return_value={"uid": "test-user-001"}),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield client


async def test_session_state_survives_cache_clear(auth_client: AsyncClient) -> None:
    """GET /state must fall back to the durable session store after cache loss."""
    run_response = await auth_client.post(
        "/api/v1/session/run",
        json={"description": "deployment latency timeout cpu memory"},
    )
    assert run_response.status_code == 200, run_response.text
    session_id = run_response.json()["session_id"]

    session_api._session_cache.clear()

    get_response = await auth_client.get(f"/api/v1/session/{session_id}/state")
    assert get_response.status_code == 200, get_response.text
    payload = get_response.json()
    assert payload["session_id"] == session_id
    assert payload["draft_explanation"]

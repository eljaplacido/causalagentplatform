"""CORS headers reach the browser on every response, a 401 included.

Starlette runs the middleware added last first. With the auth middleware outside CORS, a
request without a valid token got a 401 with no ``Access-Control-Allow-Origin``, and the
browser reported it as a CORS failure: a newcomer whose frontend sent no token saw a CORS
error instead of the 401 it was. These tests use the real app and an allowed origin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ORIGIN = "http://localhost:3000"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", ORIGIN)
    from src.api.main import create_app

    return TestClient(create_app())


def test_a_401_carries_the_cors_headers(client: TestClient) -> None:
    response = client.get("/api/v1/decisions", headers={"Origin": ORIGIN})
    assert response.status_code == 401, response.text
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_a_disallowed_origin_still_gets_no_cors_header(client: TestClient) -> None:
    response = client.get("/api/v1/decisions", headers={"Origin": "http://evil.example"})
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_a_preflight_is_answered_without_a_token(client: TestClient) -> None:
    response = client.options(
        "/api/v1/decisions",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == ORIGIN

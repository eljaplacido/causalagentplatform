"""The auth middleware's public paths are exact paths, not prefixes.

FirebaseAuthMiddleware lets a request through without a token when its path is
public. It used to test ``path.startswith(p)`` for every public path, so any
route whose path merely *began* with ``/health``, ``/docs``, ``/redoc`` or
``/openapi.json`` -- ``/healthz``, ``/docs-admin``, ``/health/internal`` -- was
served unauthenticated. These tests mount such routes on a small app behind the
real middleware and require a token for each of them, while the documented
public paths stay public.

FastAPI's interactive docs need no path of their own beyond ``/docs``,
``/redoc`` and ``/openapi.json``: Swagger UI and ReDoc load their scripts and
styles from a CDN, and the OAuth2 redirect page (``/docs/oauth2-redirect``) is
only used by a Swagger "Authorize" flow this app does not configure.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.auth import PUBLIC_PATHS, FirebaseAuthMiddleware


def _app() -> FastAPI:
    app = FastAPI(docs_url="/docs", redoc_url="/redoc")
    app.add_middleware(FirebaseAuthMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Routes that share a public path's prefix and are NOT public.
    for i, path in enumerate((
        "/healthz",
        "/health/internal",
        "/docs-admin",
        "/docs/admin",
        "/redoc-internal",
        "/openapi.json.bak",
        "/favicon.ico.php",
    )):
        app.add_api_route(
            path, lambda: {"secret": True}, methods=["GET"], operation_id=f"not_public_{i}"
        )
    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # The dev bypass must not be what decides these tests.
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    return TestClient(_app())


@pytest.mark.parametrize("path", ["/health", "/docs", "/redoc", "/openapi.json"])
def test_public_paths_are_served_without_a_token(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, (path, response.text)


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/health/internal",
        "/docs-admin",
        "/docs/admin",
        "/redoc-internal",
        "/openapi.json.bak",
        "/favicon.ico.php",
    ],
)
def test_a_path_that_only_starts_like_a_public_one_requires_a_token(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 401, (path, response.text)
    assert "secret" not in response.text


def test_public_paths_are_the_documented_set() -> None:
    assert frozenset(
        {"/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}
    ) == PUBLIC_PATHS

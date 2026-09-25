"""
The scope the database's row-level security reads, as the application builds it.

Written for septa-47 (register entry `rls-policies-bind-nothing`). The policies in
migration 010_rls_binds.sql read two settings; src/db/scope.py encodes them and
the auth middleware binds them per request (rbac.scope_for_user). What the
policies then do with them is tests/integration/test_rls_domain_isolation.py,
on PostgreSQL. This file pins the application's half, on any database.

Preconditions: none beyond the app importing; no database is needed except for
the middleware test, which uses the default fixtures.

Postconditions (from rbac.domain_scope's contract and 010's header):
  - ``app.user_domains`` is a JSON array of the user's domains, sorted, or ''
    exactly when rbac.domain_scope returns None (an unscoped role, no claim);
  - ``app.user_role`` is a JSON array of the user's platform roles only: a
    token that names the service marker does not carry it through;
  - no user and no declared scope encode as an empty list: nothing, not
    everything;
  - the service scope is unscoped and names the service marker, and only a
    block that declares it runs under it;
  - within a request, the code the route runs sees that caller's scope; after
    the request, the surrounding context's scope is back;
  - on a non-PostgreSQL engine the listener and the start-up check do nothing.

Undefined, and not asserted: the ``label`` text, which is for logs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from src.db.scope import (
    NO_SCOPE,
    SERVICE_ROLE,
    assert_rls_binds,
    bind_scope,
    current_scope,
    install_scope_listener,
    rls_bypass_reasons,
    service_scope,
)
from src.middleware.rbac import scope_for_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _settings(user: dict[str, Any] | None) -> tuple[Any, list[str]]:
    encoded = scope_for_user(user).settings()
    domains = None if encoded["domains"] == "" else json.loads(encoded["domains"])
    return domains, json.loads(encoded["roles"])


# ── Encoding ──────────────────────────────────────────────────────────────────


def test_a_scoped_user_sends_its_domains_as_a_sorted_json_array() -> None:
    domains, roles = _settings({"roles": ["analyst"], "domains": ["beta", "alpha"]})
    assert domains == ["alpha", "beta"]
    assert roles == ["analyst"]


def test_a_domain_name_with_a_comma_stays_one_element() -> None:
    domains, _ = _settings({"roles": ["analyst"], "domains": ["a,b"]})
    assert domains == ["a,b"]


@pytest.mark.parametrize("role", ["governance_officer", "platform_engineer"])
def test_only_an_unscoped_role_without_a_claim_sends_no_domain_list(role: str) -> None:
    domains, roles = _settings({"roles": [role]})
    assert domains is None
    assert roles == [role]


@pytest.mark.parametrize("role", ["governance_officer", "platform_engineer"])
def test_a_claim_binds_the_unscoped_roles(role: str) -> None:
    domains, _ = _settings({"roles": [role], "domains": ["alpha"]})
    assert domains == ["alpha"]


@pytest.mark.parametrize(
    "user",
    [
        {"roles": ["viewer"]},
        {"roles": ["analyst"], "domains": []},
        {"roles": ["analyst"], "domains": 42},
        {"roles": ["governance_officer"], "domains": []},
    ],
)
def test_no_domain_is_an_empty_list_not_an_absent_one(user: dict[str, Any]) -> None:
    domains, _ = _settings(user)
    assert domains == []


def test_a_token_cannot_carry_the_service_marker_or_unknown_roles() -> None:
    domains, roles = _settings({"roles": [SERVICE_ROLE, "root", "analyst"]})
    assert roles == ["analyst"]
    assert domains == []


def test_no_user_is_nothing() -> None:
    assert _settings(None) == ([], [])
    assert json.loads(NO_SCOPE.settings()["domains"]) == []
    assert json.loads(NO_SCOPE.settings()["roles"]) == []


# ── Binding ───────────────────────────────────────────────────────────────────


def test_the_default_outside_any_binding_is_no_scope() -> None:
    import contextvars

    # A fresh context: nothing inherited from fixtures or other tests.
    assert contextvars.Context().run(current_scope) == NO_SCOPE


def test_the_service_scope_is_unscoped_and_only_inside_its_block() -> None:
    with bind_scope(NO_SCOPE):
        with service_scope("unit test") as scope:
            assert current_scope() is scope
            assert scope.settings() == {"domains": "", "roles": json.dumps([SERVICE_ROLE])}
        assert current_scope() == NO_SCOPE


# ── Per request ───────────────────────────────────────────────────────────────


_router = APIRouter()
_seen: list[dict[str, str]] = []


@_router.get("/api/test-only/db-scope")
async def _capture() -> dict[str, str]:
    _seen.append(current_scope().settings())
    return {}


@pytest.mark.asyncio
async def test_a_request_runs_under_its_callers_scope(db_engine: AsyncEngine) -> None:
    from src.api.main import app

    user = {"uid": "scope-test", "roles": ["analyst"], "domains": ["alpha"]}
    app.include_router(_router)
    try:
        with (
            patch(
                "src.middleware.auth._verify_firebase_token",
                new=AsyncMock(return_value=user),
            ),
            bind_scope(NO_SCOPE),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                ok = await client.get(
                    "/api/test-only/db-scope", headers={"Authorization": "Bearer t"}
                )
            assert current_scope() == NO_SCOPE
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/test-only/db-scope"
        ]
    assert ok.status_code == 200, ok.text
    assert _seen[-1] == {"domains": '["alpha"]', "roles": '["analyst"]'}


# ── Not PostgreSQL ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqlite_gets_no_listener_and_passes_the_start_up_check() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.db import scope as scope_module

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        install_scope_listener(engine)
        assert not event.contains(engine.sync_engine, "begin", scope_module._apply_scope)
        assert await rls_bypass_reasons(engine) == []
        await assert_rls_binds(engine)
    finally:
        await engine.dispose()

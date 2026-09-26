"""
The database enforces domain isolation itself, behind the application.

Written for septa-47 (register entry `rls-policies-bind-nothing`). Migration 001
declared row-level-security policies on causal_entities and causal_edges, but no
code set the settings they read, the app connected as the tables' owner (whom
RLS does not bind), and graph_fragments had no policy. Migration
010_rls_binds.sql replaces the policies and adds graph_fragments';
src/db/scope.py sets ``app.user_domains`` and ``app.user_role`` at the start of
every transaction from the scope the auth middleware binds per request
(rbac.scope_for_user); the app connects as causal_app.

Preconditions: TEST_DATABASE_URL names a PostgreSQL server. The db_engine
fixture is then a database built from src/db/migrations/*.sql and connected as
causal_app (tests/conftest.py); db_session is the owner's connection and seeds
the rows. Without PostgreSQL there is no row-level security to test, so every
test here is skipped on SQLite -- and the gate's backend-pytest-postgres step,
which runs them, fails on any skip.

The seed: in each of the domains ``alpha``, ``beta`` and ``a,b`` (a comma in the
name, which 001's comma-split parsing got wrong), one entity, one edge and one
fragment.

Postconditions, all over raw SQL that has no domain filter of its own (so the
application's filter in the registry is not what is being tested):

  - the app's connection is causal_app: not a superuser, not BYPASSRLS, not the
    owner of the three tables;
  - a user whose token grants ``alpha`` selects rows of ``alpha`` only, from
    each of the three tables; one granted ``a,b`` sees ``a,b`` and not ``alpha``;
  - governance_officer and platform_engineer without a ``domains`` claim select
    every row; with a claim, they are bound by it like everyone else;
  - an empty ``domains`` list, a viewer with no claim, no scope at all (code
    outside a request that declared none), and a token that names the service
    marker as its role each select nothing;
  - the declared service scope selects every row;
  - a scoped user cannot insert a row into another domain, and its UPDATE of
    another domain's rows touches none;
  - the scope is transaction-local: a pooled connection carries nothing into
    the next transaction;
  - through the HTTP API, with the application's own domain filter switched off,
    the list and read routes still return nothing outside the caller's domains:
    the middleware's scope reaches the database;
  - the start-up check accepts the causal_app connection and refuses the owner's.

Negative controls (the test bites): with the policy dropped from a table, with
row-level security disabled on it, or connected as the owner, the same
isolation check reports rows of another domain.

Undefined, and not asserted: row order, and which error text PostgreSQL uses for
a refused insert beyond its being a row-level-security violation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from src.db.scope import (
    NO_SCOPE,
    RLS_TABLES,
    RlsBypassError,
    assert_rls_binds,
    bind_scope,
    install_scope_listener,
    rls_bypass_reasons,
    service_scope,
)
from src.middleware.rbac import scope_for_user
from tests.conftest import postgres_only

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from src.db.scope import DbScope

pytestmark = [pytest.mark.asyncio, postgres_only]

DOMAINS = ("alpha", "beta", "a,b")


def _user(roles: list[str], domains: Any = None, *, claim: bool = True) -> dict[str, Any]:
    user: dict[str, Any] = {"uid": f"rls-{'-'.join(roles) or 'none'}", "roles": roles}
    if claim:
        user["domains"] = domains
    return user


ANALYST_ALPHA = _user(["analyst"], ["alpha"])
_CREATED_BY = '{"id": "u1", "email": "u1@test.com", "name": "U One"}'


# ── Seeding (as the owner, whom RLS does not bind) ───────────────────────────


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> dict[str, dict[str, UUID]]:
    """One entity, edge and fragment per domain. Returns {domain: {table: id}}."""
    ids: dict[str, dict[str, UUID]] = {}
    for domain in DOMAINS:
        verdict, entity, edge, fragment = uuid4(), uuid4(), uuid4(), uuid4()
        await db_session.execute(
            text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict) VALUES (:id, 'rls-test', 'SEED', 'h', 'APPROVED')"
            ),
            {"id": verdict},
        )
        await db_session.execute(
            text(
                "INSERT INTO causal_entities (id, name, entity_type, domain, created_by) "
                "VALUES (:id, :name, 'SERVICE', :domain, CAST(:by AS JSONB))"
            ),
            {"id": entity, "name": f"svc-{domain}", "domain": domain, "by": _CREATED_BY},
        )
        await db_session.execute(
            text(
                "INSERT INTO causal_edges (id, source_id, source_type, target_id, "
                "target_type, edge_type, confidence, guardian_verdict_ref, domain) "
                "VALUES (:id, :src, 'CausalEntity', :tgt, 'Outcome', 'CAUSES', 0.5, "
                ":verdict, :domain)"
            ),
            {"id": edge, "src": entity, "tgt": uuid4(), "verdict": verdict, "domain": domain},
        )
        await db_session.execute(
            text(
                "INSERT INTO graph_fragments (id, name, domain, created_by, approval_status, "
                "guardian_verdict_ref) "
                "VALUES (:id, :name, :domain, CAST(:by AS JSONB), 'APPROVED', :verdict)"
            ),
            {
                "id": fragment,
                "name": f"frag-{domain}",
                "domain": domain,
                "by": _CREATED_BY,
                "verdict": verdict,
            },
        )
        ids[domain] = {
            "causal_entities": entity,
            "causal_edges": edge,
            "graph_fragments": fragment,
        }
    await db_session.commit()
    return ids


# ── The check itself ──────────────────────────────────────────────────────────


async def _visible(conn: AsyncConnection) -> dict[str, set[str]]:
    """Which domains each RLS table shows this connection, by raw unfiltered SQL."""
    seen: dict[str, set[str]] = {}
    for table in RLS_TABLES:
        # Table names from a fixed tuple, not input.
        rows = await conn.execute(text(f"SELECT domain FROM {table}"))  # noqa: S608
        seen[table] = {r[0] for r in rows}
    return seen


def _leaks(seen: dict[str, set[str]], allowed: set[str]) -> list[str]:
    """Every (table, domain) visible outside `allowed`."""
    return sorted(f"{t}:{d}" for t, domains in seen.items() for d in domains - allowed)


async def _visible_as(engine: AsyncEngine, scope: DbScope) -> dict[str, set[str]]:
    """What the app's connection sees under `scope`, set exactly as the app sets it."""
    with bind_scope(scope):
        async with engine.connect() as conn:
            seen = await _visible(conn)
            await conn.rollback()
    return seen


ALL_OF_THEM = {t: set(DOMAINS) for t in RLS_TABLES}
NOTHING: dict[str, set[str]] = {t: set() for t in RLS_TABLES}


# ── The connection ────────────────────────────────────────────────────────────


async def test_the_app_connects_as_causal_app_which_rls_binds(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user AS name, r.rolsuper, r.rolbypassrls "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).mappings().one()
        owners = (
            await conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c WHERE c.relname = ANY(:t) "
                    "AND pg_has_role(current_user, c.relowner, 'USAGE')"
                ),
                {"t": list(RLS_TABLES)},
            )
        ).scalars().all()
    assert row["name"] == "causal_app"
    assert not row["rolsuper"]
    assert not row["rolbypassrls"]
    assert owners == []
    assert await rls_bypass_reasons(db_engine) == []


#: Tables whose rows are never revised: 001-007 and 011 revoke UPDATE and trap it.
APPEND_ONLY = {
    "audit_log",
    "verdict_consumption",
    "decision_records",
    "decision_followups",
    "causal_model_versions",
    "decision_model_refs",
    "provenance_entries",
    "provenance_erasures",
}


async def test_causal_app_may_do_what_the_app_does_and_no_more(db_engine: AsyncEngine) -> None:
    # What the app does: read and insert everywhere, update what is not
    # append-only, draw ids from the sequences. What it never does: delete,
    # truncate, or rewrite history.
    async with db_engine.connect() as conn:
        tables = (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).scalars().all()
        privileges = {
            (t, p): (
                await conn.execute(
                    text("SELECT has_table_privilege('causal_app', :t, :p)"), {"t": t, "p": p}
                )
            ).scalar_one()
            for t in tables
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
        sequences = (
            await conn.execute(
                text(
                    "SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' "
                    "AND NOT has_sequence_privilege('causal_app', "
                    "format('%I.%I', schemaname, sequencename), 'USAGE')"
                )
            )
        ).scalars().all()
    assert set(tables) >= set(RLS_TABLES) | APPEND_ONLY
    wrong = sorted(
        f"{t}:{p}={has}"
        for (t, p), has in privileges.items()
        if has
        != (
            p in ("SELECT", "INSERT") or (p == "UPDATE" and t not in APPEND_ONLY)
        )
    )
    assert wrong == []
    assert sequences == []


# ── Scoped users ──────────────────────────────────────────────────────────────


async def test_a_user_limited_to_one_domain_sees_no_row_of_another(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    seen = await _visible_as(db_engine, scope_for_user(ANALYST_ALPHA))
    assert seen == {t: {"alpha"} for t in RLS_TABLES}


async def test_a_domain_with_a_comma_is_one_domain(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    # 001 split the setting on ',', so a grant of "a,b" was read as "a" and "b".
    seen = await _visible_as(db_engine, scope_for_user(_user(["analyst"], ["a,b"])))
    assert seen == {t: {"a,b"} for t in RLS_TABLES}


async def test_by_id_another_domains_row_is_not_there(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    with bind_scope(scope_for_user(ANALYST_ALPHA)):
        async with db_engine.connect() as conn:
            for table in RLS_TABLES:
                found = (
                    await conn.execute(
                        text(f"SELECT count(*) FROM {table} WHERE id = :id"),  # noqa: S608
                        {"id": seeded["beta"][table]},
                    )
                ).scalar_one()
                assert found == 0, table


@pytest.mark.parametrize("role", ["governance_officer", "platform_engineer"])
async def test_unscoped_roles_without_a_claim_see_every_domain(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]], role: str
) -> None:
    seen = await _visible_as(db_engine, scope_for_user(_user([role], claim=False)))
    assert seen == ALL_OF_THEM


@pytest.mark.parametrize("role", ["governance_officer", "platform_engineer"])
async def test_a_claim_binds_the_unscoped_roles_too(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]], role: str
) -> None:
    seen = await _visible_as(db_engine, scope_for_user(_user([role], ["beta"])))
    assert seen == {t: {"beta"} for t in RLS_TABLES}


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(scope_for_user(_user(["analyst"], [])), id="empty-domain-list"),
        pytest.param(scope_for_user(_user(["governance_officer"], [])), id="governance-empty-list"),
        pytest.param(scope_for_user(_user(["viewer"], claim=False)), id="viewer-without-claim"),
        pytest.param(scope_for_user(_user(["analyst"], 42)), id="malformed-claim"),
        pytest.param(
            scope_for_user(_user(["causal_service"], claim=False)), id="token-names-service"
        ),
        pytest.param(scope_for_user(None), id="anonymous"),
        pytest.param(NO_SCOPE, id="no-scope-declared"),
    ],
)
async def test_these_callers_see_nothing(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]], scope: DbScope
) -> None:
    assert await _visible_as(db_engine, scope) == NOTHING


async def test_the_declared_service_scope_sees_every_domain(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    # Declared inside a block that had no scope: the declaration is what opens it.
    with bind_scope(NO_SCOPE):
        assert await _visible_as(db_engine, NO_SCOPE) == NOTHING
        with service_scope("rls test"):
            async with db_engine.connect() as conn:
                assert await _visible(conn) == ALL_OF_THEM


# ── Writes ────────────────────────────────────────────────────────────────────


async def test_a_scoped_user_cannot_write_into_another_domain(
    db_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    with bind_scope(scope_for_user(ANALYST_ALPHA)):
        async with db_engine.connect() as conn:
            with pytest.raises(DBAPIError, match="row-level security"):
                await conn.execute(
                    text(
                        "INSERT INTO graph_fragments (id, name, domain, created_by) "
                        "VALUES (:id, 'intruder', 'beta', CAST('{}' AS JSONB))"
                    ),
                    {"id": uuid4()},
                )
            await conn.rollback()
            touched = await conn.execute(
                text("UPDATE causal_entities SET description = 'defaced' WHERE domain = 'beta'")
            )
            assert touched.rowcount == 0
            await conn.commit()
    with service_scope("rls test: read back"):
        async with db_engine.connect() as conn:
            defaced = (
                await conn.execute(
                    text("SELECT count(*) FROM causal_entities WHERE description = 'defaced'")
                )
            ).scalar_one()
    assert defaced == 0


# ── Pooling ───────────────────────────────────────────────────────────────────


async def test_a_pooled_connection_carries_no_scope_into_the_next_transaction(
    postgres_test_db: Any, seeded: dict[str, dict[str, UUID]]
) -> None:
    # One connection, reused: whatever the first transaction set must be gone
    # when the second, which declares no scope, begins.
    engine = create_async_engine(postgres_test_db.app_url, pool_size=1, max_overflow=0)
    install_scope_listener(engine)
    try:
        with bind_scope(scope_for_user(_user(["governance_officer"], claim=False))):
            async with engine.begin() as conn:
                pid_first = (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                assert await _visible(conn) == ALL_OF_THEM
        with bind_scope(NO_SCOPE):
            async with engine.begin() as conn:
                pid_second = (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                assert await _visible(conn) == NOTHING
        # And outside any transaction the connection holds no scope at all.
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            leftover = await driver.fetchval(
                "SELECT current_setting('app.user_role', true)"
            )
        assert pid_first == pid_second
        assert leftover in (None, "")
    finally:
        await engine.dispose()


# ── Through the API ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def unfiltered_client(db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """The API as ANALYST_ALPHA, with the application's domain filter switched off.

    The semantic routes pass `domain_scope(user)` to the registry; patching the
    routes' reference to it to "unscoped" removes the application's line, and
    leaves the middleware -- which binds the database scope through rbac's own
    `domain_scope` -- untouched. Whatever still keeps other domains out is the
    database.
    """
    from src.api.main import app

    with (
        patch(
            "src.middleware.auth._verify_firebase_token",
            new=AsyncMock(return_value=ANALYST_ALPHA),
        ),
        patch("src.api.semantic.domain_scope", return_value=None),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer t"},
        ) as client:
            yield client


async def test_the_api_without_its_own_filter_still_isolates(
    unfiltered_client: AsyncClient, seeded: dict[str, dict[str, UUID]]
) -> None:
    entities = await unfiltered_client.get("/api/semantic/entities")
    fragments = await unfiltered_client.get("/api/semantic/fragments")
    assert entities.status_code == 200, entities.text
    assert fragments.status_code == 200, fragments.text
    assert {e["domain"] for e in entities.json()} == {"alpha"}
    assert {f["domain"] for f in fragments.json()} == {"alpha"}

    beta = seeded["beta"]
    for path in (
        f"/api/semantic/entities/{beta['causal_entities']}",
        f"/api/semantic/edges/{beta['causal_edges']}",
        f"/api/semantic/fragments/{beta['graph_fragments']}",
    ):
        response = await unfiltered_client.get(path)
        assert response.status_code == 404, (path, response.text)
    own = await unfiltered_client.get(f"/api/semantic/edges/{seeded['alpha']['causal_edges']}")
    assert own.status_code == 200, own.text


# ── Start-up ──────────────────────────────────────────────────────────────────


async def test_start_up_accepts_causal_app_and_refuses_the_owner(
    db_engine: AsyncEngine, db_owner_engine: AsyncEngine
) -> None:
    await assert_rls_binds(db_engine)
    with pytest.raises(RlsBypassError, match="superuser|owns"):
        await assert_rls_binds(db_owner_engine)


# ── Negative controls: the check above bites ─────────────────────────────────


async def _visible_as_app_role_after(
    owner: AsyncEngine, ddl: list[str], scope: DbScope
) -> dict[str, set[str]]:
    """Apply `ddl` as the owner, then look as causal_app -- all in one transaction
    that is rolled back, so the schema the rest of the suite uses is untouched."""
    async with owner.connect() as conn:
        await conn.begin()
        for statement in ddl:
            await conn.execute(text(statement))
        await conn.execute(text("SET LOCAL ROLE causal_app"))
        settings = scope.settings()
        await conn.execute(
            text(
                "SELECT set_config('app.user_domains', :domains, true), "
                "set_config('app.user_role', :roles, true)"
            ),
            settings,
        )
        seen = await _visible(conn)
        await conn.rollback()
    return seen


async def test_control_the_same_path_isolates_with_the_policies_in_place(
    db_owner_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    seen = await _visible_as_app_role_after(db_owner_engine, [], scope_for_user(ANALYST_ALPHA))
    assert _leaks(seen, {"alpha"}) == []


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_control_with_the_policy_dropped_rows_leak(
    db_owner_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]], table: str
) -> None:
    # With RLS enabled and no policy left, PostgreSQL denies by default; the
    # permissive policy an operator might add instead is what "dropped" means
    # here: domain isolation replaced by nothing.
    ddl = [
        f"DROP POLICY {table}_domain_isolation ON {table}",
        f"CREATE POLICY rls_test_open ON {table} FOR ALL USING (true) WITH CHECK (true)",
    ]
    seen = await _visible_as_app_role_after(db_owner_engine, ddl, scope_for_user(ANALYST_ALPHA))
    assert _leaks(seen, {"alpha"}) == [f"{table}:a,b", f"{table}:beta"]


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_control_with_rls_disabled_rows_leak(
    db_owner_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]], table: str
) -> None:
    ddl = [f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"]
    seen = await _visible_as_app_role_after(db_owner_engine, ddl, scope_for_user(ANALYST_ALPHA))
    assert _leaks(seen, {"alpha"}) == [f"{table}:a,b", f"{table}:beta"]


async def test_control_connected_as_the_owner_rows_leak(
    db_owner_engine: AsyncEngine, seeded: dict[str, dict[str, UUID]]
) -> None:
    # What docker-compose did before: the app on the owner's connection, with the
    # scope set exactly as the app sets it. RLS does not bind the owner.
    install_scope_listener(db_owner_engine)
    seen = await _visible_as(db_owner_engine, scope_for_user(ANALYST_ALPHA))
    assert _leaks(seen, {"alpha"}) != []

"""
The database-side half of domain isolation: which scope a transaction runs in.

The application enforces domain scope in the API and the registry's queries
(src/middleware/rbac.py). PostgreSQL enforces it a second time with row-level
security on ``causal_entities``, ``causal_edges`` and ``graph_fragments``
(migration ``010_rls_binds.sql``). The policies read two settings; this module
is the one place that sets them.

**Per request.** ``FirebaseAuthMiddleware`` binds the authenticated user's scope
(`bind_scope(rbac.scope_for_user(user))`) around the rest of the request. The
scope lives in a context variable, so it follows the request into every
coroutine, thread-pool call and task spawned from it, and into nothing else.

**Per transaction.** `install_scope_listener` hooks the engine's ``begin``
event: every transaction on a PostgreSQL connection, from a session or a raw
connection, starts with::

    SELECT set_config('app.user_domains', :domains, true),
           set_config('app.user_role',    :roles,   true)

``true`` makes both transaction-local (``SET LOCAL``): they end with the
transaction, so a pooled connection never carries one request's scope into the
next.

**The encoding** (what 010's ``app_scope_allows`` parses):

- ``app.user_domains``: a JSON array of domain names, or ``''`` for "no domain
  restriction". ``''`` is sent only when `rbac.domain_scope` returned None.
- ``app.user_role``: a JSON array of the caller's platform roles (only roles in
  `rbac.ALL_ROLES`), or ``["causal_service"]`` for a service context.

The database frees a caller from the domain list only when the list is unset
AND a role in it is ``governance_officer``, ``platform_engineer`` or
``causal_service``. So it applies rbac's rule itself: a present ``domains``
claim binds every role.

**Outside a request** the default scope is `NO_SCOPE`: an empty domain list, no
role, so the policies show nothing and refuse every write to those tables.
Code with no user behind it -- a seed script, a background job, an agent run
outside a request, the test fixtures that call the registry directly -- must
say so, with ``with service_scope("why"):``. That scope is intentionally
unscoped (every domain), and it is never the default: forgetting it fails
closed, it does not open up. An agent run *inside* a request inherits that
request's user scope, because it acts for that user.

**The connection role.** RLS binds neither a superuser, a BYPASSRLS role, nor
the tables' owner. `assert_rls_binds` checks the connected role against all
three and the app refuses to start on PostgreSQL when it fails
(src/api/main.py): the app connects as ``causal_app`` (docs/QUICKSTART.md).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event, text

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger(__name__)

#: The role marker a declared service context runs under. Not a platform role:
#: `rbac.scope_for_user` forwards only roles in rbac.ALL_ROLES, so no token can
#: carry it into the database.
SERVICE_ROLE = "causal_service"

#: The tables 010_rls_binds.sql (and 014 for the version history) put under a domain policy.
RLS_TABLES: tuple[str, ...] = (
    "causal_entities",
    "causal_edges",
    "graph_fragments",
    # 014: the version history carries each object's domain, and the same policy.
    "semantic_object_versions",
)


@dataclass(frozen=True)
class DbScope:
    """What a transaction may see: `domains` (None = no restriction) and `roles`."""

    domains: frozenset[str] | None
    roles: tuple[str, ...]
    #: For logs and errors only; the database never sees it.
    label: str

    def settings(self) -> dict[str, str]:
        """The two settings, encoded as 010's policies parse them."""
        return {
            "domains": "" if self.domains is None else json.dumps(sorted(self.domains)),
            "roles": json.dumps(list(self.roles)),
        }


#: No user, no declared service: nothing visible in the RLS tables.
NO_SCOPE = DbScope(domains=frozenset(), roles=(), label="none")

_current: ContextVar[DbScope] = ContextVar("db_scope", default=NO_SCOPE)


def current_scope() -> DbScope:
    """The scope the next transaction in this context will run under."""
    return _current.get()


@contextmanager
def bind_scope(scope: DbScope) -> Iterator[DbScope]:
    """Run the block, and everything it spawns, under `scope`."""
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def user_scope(domains: Collection[str] | None, roles: Collection[str], label: str) -> DbScope:
    """A user's scope. Build it with `rbac.scope_for_user`, not by hand."""
    return DbScope(
        domains=None if domains is None else frozenset(domains),
        roles=tuple(sorted(set(roles) - {SERVICE_ROLE})),
        label=label,
    )


@contextmanager
def service_scope(reason: str) -> Iterator[DbScope]:
    """Declare that the block acts for no user and may see every domain.

    `reason` is logged. Use it only where there is genuinely no user: seeding,
    background jobs, maintenance. A request's code never needs it.
    """
    scope = DbScope(domains=None, roles=(SERVICE_ROLE,), label=f"service:{reason}")
    log.info("db.scope.service", reason=reason)
    with bind_scope(scope) as bound:
        yield bound


_SET_SCOPE = text(
    "SELECT set_config('app.user_domains', :domains, true), "
    "set_config('app.user_role', :roles, true)"
)


def _apply_scope(conn: Connection) -> None:
    conn.execute(_SET_SCOPE, current_scope().settings())


def install_scope_listener(engine: AsyncEngine) -> None:
    """Make every transaction on `engine` start in the current scope.

    A no-op for a non-PostgreSQL engine: SQLite has no row-level security, and
    the application's own filter is the only line there.
    """
    sync_engine = engine.sync_engine
    if sync_engine.dialect.name != "postgresql":
        return
    if not event.contains(sync_engine, "begin", _apply_scope):
        event.listen(sync_engine, "begin", _apply_scope)


class RlsBypassError(RuntimeError):
    """The connected role is not bound by the row-level-security policies."""


async def rls_bypass_reasons(engine: AsyncEngine) -> list[str]:
    """Why the role `engine` connects as would bypass RLS; empty when it is bound."""
    if engine.dialect.name != "postgresql":
        return []
    reasons: list[str] = []
    async with engine.connect() as conn:
        role = (
            await conn.execute(
                text(
                    "SELECT current_user AS name, r.rolsuper, r.rolbypassrls "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).mappings().one()
        if role["rolsuper"]:
            reasons.append(f"{role['name']} is a superuser")
        if role["rolbypassrls"]:
            reasons.append(f"{role['name']} has BYPASSRLS")
        tables: list[dict[str, Any]] = [
            dict(r)
            for r in (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "pg_has_role(current_user, c.relowner, 'USAGE') AS owns "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relname = ANY(:names)"
                    ),
                    {"names": list(RLS_TABLES)},
                )
            ).mappings()
        ]
        await conn.rollback()
    for row in tables:
        if not row["relrowsecurity"]:
            reasons.append(f"row-level security is off on {row['relname']}")
        elif row["owns"] and not row["relforcerowsecurity"]:
            reasons.append(f"{role['name']} owns {row['relname']}")
    missing = set(RLS_TABLES) - {row["relname"] for row in tables}
    reasons.extend(f"{name} does not exist (migrations not applied?)" for name in sorted(missing))
    return reasons


async def assert_rls_binds(engine: AsyncEngine) -> None:
    """Refuse a PostgreSQL connection that the domain policies would not bind."""
    reasons = await rls_bypass_reasons(engine)
    if reasons:
        log.critical("db.rls.bypassed", reasons=reasons)
        raise RlsBypassError(
            "The database role the app connects as is not bound by row-level "
            "security: " + "; ".join(reasons) + ". Connect as causal_app "
            "(docs/QUICKSTART.md, 'How the app connects')."
        )

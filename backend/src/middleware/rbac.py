"""
The role matrix: which platform roles may call which route, and which domains a
user may see.

Every state-changing route of the API, and the reads that expose the audit
trail, the review queue, provenance or the semantic layer, is one row of
`ROUTE_ROLES`, keyed by its method and its full mounted path. A route takes its
check with ``Depends(guard("POST", "/api/semantic/drafts"))``; `guard` raises at
import time for a key the matrix does not hold, and
``tests/integration/test_rbac_matrix.py`` fails for a mounted state-changing
route that does not depend on its row. So a new route cannot be open by
omission: it is either in the matrix or the suite is red.

The roles are CYBERSECURITY.md §4.1's -- viewer, analyst, graph_author,
governance_officer, platform_engineer, agent -- plus ``sre``, the operational
role that decides interventions. Where §4.1 says nothing about a route, the
choice is the least privilege consistent with it, and §4.0 records it.

Domain isolation (§4.2) is application-level: the token's ``domains`` claim, a
list of domain names, bounds which semantic objects a user reads, drafts in, or
reviews. `domain_scope` returns ``None`` (no restriction) only when the claim is
absent AND the user holds a role in `UNSCOPED_ROLES`; for everyone else an
absent or malformed claim means no domain at all -- fail closed.

The database enforces the same scope a second time: `scope_for_user` turns it
into the settings row-level security reads on ``causal_entities``,
``causal_edges`` and ``graph_fragments`` (src/db/scope.py, migration
``010_rls_binds.sql``), bound per request by the auth middleware. A query that
forgets the application's filter still returns nothing outside the user's
domains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.db.scope import user_scope
from src.middleware.auth import require_role, roles_of

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from fastapi import Request

    from src.db.scope import DbScope

# ── Roles ─────────────────────────────────────────────────────────────────────

VIEWER = "viewer"
ANALYST = "analyst"
GRAPH_AUTHOR = "graph_author"
GOVERNANCE_OFFICER = "governance_officer"
PLATFORM_ENGINEER = "platform_engineer"
AGENT = "agent"
SRE = "sre"

#: Every role the platform knows. Holding none of them is holding no permission.
ALL_ROLES: tuple[str, ...] = (
    VIEWER,
    ANALYST,
    GRAPH_AUTHOR,
    GOVERNANCE_OFFICER,
    PLATFORM_ENGINEER,
    AGENT,
    SRE,
)

#: §4.1 "Graph Write: Approve/reject" and "Admin". Also the audit trail's readers.
GOVERNANCE_ROLES: tuple[str, ...] = (GOVERNANCE_OFFICER, PLATFORM_ENGINEER)
#: Who decides an intervention, and records what it did.
DECISION_ROLES: tuple[str, ...] = (GOVERNANCE_OFFICER, PLATFORM_ENGINEER, SRE)
#: §4.1 "Graph Write": propose a draft (analyst), propose + submit (graph_author),
#: draft only (agent), admin (platform_engineer). A governance officer rules on
#: drafts and does not write them.
DRAFT_ROLES: tuple[str, ...] = (ANALYST, GRAPH_AUTHOR, PLATFORM_ENGINEER, AGENT)
#: §4.1 "Governance": read-only check (agent), full (governance_officer), admin
#: (platform_engineer); a graph_author's "submit" is a check against its draft.
CHECK_ROLES: tuple[str, ...] = (GRAPH_AUTHOR, GOVERNANCE_OFFICER, PLATFORM_ENGINEER, AGENT)
#: A recorded decision is a saved run ("Simulation: Run, save" is the analyst's);
#: sre decides operationally; governance and platform roles keep what they had.
LEDGER_ROLES: tuple[str, ...] = (ANALYST, GOVERNANCE_OFFICER, PLATFORM_ENGINEER, SRE)

#: Roles that may hold no ``domains`` claim and still see every domain.
UNSCOPED_ROLES: frozenset[str] = frozenset({GOVERNANCE_OFFICER, PLATFORM_ENGINEER})
#: Roles that read unapproved semantic objects ("Read all"). A holder of none of
#: them -- a viewer -- reads APPROVED objects only.
READ_ALL_ROLES: frozenset[str] = frozenset(ALL_ROLES) - {VIEWER}

# ── The matrix ────────────────────────────────────────────────────────────────

#: (method, full path) -> the roles that may call it. One of them is enough.
ROUTE_ROLES: dict[tuple[str, str], tuple[str, ...]] = {
    # Semantic layer: reads need a platform role (viewer reads approved only, and
    # every read is bounded by the user's domains); drafting is §4.1 Graph Write.
    ("GET", "/api/semantic/entities"): ALL_ROLES,
    ("GET", "/api/semantic/entities/{entity_id}"): ALL_ROLES,
    ("GET", "/api/semantic/fragments"): ALL_ROLES,
    ("GET", "/api/semantic/fragments/{fragment_id}"): ALL_ROLES,
    ("GET", "/api/semantic/edges/{edge_id}"): ALL_ROLES,
    ("GET", "/api/semantic/edges/{edge_id}/audit-trail"): ALL_ROLES,
    ("POST", "/api/semantic/drafts"): DRAFT_ROLES,
    # Governance
    ("POST", "/api/governance/check"): CHECK_ROLES,
    ("POST", "/api/governance/approve/{object_id}"): GOVERNANCE_ROLES,
    ("POST", "/api/governance/reject/{object_id}"): GOVERNANCE_ROLES,
    ("GET", "/api/governance/review-queue"): GOVERNANCE_ROLES,
    ("GET", "/api/governance/audit-log"): GOVERNANCE_ROLES,
    # Sessions: §4.1 gives every role "Run".
    ("POST", "/api/v1/session/run"): ALL_ROLES,
    ("POST", "/api/v1/session/incident"): ALL_ROLES,
    # Platform: a probe over the request body, changing nothing.
    ("POST", "/api/v1/platform/evaluation/calibration"): ALL_ROLES,
    # Provenance: read like the audit log, and erased only by governance roles.
    ("GET", "/api/v1/provenance/trace/{session_id}"): GOVERNANCE_ROLES,
    ("GET", "/api/v1/provenance/export/{session_id}"): GOVERNANCE_ROLES,
    ("GET", "/api/v1/provenance/invocations/{session_id}"): GOVERNANCE_ROLES,
    ("DELETE", "/api/v1/provenance/session/{session_id}"): GOVERNANCE_ROLES,
    # Interventions: whoever decides one records what it did.
    ("POST", "/api/v1/interventions/feedback"): DECISION_ROLES,
    ("POST", "/api/v1/interventions/{intervention_id}/approve"): DECISION_ROLES,
    ("POST", "/api/v1/interventions/{intervention_id}/reject"): DECISION_ROLES,
    # Decision ledger
    ("POST", "/api/v1/decisions"): LEDGER_ROLES,
    ("POST", "/api/v1/decisions/{entry_digest}/followup"): LEDGER_ROLES,
    ("POST", "/api/v1/decisions/verify-anchored"): ALL_ROLES,
    # Assumption registry: a revision proposes edges (Graph Write, draft); a
    # review rules on them.
    ("POST", "/api/v1/models"): DRAFT_ROLES,
    ("POST", "/api/v1/models/{name}/versions"): DRAFT_ROLES,
    ("POST", "/api/v1/models/{name}/reviews"): GOVERNANCE_ROLES,
}

_GUARDS: dict[tuple[str, str], Callable[[Request], Any]] = {}


def guard(method: str, path: str) -> Callable[[Request], Any]:
    """The dependency that enforces the matrix row for (method, path).

    The same callable for the same key, so a test can tell which row a route
    depends on. A key the matrix does not hold is a KeyError at import time.
    """
    key = (method.upper(), path)
    roles = ROUTE_ROLES[key]
    if key not in _GUARDS:
        _GUARDS[key] = require_role(*roles)
    return _GUARDS[key]


# ── Domain scope ──────────────────────────────────────────────────────────────


def domain_scope(user: dict[str, Any]) -> frozenset[str] | None:
    """The domains `user` may see, or None for every domain.

    None only for a user with no ``domains`` claim who holds an UNSCOPED role. A
    claim present is binding for every role. Absent (for any other role) or
    malformed means the empty set: nothing is visible.
    """
    claim = user.get("domains")
    if claim is None:
        return None if UNSCOPED_ROLES & set(roles_of(user)) else frozenset()
    if isinstance(claim, str):
        return frozenset({claim}) if claim else frozenset()
    if isinstance(claim, list):
        return frozenset(d for d in claim if isinstance(d, str) and d)
    return frozenset()


def in_scope(scope: Collection[str] | None, domain: str | None) -> bool:
    """Whether an object of `domain` is inside `scope` (None = everything)."""
    return scope is None or (domain is not None and domain in scope)


def approved_only(user: dict[str, Any]) -> bool:
    """True when the user may read only APPROVED semantic objects (a viewer)."""
    return not (READ_ALL_ROLES & set(roles_of(user)))


def scope_for_user(user: dict[str, Any] | None) -> DbScope:
    """The database scope for `user`: `domain_scope` and the platform roles it holds.

    Only roles in `ALL_ROLES` are forwarded, so a token cannot name the database's
    service marker. No user is `NO_SCOPE`'s equivalent: no domain, no role.
    """
    if user is None:
        return user_scope(frozenset(), (), label="anonymous")
    roles = [r for r in roles_of(user) if r in ALL_ROLES]
    return user_scope(domain_scope(user), roles, label=f"user:{user.get('uid', '?')}")

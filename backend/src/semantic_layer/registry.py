"""
SemanticLayerRegistry — async registry for all causal semantic objects.

Rules (from DATA_LAYER.md §2.2):
  1. All writes require a Guardian APPROVED verdict.
  2. All objects are versioned — no in-place mutation; deprecate and replace.
  3. All approved objects carry lineage.
  4. Draft objects cannot participate in simulations.
  5. Deprecated objects are retained (filtered from active surfaces).

Every write method validates the guardian_verdict_ref against the audit_log
table before proceeding.  GuardianBlockedError is raised if:
  - The verdict_id does not exist in audit_log
  - The verdict is not APPROVED
  - The verdict is not signed by the Guardian (a row in an append-only log is a
    record of what happened, not an authorisation)
  - The verdict was issued for another action: a promotion needs one issued for
    GOVERNANCE_APPROVE (septa-60)
  - The verdict was issued for a different object, or for none
  - The verdict has already been spent (see verdict_consumption)

The registry never imports or calls the GovernanceAgent directly — it only
reads the audit_log table to verify verdict status.  This prevents circular
dependencies and ensures the database is the single source of truth.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import bindparam, column, select, table, text, update
from sqlalchemy.exc import IntegrityError

from src.db.factory import CloudSqlFactory
from src.db.types import (
    TEXT_ARRAY,
    TIMESTAMP,
    UUID_ARRAY,
    json_value,
    timestamp_text,
    typed_text,
)
from src.exceptions import GuardianBlockedError, InvalidTransitionError, ObjectNotFoundError
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AssumptionType,
    AuditEntry,
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    ElicitedFrom,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    ReviewStatus,
    RiskTier,
    UserRef,
)
from src.semantic_layer.review_risk import (
    RECORD_SENSITIVITY_CONTEXT,
    TIER_RANK,
    edge_risk,
    entity_risk,
    fragment_risk,
    recorded_sensitivity,
)
from src.semantic_layer.verdict import GOVERNANCE_APPROVE, RECORD_SENSITIVITY
from src.semantic_layer.verdict import claim as verdict_claim
from src.semantic_layer.verdict import verify as verdict_verify

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.expression import TableClause

    from src.semantic_layer.models import SensitivityResult

log = structlog.get_logger(__name__)


# ── Verdict validation ────────────────────────────────────────────────────────


async def _assert_verdict_approved(
    session: AsyncSession,
    verdict_id: UUID,
    subject_id: UUID,
    context: str = "write",
    expected: GuardianVerdict = GuardianVerdict.APPROVED,
    action: str | None = None,
) -> AuditEntry:
    """
    Assert that this verdict authorises *this* decision, then spend it.

    ``expected`` is APPROVED for a write. A rejection needs the same four checks
    against a REJECTED verdict: a human confirming a refusal should be acting on a
    Guardian ruling about *this* object, not on any UUID they happened to send.

    ``action``, when given, is the decision the caller is carrying out, and the
    verdict must have been issued for exactly that action: a promotion passes
    ``GOVERNANCE_APPROVE``. The action is in the signed claim, so it is read after
    the signature verifies. Before septa-60 it went unread, and an APPROVED verdict
    from a check for any action on the object could approve it.

    Four questions, all of which used to go unasked except the first two:

      1. Does the verdict exist, and does it say APPROVED?
      2. Was it issued by the Guardian? An unsigned row, or one whose signature
         does not verify, is history rather than authority -- which matters
         because two code paths insert APPROVED rows without going through the
         Guardian at all, one of them under no role requirement.
      3. Was it issued for *this* object? A verdict for object A is refused for
         object B.
      4. Has it been spent? Recorded by inserting into `verdict_consumption`,
         because audit_log is append-only and cannot be marked. The primary key
         there is what makes the second attempt fail.

    Raises GuardianBlockedError on any of them. The verdict is spent before the
    caller's write, in the caller's transaction: if the write then fails and rolls
    back, the consumption rolls back with it, so a failed write does not burn a
    verdict and a successful one cannot be repeated.
    """
    row = await session.execute(
        text(
            "SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
            "policy_rules_cited, session_id, created_at, subject_id, signature "
            "FROM audit_log WHERE id = :vid"
        ),
        {"vid": str(verdict_id)},
    )
    record = row.mappings().one_or_none()

    if record is None:
        raise GuardianBlockedError(
            reason=f"Guardian verdict {verdict_id} not found in audit_log",
            verdict_id=str(verdict_id),
        )

    verdict_status = record["guardian_verdict"]
    if verdict_status != expected:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict is {verdict_status}, not {expected.value} — "
                f"{context} blocked"
            ),
            verdict_status=verdict_status,
            verdict_id=str(verdict_id),
        )

    created_at = record["created_at"]
    statement = verdict_claim(
        verdict_id=record["id"],
        subject_id=record["subject_id"],
        payload_hash=record["payload_hash"],
        verdict=str(verdict_status),
        action_type=record["action_type"],
        agent_id=record["agent_id"],
        # TIMESTAMPTZ comes back as a datetime, SQLite's column as the text written;
        # timestamp_text renders both as the ISO text the Guardian signed.
        created_at=timestamp_text(created_at) or "",
    )
    if not verdict_verify(statement, record["signature"]):
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} is not signed by the issuer — {context} "
                "blocked. A row in audit_log is a record of what happened, not an "
                "authorisation; only the Guardian can issue one."
            ),
            verdict_id=str(verdict_id),
        )

    # After the signature, so the action read here is the one the Guardian signed.
    _assert_action(record["action_type"], action, verdict_id, context)

    issued_for = record["subject_id"]
    if not issued_for:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} names no subject, so it authorises no "
                f"write — {context} blocked"
            ),
            verdict_id=str(verdict_id),
        )
    if str(issued_for) != str(subject_id):
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} was issued for object {issued_for}, "
                f"not {subject_id} — {context} blocked"
            ),
            verdict_id=str(verdict_id),
            subject_id=str(subject_id),
        )

    try:
        await session.execute(
            typed_text(
                "INSERT INTO verdict_consumption (verdict_id, subject_id, context, consumed_at) "
                "VALUES (:vid, :sid, :ctx, :ts)",
                ts=TIMESTAMP,
            ),
            {
                "vid": str(verdict_id),
                "sid": str(subject_id),
                "ctx": context,
                "ts": datetime.now(UTC),
            },
        )
    except IntegrityError as exc:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} has already been used — {context} blocked. "
                "A verdict authorises one write; obtain a new one."
            ),
            verdict_id=str(verdict_id),
        ) from exc

    log.debug(
        "registry.verdict_verified",
        verdict_id=str(verdict_id),
        subject_id=str(subject_id),
        context=context,
    )

    return AuditEntry(
        id=UUID(str(record["id"])),
        agent_id=record["agent_id"],
        action_type=record["action_type"],
        payload_hash=record["payload_hash"],
        guardian_verdict=GuardianVerdict(record["guardian_verdict"]),
        policy_rules_cited=_as_rule_list(record["policy_rules_cited"]),
        session_id=record["session_id"],
        created_at=record["created_at"],
    )


def _assert_action(issued: Any, action: str | None, verdict_id: UUID, context: str) -> None:
    """Refuse a verdict issued for a different decision than the one being made."""
    if action is None or str(issued) == action:
        return
    raise GuardianBlockedError(
        reason=(
            f"Guardian verdict {verdict_id} was issued for action {issued}, not "
            f"{action} — {context} blocked. It needs an APPROVED verdict from /check "
            f"for action {action} on this object."
        ),
        verdict_id=str(verdict_id),
    )


#: The statuses a review may move an object out of. APPROVED and DEPRECATED are
#: terminal for approval and rejection alike.
_REVIEWABLE = ("DRAFT", "UNDER_REVIEW")


def _assert_reviewable(current: Any, object_id: UUID, object_type: str, attempted: str) -> None:
    if str(current) not in _REVIEWABLE:
        raise InvalidTransitionError(str(object_id), object_type, str(current), attempted)


def _as_lineage(value: Any) -> list[dict[str, Any]]:
    """Lineage as a list, whatever the engine handed back (JSON text or a list)."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return list(value)


def _as_rule_list(value: Any) -> list[str]:
    """Policy rules come back as a JSON string on SQLite and a list on Postgres.

    `list()` of a JSON string gives a list of single characters, which is what this
    used to return under the dev engine.
    """
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
    return [str(v) for v in value]


def _object_hash(obj: dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of a JSON-serialisable dict."""
    canonical = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _parse_json_fields(raw: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """
    Parse specified fields from JSON strings back to Python objects in-place copy.

    SQLite stores lists and dicts as JSON TEXT.  This helper reverses the
    serialisation done by _sv() so that Pydantic model_validate() receives
    native Python types.

    Fields that are already non-string (e.g. None, list, dict) are left unchanged.
    """
    result = dict(raw)
    for field in fields:
        val = result.get(field)
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass  # leave as-is; Pydantic will surface the type error
    return result


def _force_draft_status(
    draft: CausalEdge | GraphFragment | CausalEntity | Assumption,
) -> CausalEdge | GraphFragment | CausalEntity | Assumption:
    """Return the object as a DRAFT, whatever status it claimed to arrive with.

    The approval state of a semantic object is decided by the review flow, not
    by the object.  Everything the model validators inspect before permitting
    APPROVED -- `approved_by`, `approved_at`, `guardian_verdict_ref` -- comes
    from the submitter, so validation cannot be the control here; the write
    path has to be.

    The approver fields are cleared alongside the status: leaving a name and a
    timestamp on a DRAFT would put an approval in the record that never
    happened, which is the same lie one field further down.

    `Assumption` carries no approval fields and is returned untouched.
    """
    updates: dict[str, Any]
    if isinstance(draft, GraphFragment):
        # A fragment's validator requires guardian_verdict_ref when APPROVED.
        # Back at DRAFT it carries no meaning, and keeping a submitter-chosen
        # UUID there would leave a reference nothing issued.
        updates = {
            "approval_status": FragmentApprovalStatus.DRAFT,
            "guardian_verdict_ref": None,
        }
    elif isinstance(draft, CausalEdge | CausalEntity):
        updates = {
            "approval_status": ApprovalStatus.DRAFT,
            "approved_by": None,
            "approved_at": None,
        }
    else:
        return draft

    if all(getattr(draft, field) == value for field, value in updates.items()):
        return draft

    log.warning(
        "registry.draft_status_forced",
        object_type=type(draft).__name__,
        object_id=str(draft.id),
        submitted_as=_sv(draft.approval_status),
    )
    return draft.model_copy(update=updates)


def _sv(v: Any) -> Any:
    """
    Serialize a value for SQLite/PostgreSQL binding.

    - Enums: return .value (string)
    - Lists: return JSON string (SQLite stores JSON as TEXT)
    - datetime: return ISO string
    - Other: pass through
    """
    if v is None:
        return None
    if hasattr(v, "value"):  # Enum
        return v.value
    if isinstance(v, list):
        return json.dumps(v, default=str)
    if hasattr(v, "isoformat"):  # datetime/date
        return v.isoformat()
    return v


# ── Domain scope ──────────────────────────────────────────────────────────────
#
# `domains` on a read is the caller's domain scope (src.middleware.rbac.domain_scope):
# None reads every domain, a set reads only those. An object outside the scope is
# reported as not found, never as forbidden, so a scoped user cannot probe for the
# existence of another domain's objects by id.


async def _insert_assumption(
    session: AsyncSession, draft: Assumption, recorded_under: UUID | None = None
) -> None:
    """INSERT one assumption row, sensitivity result included. The caller holds the
    verdict that authorises it and commits.

    ``recorded_under`` is the RECORD_SENSITIVITY verdict the platform recorded the
    result under, and only record_edge_sensitivity passes it: it is what makes the
    result count for Guardian PR-007 and the review tiers
    (review_risk.recorded_sensitivity). propose_draft never does, so a drafter's own
    result is stored as what the drafter said and counts for nothing."""
    await session.execute(
        typed_text(
            "INSERT INTO assumptions ("
            "  id, statement, assumption_type, scope, justification, "
            "  elicited_from, elicited_by, elicited_at, review_status, domain, "
            "  sensitivity_result, sensitivity_verdict_id"
            ") VALUES ("
            "  :id, :statement, :assumption_type, :scope, :justification, "
            "  :elicited_from, :elicited_by, :elicited_at, :review_status, "
            "  :domain, :sensitivity_result, :sensitivity_verdict_id"
            ")",
            elicited_at=TIMESTAMP,
        ),
        {
            "id": str(draft.id),
            "statement": draft.statement,
            "assumption_type": _sv(draft.assumption_type),
            "scope": json.dumps([ref.model_dump() for ref in draft.scope], default=str),
            "justification": draft.justification,
            "elicited_from": _sv(draft.elicited_from),
            "elicited_by": draft.elicited_by.model_dump_json(),
            "elicited_at": draft.elicited_at,
            "review_status": _sv(draft.review_status),
            "domain": draft.domain,
            # What Guardian PR-007 reads for the edges resting on this assumption --
            # counted only with the verdict it was recorded under, below.
            "sensitivity_result": (
                draft.sensitivity_result.model_dump_json()
                if draft.sensitivity_result is not None
                else None
            ),
            "sensitivity_verdict_id": str(recorded_under) if recorded_under else None,
        },
    )


def _domain_filter(
    tbl: TableClause, domains: Collection[str] | None, params: dict[str, Any]
) -> list[Any]:
    """The WHERE clauses restricting `tbl.domain` to `domains`, binding into `params`.

    None when unscoped. An empty scope compiles to an IN over the empty set, which
    matches nothing.
    """
    if domains is None:
        return []
    params["scope"] = sorted(domains)
    return [tbl.c.domain.in_(bindparam("scope", expanding=True))]


# ── Tables ────────────────────────────────────────────────────────────────────
#
# Lightweight SQLAlchemy Core tables (no ORM, no metadata): the queries below that
# vary by filter or by object type are built from these, so a table or column
# name comes from this module and every value is a bound parameter. No statement
# is assembled from strings (bandit B608 is enforced over backend/src).
#
# The columns are untyped and every value goes through a named bindparam() given
# at execution, exactly as with text(): a literal compared to a column would take
# a type from the Python value, and asyncpg would then render a cast (`$1::VARCHAR`)
# that PostgreSQL refuses against the JSONB and UUID columns of 001_initial.sql.


def _table(name: str, *columns: str) -> TableClause:
    return table(name, *(column(c) for c in columns))


_ENTITY_COLUMNS = (
    "id", "name", "entity_type", "domain", "description", "attributes",
    "ontology_version", "approval_status", "created_by", "created_at",
    "approved_at", "approved_by", "lineage", "tags",
)
_FRAGMENT_COLUMNS = (
    "id", "name", "domain", "description", "edge_ids", "entry_nodes",
    "exit_nodes", "approval_status", "version", "usage_count", "tags",
    "guardian_verdict_ref", "created_by", "created_at", "approved_at", "approved_by",
)
_ASSUMPTION_COLUMNS = (
    "id", "statement", "assumption_type", "scope", "justification",
    "elicited_from", "elicited_by", "elicited_at", "review_status",
    "last_reviewed_at", "next_review_due", "sensitivity_result", "domain",
)

_CAUSAL_ENTITIES = _table("causal_entities", *_ENTITY_COLUMNS, "guardian_verdict_ref")
_GRAPH_FRAGMENTS = _table("graph_fragments", *_FRAGMENT_COLUMNS, "lineage")
_ASSUMPTIONS = _table("assumptions", *_ASSUMPTION_COLUMNS)
_CAUSAL_EDGES = _table(
    "causal_edges", "id", "edge_type", "domain", "approval_status", "created_at",
    "approved_at", "approved_by", "guardian_verdict_ref", "lineage", "confidence",
    "assumption_refs", "created_by",
)

#: The object types a review may approve or reject: their table, and the column
#: the review queue shows as the object's name. A closed mapping; an object_type
#: outside it is refused before any SQL is built.
_REVIEWABLE_TABLES: dict[str, tuple[TableClause, str]] = {
    "CausalEdge": (_CAUSAL_EDGES, "edge_type"),
    "GraphFragment": (_GRAPH_FRAGMENTS, "name"),
    "CausalEntity": (_CAUSAL_ENTITIES, "name"),
}

#: What the review queue reads from each reviewable table beyond the common columns:
#: the fields its risk tier is derived from, and who created the object.
_QUEUE_COLUMNS: dict[str, tuple[str, ...]] = {
    "CausalEdge": ("confidence", "assumption_refs", "created_by"),
    "GraphFragment": ("edge_ids", "created_by"),
    "CausalEntity": ("created_by",),
}


def _moment(value: str | None) -> datetime:
    """A queue item's created_at as an aware datetime, for ordering; missing sorts last."""
    if not value:
        return datetime.max.replace(tzinfo=UTC)
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _outside(domains: Collection[str] | None, domain: Any) -> bool:
    return domains is not None and domain not in domains


# ── Registry ──────────────────────────────────────────────────────────────────


class SemanticLayerRegistry:
    """
    Async registry for causal semantic objects backed by the CloudSqlFactory.

    All methods use the session factory from CloudSqlFactory — no direct
    connection instantiation.

    Thread-safety: each async call acquires its own session from the pool.
    The registry object itself is stateless and can be shared freely.
    """

    # ── Entity ────────────────────────────────────────────────────────────────

    async def get_entity(
        self, entity_id: UUID, domains: Collection[str] | None = None
    ) -> CausalEntity:
        """Fetch a CausalEntity by ID, if it is inside `domains`."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT id, name, entity_type, domain, description, attributes, "
                    "ontology_version, approval_status, created_by, created_at, "
                    "approved_at, approved_by, lineage, tags "
                    "FROM causal_entities WHERE id = :eid"
                ),
                {"eid": str(entity_id)},
            )
            record = row.mappings().one_or_none()
        if record is None or _outside(domains, record["domain"]):
            raise ObjectNotFoundError(str(entity_id), "CausalEntity")
        raw = _parse_json_fields(
            dict(record), ["attributes", "lineage", "tags", "created_by", "approved_by"]
        )
        return CausalEntity.model_validate(raw)

    async def list_entities(
        self,
        domain: str | None = None,
        status: ApprovalStatus | None = None,
        domains: Collection[str] | None = None,
    ) -> Sequence[CausalEntity]:
        """List CausalEntities inside `domains`, optionally filtered by domain and/or status."""
        t = _CAUSAL_ENTITIES
        params: dict[str, Any] = {}
        filters = _domain_filter(t, domains, params)
        if domain:
            filters.append(t.c.domain == bindparam("domain"))
            params["domain"] = domain
        if status:
            filters.append(t.c.approval_status == bindparam("status"))
            params["status"] = status.value

        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                select(*(t.c[c] for c in _ENTITY_COLUMNS))
                .where(*filters)
                .order_by(t.c.created_at.desc()),
                params,
            )
            return [
                CausalEntity.model_validate(
                    _parse_json_fields(
                        dict(r), ["attributes", "lineage", "tags", "created_by", "approved_by"]
                    )
                )
                for r in rows.mappings()
            ]

    # ── Edge ─────────────────────────────────────────────────────────────────

    async def get_edge(
        self, edge_id: UUID, domains: Collection[str] | None = None
    ) -> CausalEdge:
        """Fetch a CausalEdge by ID, if it is inside `domains`."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT id, source_id, source_type, target_id, target_type, "
                    "edge_type, direction, confidence, evidence_refs, assumption_refs, "
                    "approval_status, approved_by, approved_at, guardian_verdict_ref, "
                    "version, lineage, domain, created_at, created_by "
                    "FROM causal_edges WHERE id = :eid"
                ),
                {"eid": str(edge_id)},
            )
            record = row.mappings().one_or_none()
        if record is None or _outside(domains, record["domain"]):
            raise ObjectNotFoundError(str(edge_id), "CausalEdge")
        # Reconstruct nested objects and parse JSON fields
        raw = dict(record)
        raw["source"] = {"id": raw.pop("source_id"), "object_type": raw.pop("source_type")}
        raw["target"] = {"id": raw.pop("target_id"), "object_type": raw.pop("target_type")}
        raw = _parse_json_fields(
            raw,
            ["evidence_refs", "assumption_refs", "lineage", "approved_by", "created_by"],
        )
        return CausalEdge.model_validate(raw)

    async def get_edge_audit_trail(
        self, edge_id: UUID, domains: Collection[str] | None = None
    ) -> Sequence[AuditEntry]:
        """Return all audit_log entries associated with a CausalEdge inside `domains`."""
        async with CloudSqlFactory.get_session_factory()() as session:
            # Get the edge's verdict ref
            edge_row = await session.execute(
                text("SELECT guardian_verdict_ref, domain FROM causal_edges WHERE id = :eid"),
                {"eid": str(edge_id)},
            )
            record = edge_row.mappings().one_or_none()
            if record is None or _outside(domains, record["domain"]):
                raise ObjectNotFoundError(str(edge_id), "CausalEdge")

            verdict_id = record["guardian_verdict_ref"]
            rows = await session.execute(
                text(
                    "SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
                    "policy_rules_cited, session_id, created_at "
                    "FROM audit_log WHERE id = :vid ORDER BY created_at ASC"
                ),
                {"vid": str(verdict_id)},
            )
            return [
                AuditEntry.model_validate(
                    _parse_json_fields(dict(r), ["policy_rules_cited"])
                )
                for r in rows.mappings()
            ]

    # ── GraphFragment ─────────────────────────────────────────────────────────

    async def get_fragment(
        self, fragment_id: UUID, domains: Collection[str] | None = None
    ) -> GraphFragment:
        """Fetch a GraphFragment by ID, if it is inside `domains`."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT id, name, domain, description, edge_ids, entry_nodes, "
                    "exit_nodes, approval_status, version, usage_count, tags, "
                    "guardian_verdict_ref, created_by, created_at, approved_at, approved_by "
                    "FROM graph_fragments WHERE id = :fid"
                ),
                {"fid": str(fragment_id)},
            )
            record = row.mappings().one_or_none()
        if record is None or _outside(domains, record["domain"]):
            raise ObjectNotFoundError(str(fragment_id), "GraphFragment")
        raw = dict(record)
        raw["edges"] = raw.pop("edge_ids", []) or []
        raw = _parse_json_fields(
            raw, ["edges", "entry_nodes", "exit_nodes", "tags", "created_by", "approved_by"]
        )
        return GraphFragment.model_validate(raw)

    async def list_fragments(
        self,
        domain: str | None = None,
        status: FragmentApprovalStatus | None = None,
        domains: Collection[str] | None = None,
    ) -> Sequence[GraphFragment]:
        """List GraphFragments inside `domains`, filtered by domain and/or approval status."""
        t = _GRAPH_FRAGMENTS
        params: dict[str, Any] = {}
        filters = _domain_filter(t, domains, params)
        if domain:
            filters.append(t.c.domain == bindparam("domain"))
            params["domain"] = domain
        if status:
            filters.append(t.c.approval_status == bindparam("status"))
            params["status"] = status.value

        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                select(*(t.c[c] for c in _FRAGMENT_COLUMNS))
                .where(*filters)
                .order_by(t.c.created_at.desc()),
                params,
            )
            results = []
            for r in rows.mappings():
                raw = dict(r)
                raw["edges"] = raw.pop("edge_ids", []) or []
                raw = _parse_json_fields(
                    raw,
                    ["edges", "entry_nodes", "exit_nodes", "tags", "created_by", "approved_by"],
                )
                results.append(GraphFragment.model_validate(raw))
        return results

    # ── Assumption ────────────────────────────────────────────────────────────

    async def get_assumption(self, assumption_id: UUID) -> Assumption:
        """Fetch an Assumption by ID."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT id, statement, assumption_type, scope, justification, "
                    "elicited_from, elicited_by, elicited_at, review_status, "
                    "last_reviewed_at, next_review_due, sensitivity_result, domain "
                    "FROM assumptions WHERE id = :aid"
                ),
                {"aid": str(assumption_id)},
            )
            record = row.mappings().one_or_none()
        if record is None:
            raise ObjectNotFoundError(str(assumption_id), "Assumption")
        raw = _parse_json_fields(dict(record), ["scope", "elicited_by", "sensitivity_result"])
        return Assumption.model_validate(raw)

    async def list_pending_assumptions(self, domain: str | None = None) -> Sequence[Assumption]:
        """List assumptions in PENDING review status, for governance queue."""
        t = _ASSUMPTIONS
        params: dict[str, Any] = {"status": ReviewStatus.PENDING.value}
        filters = [t.c.review_status == bindparam("status")]
        if domain:
            filters.append(t.c.domain == bindparam("domain"))
            params["domain"] = domain
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                select(*(t.c[c] for c in _ASSUMPTION_COLUMNS))
                .where(*filters)
                .order_by(t.c.elicited_at.asc()),
                params,
            )
            return [
                Assumption.model_validate(
                    _parse_json_fields(dict(r), ["scope", "elicited_by", "sensitivity_result"])
                )
                for r in rows.mappings()
            ]

    # ── Draft proposal ────────────────────────────────────────────────────────

    async def propose_draft(
        self,
        draft: CausalEdge | GraphFragment | CausalEntity | Assumption,
        verdict_id: UUID,
    ) -> UUID:
        """
        Persist a draft semantic object after verifying the Guardian verdict.

        The verdict must be APPROVED.  The object is persisted as a DRAFT
        whatever status it arrived with — promotion is a *separate*
        promote_to_approved call after human review.

        That is enforced here rather than trusted from the caller, because the
        approval fields travel inside the submitted object: `approval_status`,
        and the `approved_by` / `approved_at` / `guardian_verdict_ref` the model
        validators ask for before allowing APPROVED are all chosen by whoever
        submitted it.  A verdict authorises *a write*; it does not license the
        object to say what is written.  See
        tests/guardian/test_draft_cannot_self_approve.py.

        Returns the UUID of the newly created object.
        """
        draft = _force_draft_status(draft)

        async with CloudSqlFactory.get_session_factory()() as session:
            # Verify verdict
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=draft.id,
                context=f"propose_draft({type(draft).__name__})",
            )

            obj_id = draft.id

            if isinstance(draft, CausalEdge):
                await session.execute(
                    typed_text(
                        "INSERT INTO causal_edges ("
                        "  id, source_id, source_type, target_id, target_type, "
                        "  edge_type, direction, confidence, evidence_refs, "
                        "  assumption_refs, approval_status, guardian_verdict_ref, "
                        "  version, lineage, domain, created_at, created_by"
                        ") VALUES ("
                        "  :id, :source_id, :source_type, :target_id, :target_type, "
                        "  :edge_type, :direction, :confidence, :evidence_refs, "
                        "  :assumption_refs, :approval_status, :verdict_ref, "
                        "  :version, :lineage, :domain, :created_at, :created_by"
                        ")",
                        evidence_refs=UUID_ARRAY,
                        assumption_refs=UUID_ARRAY,
                        created_at=TIMESTAMP,
                    ),
                    {
                        "id": str(obj_id),
                        "source_id": str(draft.source.id),
                        "source_type": _sv(draft.source.object_type),
                        "target_id": str(draft.target.id),
                        "target_type": _sv(draft.target.object_type),
                        "edge_type": _sv(draft.edge_type),
                        "direction": _sv(draft.direction),
                        "confidence": float(draft.confidence),
                        "evidence_refs": [str(e) for e in draft.evidence_refs],
                        "assumption_refs": [str(a) for a in draft.assumption_refs],
                        "approval_status": _sv(draft.approval_status),
                        "verdict_ref": str(verdict_id),
                        "version": draft.version,
                        "lineage": json.dumps([r.model_dump() for r in draft.lineage], default=str),
                        "domain": draft.domain,
                        "created_at": datetime.now(UTC),
                        "created_by": (
                            draft.created_by.model_dump_json()
                            if draft.created_by is not None
                            else None
                        ),
                    },
                )

            elif isinstance(draft, GraphFragment):
                await session.execute(
                    typed_text(
                        "INSERT INTO graph_fragments ("
                        "  id, name, domain, description, edge_ids, entry_nodes, "
                        "  exit_nodes, approval_status, version, usage_count, tags, "
                        "  guardian_verdict_ref, created_by, created_at"
                        ") VALUES ("
                        "  :id, :name, :domain, :description, :edge_ids, :entry_nodes, "
                        "  :exit_nodes, :approval_status, :version, 0, :tags, "
                        "  :verdict_ref, :created_by, :created_at"
                        ")",
                        edge_ids=UUID_ARRAY,
                        tags=TEXT_ARRAY,
                        created_at=TIMESTAMP,
                    ),
                    {
                        "id": str(obj_id),
                        "name": draft.name,
                        "domain": draft.domain,
                        "description": draft.description,
                        "edge_ids": [str(e) for e in draft.edges],
                        "entry_nodes": json.dumps(
                            [n.model_dump() for n in draft.entry_nodes], default=str
                        ),
                        "exit_nodes": json.dumps(
                            [n.model_dump() for n in draft.exit_nodes], default=str
                        ),
                        "approval_status": _sv(draft.approval_status),
                        "version": draft.version,
                        "tags": list(draft.tags),
                        "verdict_ref": str(verdict_id) if verdict_id else None,
                        "created_by": draft.created_by.model_dump_json(),
                        "created_at": draft.created_at,
                    },
                )

            elif isinstance(draft, CausalEntity):
                await session.execute(
                    typed_text(
                        "INSERT INTO causal_entities ("
                        "  id, name, entity_type, domain, description, attributes, "
                        "  ontology_version, approval_status, created_by, created_at, tags"
                        ") VALUES ("
                        "  :id, :name, :entity_type, :domain, :description, "
                        "  :attributes, :ontology_version, :approval_status, "
                        "  :created_by, :created_at, :tags"
                        ")",
                        created_at=TIMESTAMP,
                        tags=TEXT_ARRAY,
                    ),
                    {
                        "id": str(obj_id),
                        "name": draft.name,
                        "entity_type": _sv(draft.entity_type),
                        "domain": draft.domain,
                        "description": draft.description,
                        "attributes": json.dumps(
                            {k: v.model_dump() for k, v in draft.attributes.items()},
                            default=str,
                        ),
                        "ontology_version": draft.ontology_version,
                        "approval_status": _sv(draft.approval_status),
                        "created_by": draft.created_by.model_dump_json(),
                        "created_at": draft.created_at,
                        "tags": list(draft.tags),
                    },
                )

            elif isinstance(draft, Assumption):
                await _insert_assumption(session, draft)

            else:
                raise TypeError(f"Unsupported draft type: {type(draft).__name__}")

            await session.commit()

        log.info(
            "registry.draft.proposed",
            object_type=type(draft).__name__,
            object_id=str(obj_id),
            verdict_id=str(verdict_id),
        )
        return obj_id

    async def promote_to_approved(
        self,
        object_id: UUID,
        object_type: str,
        verdict_id: UUID,
        approver: UserRef,
        domains: Collection[str] | None = None,
    ) -> None:
        """
        Promote a DRAFT / UNDER_REVIEW object to APPROVED status.

        Verifies the Guardian verdict before making any changes.
        Records an approval lineage entry on the object.

        object_type must be one of: "CausalEdge", "GraphFragment", "CausalEntity".
        """
        if object_type not in _REVIEWABLE_TABLES:
            raise ValueError(f"promote_to_approved: unknown object_type '{object_type}'")

        t, _ = _REVIEWABLE_TABLES[object_type]

        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=object_id,
                context=f"promote_to_approved({object_type})",
                action=GOVERNANCE_APPROVE,
            )

            now = datetime.now(UTC)
            lineage_entry = json.dumps(
                {
                    "step_type": "APPROVED",
                    "actor_id": approver.id,
                    "timestamp": now.isoformat(),
                    "description": f"Promoted to APPROVED via verdict {verdict_id}",
                },
                default=str,
            )

            # Read, append, write. `lineage || :entry` concatenates arrays on
            # Postgres but *strings* on SQLite, where it produced '[][{...}]' --
            # not JSON, and nothing read it closely enough to notice.
            current = await session.execute(
                select(t.c.lineage, t.c.approval_status, t.c.domain).where(
                    t.c.id == bindparam("oid")
                ),
                {"oid": str(object_id)},
            )
            found = current.mappings().one_or_none()
            if found is None or _outside(domains, found["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            # Checked here, inside the session that spent the verdict, so raising
            # rolls the spend back too. The UPDATE below used to match on id alone,
            # so a rejected (DEPRECATED) object could be re-promoted by any fresh
            # APPROVED verdict issued for it.
            _assert_reviewable(found["approval_status"], object_id, object_type, "approved")
            history = _as_lineage(found["lineage"])
            history.append(json.loads(lineage_entry))

            await session.execute(
                update(t)
                .where(
                    t.c.id == bindparam("oid"),
                    t.c.approval_status.in_(bindparam("reviewable", expanding=True)),
                )
                .values(
                    approval_status=bindparam("status"),
                    approved_at=bindparam("approved_at", type_=TIMESTAMP),
                    approved_by=bindparam("approved_by"),
                    guardian_verdict_ref=bindparam("verdict_ref"),
                    lineage=bindparam("lineage"),
                ),
                {
                    "oid": str(object_id),
                    "reviewable": list(_REVIEWABLE),
                    "status": "APPROVED",
                    "approved_at": now,
                    "approved_by": approver.model_dump_json(),
                    "verdict_ref": str(verdict_id),
                    "lineage": json.dumps(history),
                },
            )
            await session.commit()

        log.info(
            "registry.object.approved",
            object_type=object_type,
            object_id=str(object_id),
            approver_id=approver.id,
            verdict_id=str(verdict_id),
        )

    #: Rejection marks an object DEPRECATED. ApprovalStatus has no REJECTED member,
    #: and inventing one here would put a value in the column that the CHECK
    #: constraint in 001_initial.sql does not allow.
    async def reject_draft(
        self,
        object_id: UUID,
        object_type: str,
        verdict_id: UUID,
        rejector: UserRef,
        reason: str,
        domains: Collection[str] | None = None,
    ) -> None:
        """Mark a draft REJECTED (stored as DEPRECATED), against a real REJECTED verdict.

        The API has always *required* a verdict_id on rejection and never read it, so
        any UUID was accepted. It gets the same four checks approval gets -- exists,
        is the verdict it claims to be, signed by the Guardian, issued for this object,
        not already spent -- because "this was refused for a reason" is a claim about
        the record, and an unchecked one is worth nothing.
        """
        if object_type not in _REVIEWABLE_TABLES:
            raise ValueError(f"reject_draft: unknown object_type '{object_type}'")

        t, _ = _REVIEWABLE_TABLES[object_type]

        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=object_id,
                context=f"reject_draft({object_type})",
                expected=GuardianVerdict.REJECTED,
            )

            now = datetime.now(UTC)
            current = await session.execute(
                select(t.c.lineage, t.c.approval_status, t.c.domain).where(
                    t.c.id == bindparam("oid")
                ),
                {"oid": str(object_id)},
            )
            found = current.mappings().one_or_none()
            if found is None or _outside(domains, found["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            # Without this the guarded UPDATE below matched nothing on an APPROVED
            # object, yet the verdict was spent and the rejection logged as done.
            _assert_reviewable(found["approval_status"], object_id, object_type, "rejected")
            history = _as_lineage(found["lineage"])
            history.append(
                {
                    "step_type": "REJECTED",
                    "actor_id": rejector.id,
                    "timestamp": now.isoformat(),
                    "description": f"Rejected via verdict {verdict_id}: {reason}",
                }
            )

            await session.execute(
                update(t)
                .where(
                    t.c.id == bindparam("oid"),
                    t.c.approval_status.in_(bindparam("reviewable", expanding=True)),
                )
                .values(approval_status=bindparam("status"), lineage=bindparam("lineage")),
                {
                    "oid": str(object_id),
                    "reviewable": list(_REVIEWABLE),
                    "status": "DEPRECATED",
                    "lineage": json.dumps(history),
                },
            )
            await session.commit()

        log.info(
            "registry.object.rejected",
            object_type=object_type,
            object_id=str(object_id),
            rejector_id=rejector.id,
            verdict_id=str(verdict_id),
        )

    # ── The kernel's sensitivity analysis of a draft edge (septa-62) ──────────

    async def record_edge_sensitivity(
        self,
        edge_id: UUID,
        result: SensitivityResult,
        verdict_id: UUID,
        actor: UserRef,
        domains: Collection[str] | None = None,
    ) -> UUID:
        """Record a sensitivity analysis of a DRAFT / UNDER_REVIEW edge; return the new
        assumption's id.

        **Which assumption it attaches to.** An E-value or a Rosenbaum gamma* says how
        strong unmeasured confounding of the edge's source and target would have to be
        to explain the estimated effect away. That is evidence about the edge's
        IGNORABILITY assumption as tested by this analysis, so it is recorded as a
        *new* Assumption: IGNORABILITY, PENDING review, elicited from AGENT by
        ``actor``, scoped to the edge's source and target, in the edge's domain,
        carrying ``result``. Its id is appended to the edge's ``assumption_refs``,
        which is where Guardian PR-007 looks (src/agents/governance.py
        edge_sensitivity), and the edge's lineage gains a MODIFIED entry naming it.

        Nothing is rewritten in place. An existing assumption is never updated -- the
        drafter's stay what the drafter said, and an ACCEPTED one is an approved
        object -- and an APPROVED or DEPRECATED edge is refused
        (InvalidTransitionError): approval is decided on what the edge rested on when
        it was reviewed. A reviewer who REJECTS the new assumption removes the result
        from PR-007's count, as for any other.

        The write needs a Guardian APPROVED verdict issued for
        :data:`RECORD_SENSITIVITY` on this edge: signed, bound to the edge and the
        action, spent here in the same transaction as the insert and the update.
        """
        t = _CAUSAL_EDGES
        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=edge_id,
                context=RECORD_SENSITIVITY_CONTEXT,
                action=RECORD_SENSITIVITY,
            )
            current = await session.execute(
                text(
                    "SELECT source_id, source_type, target_id, target_type, "
                    "assumption_refs, approval_status, lineage, domain "
                    "FROM causal_edges WHERE id = :eid"
                ),
                {"eid": str(edge_id)},
            )
            found = current.mappings().one_or_none()
            if found is None or _outside(domains, found["domain"]):
                raise ObjectNotFoundError(str(edge_id), "CausalEdge")
            _assert_reviewable(
                found["approval_status"], edge_id, "CausalEdge", "given a sensitivity result"
            )

            source = CausalObjectRef(id=found["source_id"], object_type=found["source_type"])
            target = CausalObjectRef(id=found["target_id"], object_type=found["target_type"])
            assumption = Assumption(
                statement=(
                    f"No unmeasured common cause of {source.object_type} {source.id} and "
                    f"{target.object_type} {target.id} strong enough to explain away the "
                    "effect this analysis estimated"
                ),
                assumption_type=AssumptionType.IGNORABILITY,
                scope=[source, target],
                justification=(
                    f"Sensitivity analysis of edge {edge_id} ({result.analysis_method}): "
                    f"E-value {result.e_value}, Rosenbaum gamma* {result.rosenbaum_gamma}, "
                    "robust to unmeasured confounding: "
                    f"{result.robust_to_unmeasured_confounding}"
                ),
                elicited_from=ElicitedFrom.AGENT,
                elicited_by=actor,
                review_status=ReviewStatus.PENDING,
                sensitivity_result=result,
                domain=found["domain"],
            )
            await _insert_assumption(session, assumption, recorded_under=verdict_id)

            refs = [str(r) for r in (json_value(found["assumption_refs"], []) or [])]
            history = _as_lineage(found["lineage"])
            history.append(
                {
                    "step_type": "MODIFIED",
                    "actor_id": actor.id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "description": (
                        f"Sensitivity result recorded on assumption {assumption.id} "
                        f"via verdict {verdict_id}"
                    ),
                }
            )
            await session.execute(
                update(t)
                .where(
                    t.c.id == bindparam("oid"),
                    t.c.approval_status.in_(bindparam("reviewable", expanding=True)),
                )
                .values(
                    assumption_refs=bindparam("refs", type_=UUID_ARRAY),
                    lineage=bindparam("lineage"),
                ),
                {
                    "oid": str(edge_id),
                    "reviewable": list(_REVIEWABLE),
                    "refs": [*refs, str(assumption.id)],
                    "lineage": json.dumps(history),
                },
            )
            await session.commit()

        log.info(
            "registry.edge.sensitivity_recorded",
            edge_id=str(edge_id),
            assumption_id=str(assumption.id),
            e_value=result.e_value,
            rosenbaum_gamma=result.rosenbaum_gamma,
            verdict_id=str(verdict_id),
        )
        return assumption.id

    async def list_pending_review_queue(
        self, limit: int = 50, domains: Collection[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Return the DRAFT / UNDER_REVIEW objects inside `domains`, most at risk first.

        Each dict has id, object_type, domain, name, approval_status, created_at,
        risk_tier, risk_reasons and submitted_by. The tier is derived from stored
        fields by src/semantic_layer/review_risk.py (edges by stated confidence and
        the sensitivity results the platform recorded for them, fragments by whether they carry an unapproved
        edge, entities LOW); the queue is ordered CRITICAL, HIGH, MEDIUM, LOW and
        oldest first within a tier. submitted_by is the id in the object's
        created_by, or None where none was recorded (edges drafted before 011).

        Every pending object is read before the cut to `limit`: a tier depends on the
        row, so cutting each table first could drop a CRITICAL item for a LOW one.
        """
        pending: list[dict[str, Any]] = []
        async with CloudSqlFactory.get_session_factory()() as session:
            for obj_type, (t, name_col) in _REVIEWABLE_TABLES.items():
                extra = _QUEUE_COLUMNS[obj_type]
                params: dict[str, Any] = {"reviewable": list(_REVIEWABLE)}
                rows = await session.execute(
                    select(
                        t.c.id,
                        t.c[name_col].label("name"),
                        t.c.domain,
                        t.c.approval_status,
                        t.c.created_at,
                        *(t.c[c] for c in extra),
                    ).where(
                        t.c.approval_status.in_(bindparam("reviewable", expanding=True)),
                        *_domain_filter(t, domains, params),
                    ),
                    params,
                )
                pending.extend({"object_type": obj_type, **dict(r)} for r in rows.mappings())

            sensitivity = await recorded_sensitivity(
                session,
                {
                    str(row["id"]): [
                        str(a) for a in (json_value(row["assumption_refs"], []) or [])
                    ]
                    for row in pending
                    if row["object_type"] == "CausalEdge"
                },
            )
            edge_status = await self._edge_status(session, pending, domains)

        results: list[dict[str, Any]] = []
        for row in pending:
            if row["object_type"] == "CausalEdge":
                found = sensitivity[str(row["id"])]
                tier, reasons = edge_risk(float(row["confidence"]), found)
            elif row["object_type"] == "GraphFragment":
                edge_ids = [str(e) for e in (json_value(row["edge_ids"], []) or [])]
                tier, reasons = fragment_risk(edge_ids, edge_status)
            else:
                tier, reasons = entity_risk()
            creator = json_value(row["created_by"])
            results.append(
                {
                    "id": str(row["id"]),
                    "object_type": row["object_type"],
                    "domain": row["domain"],
                    "name": row["name"],
                    "approval_status": row["approval_status"],
                    "created_at": timestamp_text(row["created_at"]),
                    "risk_tier": tier.value,
                    "risk_reasons": reasons,
                    "submitted_by": (
                        str(creator["id"])
                        if isinstance(creator, dict) and creator.get("id")
                        else None
                    ),
                }
            )

        results.sort(key=lambda x: (TIER_RANK[RiskTier(x["risk_tier"])], _moment(x["created_at"])))
        return results[:limit]

    @staticmethod
    async def _edge_status(
        session: AsyncSession, pending: list[dict[str, Any]], domains: Collection[str] | None
    ) -> dict[str, Any]:
        """The approval status of every edge a pending fragment names, inside `domains`."""
        ids = sorted(
            {
                str(e)
                for row in pending
                if row["object_type"] == "GraphFragment"
                for e in (json_value(row["edge_ids"], []) or [])
            }
        )
        if not ids:
            return {}
        t = _CAUSAL_EDGES
        params: dict[str, Any] = {"ids": ids}
        rows = await session.execute(
            select(t.c.id, t.c.approval_status).where(
                t.c.id.in_(bindparam("ids", expanding=True)),
                *_domain_filter(t, domains, params),
            ),
            params,
        )
        return {str(r["id"]): r["approval_status"] for r in rows.mappings()}

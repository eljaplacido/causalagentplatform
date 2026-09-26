"""
SemanticLayerRegistry — async registry for all causal semantic objects.

Rules (from DATA_LAYER.md §2.2):
  1. All writes require a Guardian APPROVED verdict.
  2. All objects are versioned — no in-place mutation; deprecate and replace.
  3. All approved objects carry lineage.
  4. Draft objects cannot participate in simulations.
  5. Deprecated objects are retained (filtered from active surfaces).

How rules 2 and 3 hold (ADR-001 "Versioning"; septa-14, septa-15): every state of
an object is a row in the append-only semantic_object_versions table, and the
row in the object's own table is the current version, changed only by
_record_change, which records the next revision and never rewrites one.
Approval requires the object's source data and drafter on record, and
get_lineage assembles the chain from source data to approver out of the
version history and audit_log.

Every write method validates the guardian_verdict_ref against the audit_log
table before proceeding.  GuardianBlockedError is raised if:
  - The verdict_id does not exist in audit_log
  - The verdict is not APPROVED -- for a rejection too (septa-53)
  - The verdict is not signed by the Guardian (a row in an append-only log is a
    record of what happened, not an authorisation)
  - The verdict was issued for another action: a promotion needs one issued for
    GOVERNANCE_APPROVE (septa-60), a rejection one issued for GOVERNANCE_REJECT
    (septa-53), recording an edge's sensitivity result one issued for
    RECORD_SENSITIVITY (septa-62), and a verdict for any of those decisions
    authorises only that decision
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
from decimal import Decimal
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any, cast
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
from src.exceptions import (
    GuardianBlockedError,
    InvalidTransitionError,
    LineageIncompleteError,
    ObjectNotFoundError,
)
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
    LineageActor,
    LineageApproval,
    LineageChain,
    LineageStep,
    LineageVerdict,
    ReviewStatus,
    SemanticObjectVersion,
    RiskTier,
    UserRef,
    VersionChange,
)
from src.semantic_layer.review_risk import (
    RECORD_SENSITIVITY_CONTEXT,
    TIER_RANK,
    edge_risk,
    entity_risk,
    fragment_risk,
    recorded_sensitivity,
)
from src.semantic_layer.verdict import GOVERNANCE_APPROVE, GOVERNANCE_REJECT, RECORD_SENSITIVITY
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
    action: str | None = None,
) -> AuditEntry:
    """
    Assert that this verdict authorises *this* decision, then spend it.

    ``action`` is the review decision the caller is carrying out:
    ``GOVERNANCE_APPROVE`` for a promotion, ``GOVERNANCE_REJECT`` for a rejection,
    None for a write that is not a review decision (a new draft). Every one of them
    needs an APPROVED verdict. A rejection used to need a REJECTED one, which the
    Guardian issued only when it declined the reject request -- in practice only in
    an unclassified (DISORDER) situation -- so the authority to reject hung on the
    Guardian refusing to classify (septa-53).

    The verdict must have been issued for exactly that review action. The action is
    in the signed claim, so it is read after the signature verifies. Before septa-60
    it went unread, and an APPROVED verdict from a check for any action on the
    object could approve it.

    Five questions, all of which used to go unasked except the first two:

      1. Does the verdict exist, and does it say APPROVED?
      2. Was it issued by the Guardian? An unsigned row, or one whose signature
         does not verify, is history rather than authority -- which matters
         because two code paths insert APPROVED rows without going through the
         Guardian at all, one of them under no role requirement.
      3. Was it issued for *this* decision? The action is in the signed claim. A
         promotion needs a verdict issued for GOVERNANCE_APPROVE, a rejection one
         for GOVERNANCE_REJECT, a sensitivity record one for RECORD_SENSITIVITY; a
         verdict issued for one of them authorises that decision and no other write.
      4. Was it issued for *this* object? A verdict for object A is refused for
         object B.
      5. Has it been spent? Recorded by inserting into `verdict_consumption`,
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
    if verdict_status != GuardianVerdict.APPROVED:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict is {verdict_status}, not {GuardianVerdict.APPROVED.value} — "
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


#: The actions a verdict can be bound to. Each authorises that decision only: a
#: verdict for one is never spent on another, nor on a new draft.
_BOUND_ACTIONS = frozenset({GOVERNANCE_APPROVE, GOVERNANCE_REJECT, RECORD_SENSITIVITY})


def _assert_action(issued: Any, action: str | None, verdict_id: UUID, context: str) -> None:
    """Refuse a verdict issued for a different decision than the one being made.

    A bound decision accepts only a verdict issued for exactly that action: a
    promotion GOVERNANCE_APPROVE (septa-60), a rejection GOVERNANCE_REJECT
    (septa-53), recording a sensitivity result RECORD_SENSITIVITY (septa-62). A
    write that names no action (a new draft) accepts none of them.
    """
    issued = str(issued)
    if issued == action:
        return
    if action is not None:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} was issued for action {issued}, not "
                f"{action} — {context} blocked. It needs an APPROVED verdict from /check "
                f"for action {action} on this object."
            ),
            verdict_id=str(verdict_id),
        )
    if issued in _BOUND_ACTIONS:
        raise GuardianBlockedError(
            reason=(
                f"Guardian verdict {verdict_id} was issued for action {issued}, not "
                f"a new draft — {context} blocked. A verdict for a bound decision "
                "authorises that decision and nothing else."
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
    result is stored as what the drafter said and counts for nothing.

    The row is revision 1 with the draft's declared ``source_data``; the caller
    records that revision in the version history (014)."""
    await session.execute(
        typed_text(
            "INSERT INTO assumptions ("
            "  id, statement, assumption_type, scope, justification, "
            "  elicited_from, elicited_by, elicited_at, review_status, domain, "
            "  sensitivity_result, sensitivity_verdict_id, revision, source_data"
            ") VALUES ("
            "  :id, :statement, :assumption_type, :scope, :justification, "
            "  :elicited_from, :elicited_by, :elicited_at, :review_status, "
            "  :domain, :sensitivity_result, :sensitivity_verdict_id, 1, :source_data"
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
            "source_data": (
                draft.source_data.model_dump_json() if draft.source_data is not None else None
            ),
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
    "approved_at", "approved_by", "guardian_verdict_ref", "lineage", "tags",
    "revision", "source_data",
)
_EDGE_COLUMNS = (
    "id", "source_id", "source_type", "target_id", "target_type", "edge_type",
    "direction", "confidence", "evidence_refs", "assumption_refs", "approval_status",
    "approved_by", "approved_at", "guardian_verdict_ref", "version", "lineage",
    "domain", "created_at", "created_by", "revision", "source_data",
)
_FRAGMENT_COLUMNS = (
    "id", "name", "domain", "description", "edge_ids", "entry_nodes",
    "exit_nodes", "approval_status", "version", "usage_count", "tags",
    "guardian_verdict_ref", "created_by", "created_at", "approved_at", "approved_by",
    "lineage", "revision", "source_data",
)
_ASSUMPTION_COLUMNS = (
    "id", "statement", "assumption_type", "scope", "justification",
    "elicited_from", "elicited_by", "elicited_at", "review_status",
    "last_reviewed_at", "next_review_due", "sensitivity_result", "domain",
    "sensitivity_verdict_id", "revision", "source_data",
)

_CAUSAL_ENTITIES = _table("causal_entities", *_ENTITY_COLUMNS)
_CAUSAL_EDGES = _table("causal_edges", *_EDGE_COLUMNS)
_GRAPH_FRAGMENTS = _table("graph_fragments", *_FRAGMENT_COLUMNS)
_ASSUMPTIONS = _table("assumptions", *_ASSUMPTION_COLUMNS)
_VERSIONS = _table(
    "semantic_object_versions", "object_type", "object_id", "revision", "change",
    "state", "domain", "actor", "agent_id", "agent_version", "verdict_ref", "recorded_at",
)

#: Every versioned object type: its table, every column of it (a version's
#: `state` is the row, column for column), and the columns stored as JSON -- JSONB
#: or an array on PostgreSQL, JSON text on SQLite -- which a read decodes.
_HEADS: dict[str, tuple[TableClause, tuple[str, ...], frozenset[str]]] = {
    "CausalEntity": (
        _CAUSAL_ENTITIES,
        _ENTITY_COLUMNS,
        frozenset({"attributes", "created_by", "approved_by", "lineage", "tags", "source_data"}),
    ),
    "CausalEdge": (
        _CAUSAL_EDGES,
        _EDGE_COLUMNS,
        frozenset(
            {"evidence_refs", "assumption_refs", "approved_by", "created_by", "lineage",
             "source_data"}
        ),
    ),
    "GraphFragment": (
        _GRAPH_FRAGMENTS,
        _FRAGMENT_COLUMNS,
        frozenset(
            {"edge_ids", "entry_nodes", "exit_nodes", "tags", "created_by", "approved_by",
             "lineage", "source_data"}
        ),
    ),
    "Assumption": (
        _ASSUMPTIONS,
        _ASSUMPTION_COLUMNS,
        frozenset({"scope", "elicited_by", "sensitivity_result", "source_data"}),
    ),
}

#: The object types a review may approve or reject: their table, and the column
#: the review queue shows as the object's name. A closed mapping; an object_type
#: outside it is refused before any SQL is built.
_REVIEWABLE_TABLES: dict[str, tuple[TableClause, str]] = {
    "CausalEdge": (_CAUSAL_EDGES, "edge_type"),
    "GraphFragment": (_GRAPH_FRAGMENTS, "name"),
    "CausalEntity": (_CAUSAL_ENTITIES, "name"),
}

SemanticObject = CausalEdge | GraphFragment | CausalEntity | Assumption

#: The platform build that recorded a version: the "which version of the agent"
#: half of a lineage step.
try:
    PLATFORM_VERSION = f"causal-platform-backend {_pkg_version('causal-platform-backend')}"
except PackageNotFoundError:  # pragma: no cover - only outside an installed checkout
    PLATFORM_VERSION = "causal-platform-backend (not installed)"


def _json_safe(value: Any) -> Any:
    """A stored value as JSON: times as ISO text in UTC, ids as text, numbers as numbers."""
    if isinstance(value, datetime):
        return timestamp_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def _row_state(object_type: str, row: Any) -> dict[str, Any]:
    """A head row as the JSON a version stores: every column, JSON columns decoded."""
    _, columns, json_columns = _HEADS[object_type]
    raw = dict(row)
    state = _parse_json_fields({c: raw.get(c) for c in columns}, sorted(json_columns))
    return {k: _json_safe(v) for k, v in state.items()}


def _to_model(object_type: str, row: Any) -> SemanticObject:
    """A head row, or a version's state, as its model."""
    _, columns, json_columns = _HEADS[object_type]
    raw = _parse_json_fields({c: dict(row).get(c) for c in columns}, sorted(json_columns))
    if raw.get("revision") is None:
        raw["revision"] = 1
    if object_type == "CausalEdge":
        raw["source"] = {"id": raw.pop("source_id"), "object_type": raw.pop("source_type")}
        raw["target"] = {"id": raw.pop("target_id"), "object_type": raw.pop("target_type")}
        return CausalEdge.model_validate(raw)
    if object_type == "GraphFragment":
        raw["edges"] = raw.pop("edge_ids", []) or []
        return GraphFragment.model_validate(raw)
    if object_type == "CausalEntity":
        return CausalEntity.model_validate(raw)
    return Assumption.model_validate(raw)


def _object_type_of(obj: SemanticObject) -> str:
    return type(obj).__name__


def _versioned_type(object_type: str, context: str) -> None:
    if object_type not in _HEADS:
        raise ValueError(f"{context}: unknown object_type '{object_type}'")


def _user_ref(value: Any) -> UserRef | None:
    """A stored UserRef (JSON text or a decoded dict), or None."""
    decoded = json_value(value) if isinstance(value, str | bytes) else value
    if not isinstance(decoded, dict) or not decoded.get("id"):
        return None
    return UserRef.model_validate(decoded)


def _as_version(row: Any) -> SemanticObjectVersion:
    raw = dict(row)
    return SemanticObjectVersion(
        object_type=raw["object_type"],
        object_id=UUID(str(raw["object_id"])),
        revision=int(raw["revision"]),
        change=VersionChange(raw["change"]),
        state=json_value(raw["state"], {}),
        domain=raw["domain"],
        actor=_user_ref(raw["actor"]),
        agent_id=raw["agent_id"],
        agent_version=raw["agent_version"],
        verdict_ref=UUID(str(raw["verdict_ref"])) if raw["verdict_ref"] else None,
        recorded_at=raw["recorded_at"],
    )


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


# ── Versions ──────────────────────────────────────────────────────────────────
#
# ADR-001: "All objects are versioned -- no in-place mutation; deprecate and
# replace." Every state of a semantic object is a row in semantic_object_versions
# (014_semantic_object_versions.sql), holding the object's complete row at that
# revision. The row in the object's own table is the head: the current version.
# A state change goes through _record_change and nothing else: it records the
# state being replaced if history does not hold it yet, advances the head by one
# revision (guarded on the revision it read, so a concurrent change cannot be
# overwritten), reads the new head back and records it. The version rows are
# append-only, and the database refuses a head UPDATE that does not follow this
# (the triggers in 014 and in SQLITE_DDL).


async def _read_head(
    session: AsyncSession, object_type: str, object_id: UUID
) -> dict[str, Any] | None:
    """The head row of an object, every column, or None."""
    t, columns, _ = _HEADS[object_type]
    row = await session.execute(
        select(*(t.c[c] for c in columns)).where(t.c.id == bindparam("oid")),
        {"oid": str(object_id)},
    )
    found = row.mappings().one_or_none()
    return dict(found) if found is not None else None


async def _read_versions(
    session: AsyncSession, object_type: str, object_id: UUID
) -> list[SemanticObjectVersion]:
    v = _VERSIONS
    rows = await session.execute(
        select(*v.c)
        .where(v.c.object_type == bindparam("otype"), v.c.object_id == bindparam("oid"))
        .order_by(v.c.revision.asc()),
        {"otype": object_type, "oid": str(object_id)},
    )
    return [_as_version(r) for r in rows.mappings()]


async def _insert_version(
    session: AsyncSession,
    object_type: str,
    state: dict[str, Any],
    change: VersionChange,
    *,
    actor: UserRef | None,
    agent_id: str | None,
    verdict_ref: UUID | None,
) -> None:
    """Record one version. The primary key refuses a revision recorded twice."""
    await session.execute(
        typed_text(
            "INSERT INTO semantic_object_versions (object_type, object_id, revision, change, "
            " state, domain, actor, agent_id, agent_version, verdict_ref, recorded_at) "
            "VALUES (:otype, :oid, :rev, :change, :state, :domain, :actor, :agent, "
            " :agent_version, :verdict, :ts)",
            ts=TIMESTAMP,
        ),
        {
            "otype": object_type,
            "oid": str(state["id"]),
            "rev": int(state["revision"]),
            "change": change.value,
            "state": json.dumps(state, sort_keys=True),
            "domain": state["domain"],
            "actor": actor.model_dump_json() if actor is not None else None,
            "agent": agent_id,
            "agent_version": PLATFORM_VERSION,
            "verdict": str(verdict_ref) if verdict_ref else None,
            "ts": datetime.now(UTC),
        },
    )


async def _record_if_missing(
    session: AsyncSession, object_type: str, head: dict[str, Any]
) -> None:
    """Record the head's current state if history lacks it: a row older than 014.

    It is recorded as found (change RECORDED), before anything replaces it, so the
    first change to an object written before versioning loses nothing.
    """
    v = _VERSIONS
    exists = await session.execute(
        select(v.c.revision).where(
            v.c.object_type == bindparam("otype"),
            v.c.object_id == bindparam("oid"),
            v.c.revision == bindparam("rev"),
        ),
        {"otype": object_type, "oid": str(head["id"]), "rev": int(head["revision"] or 1)},
    )
    if exists.first() is None:
        await _insert_version(
            session,
            object_type,
            _row_state(object_type, head),
            VersionChange.RECORDED,
            actor=None,
            agent_id="registry:recorded-before-first-change",
            verdict_ref=None,
        )


async def _record_change(
    session: AsyncSession,
    object_type: str,
    head: dict[str, Any],
    changes: dict[str, tuple[Any, Any]],
    *,
    change: VersionChange,
    actor: UserRef | None,
    agent_id: str | None,
    verdict_ref: UUID | None,
    attempted: str,
) -> dict[str, Any]:
    """Advance an object to a new version: the only way a head row changes.

    ``changes`` maps a column to (value, bind type or None). The UPDATE matches the
    revision ``head`` was read at, so a change made in between is not overwritten:
    nothing matches, and the caller's transaction (with its spent verdict) rolls
    back. Returns the new head's state, which is what was recorded.
    """
    t, _, _ = _HEADS[object_type]
    await _record_if_missing(session, object_type, head)
    current = int(head["revision"] or 1)
    params: dict[str, Any] = {"oid": str(head["id"]), "cur": current, "next": current + 1}
    values: dict[str, Any] = {"revision": bindparam("next")}
    for name, (value, type_) in changes.items():
        values[name] = (
            bindparam(f"v_{name}", type_=type_) if type_ else bindparam(f"v_{name}")
        )
        params[f"v_{name}"] = value
    result = await session.execute(
        update(t)
        .where(t.c.id == bindparam("oid"), t.c.revision == bindparam("cur"))
        .values(**values),
        params,
    )
    if getattr(result, "rowcount", 1) != 1:
        raise InvalidTransitionError(
            str(head["id"]), object_type, f"no longer at revision {current}", attempted
        )
    after = await _read_head(session, object_type, UUID(str(head["id"])))
    if after is None:  # pragma: no cover - the UPDATE above matched it
        raise ObjectNotFoundError(str(head["id"]), object_type)
    state = _row_state(object_type, after)
    await _insert_version(
        session, object_type, state, change, actor=actor, agent_id=agent_id,
        verdict_ref=verdict_ref,
    )
    return state


# ── Lineage ───────────────────────────────────────────────────────────────────
#
# Registry rule 3: every approved object carries lineage from its source data,
# through whoever drafted it, to its approver (septa-15). The chain is assembled
# from what the platform recorded -- the version history and audit_log -- not
# from the `lineage` list the submitter sent, which is the submitter's claim.


def _drafter_of(object_type: str, state: dict[str, Any]) -> UserRef | None:
    """Who the object's own row says drafted it (created_by, elicited_by), if anyone."""
    return _user_ref(state.get("elicited_by" if object_type == "Assumption" else "created_by"))


def _lineage_missing(
    object_type: str, head: dict[str, Any], first: SemanticObjectVersion | None
) -> list[str]:
    """What an approval needs on record before it: the source data and the drafter."""
    state = _row_state(object_type, head)
    missing: list[str] = []
    if not state.get("source_data"):
        missing.append("source data (source_data)")
    drafted = first is not None and first.revision == 1 and (
        first.actor is not None or first.agent_id
    ) and first.change == VersionChange.PROPOSED
    if not drafted and _drafter_of(object_type, state) is None:
        missing.append("drafter")
    return missing


def _signed(record: dict[str, Any]) -> bool:
    """Whether an audit_log row's Guardian signature verifies."""
    if not record.get("signature"):
        return False
    statement = verdict_claim(
        verdict_id=record["id"],
        subject_id=record["subject_id"],
        payload_hash=record["payload_hash"],
        verdict=str(record["guardian_verdict"]),
        action_type=record["action_type"],
        agent_id=record["agent_id"],
        created_at=timestamp_text(record["created_at"]) or "",
    )
    return verdict_verify(statement, record["signature"])


def _verdict_ids(state: dict[str, Any], versions: Sequence[SemanticObjectVersion]) -> set[str]:
    ids = {str(v.verdict_ref) for v in versions if v.verdict_ref}
    ids |= {str(v.state["guardian_verdict_ref"]) for v in versions
            if v.state.get("guardian_verdict_ref")}
    if state.get("guardian_verdict_ref"):
        ids.add(str(state["guardian_verdict_ref"]))
    return ids


def _approval_of(
    state: dict[str, Any], versions: Sequence[SemanticObjectVersion]
) -> LineageApproval | None:
    if state.get("approval_status") != "APPROVED":
        return None
    approvals = [v for v in versions if v.change == VersionChange.APPROVED]
    if approvals:
        last = approvals[-1]
        return LineageApproval(
            person=last.actor, at=last.recorded_at, verdict_id=last.verdict_ref,
            revision=last.revision, attributed_from="version",
        )
    # Approved before versioning: the row says who, and cannot say on what verdict.
    return LineageApproval(
        person=_user_ref(state.get("approved_by")),
        at=state.get("approved_at"),
        verdict_id=None,
        revision=None,
        attributed_from="approved_by",
    )


def _drafting_of(
    object_type: str, state: dict[str, Any], versions: Sequence[SemanticObjectVersion]
) -> LineageActor | None:
    first = versions[0] if versions else None
    if first is not None and first.revision == 1 and first.change == VersionChange.PROPOSED:
        return LineageActor(
            person=first.actor, agent_id=first.agent_id, agent_version=first.agent_version,
            at=first.recorded_at, revision=1, attributed_from="version",
        )
    person = _drafter_of(object_type, state)
    if person is None:
        return None
    at = state.get("elicited_at" if object_type == "Assumption" else "created_at")
    return LineageActor(person=person, at=at, attributed_from="created_by")


def _gaps(
    state: dict[str, Any],
    versions: Sequence[SemanticObjectVersion],
    drafted: LineageActor | None,
    approved: LineageApproval | None,
) -> list[str]:
    gaps: list[str] = []
    if not versions:
        gaps.append(
            "no version history: the object predates versioning (014) and has not "
            "changed since, so its draft and any review are not recorded"
        )
    elif versions[0].change == VersionChange.RECORDED:
        gaps.append(
            "history starts at a state recorded as found: the draft and any change "
            "before versioning (014) are not in it"
        )
    if not state.get("source_data"):
        gaps.append("no source data declared")
    if drafted is None:
        gaps.append("no drafter recorded")
    if approved is not None:
        if approved.person is None:
            gaps.append("no approver recorded")
        if approved.verdict_id is None:
            gaps.append("no verdict recorded for the approval")
    return gaps


# ── Registry ──────────────────────────────────────────────────────────────────


class SemanticLayerRegistry:
    """
    Async registry for causal semantic objects backed by the CloudSqlFactory.

    All methods use the session factory from CloudSqlFactory — no direct
    connection instantiation.

    Thread-safety: each async call acquires its own session from the pool.
    The registry object itself is stateless and can be shared freely.
    """

    # ── Reads: the head, which is the current version ─────────────────────────

    async def get_object(
        self, object_type: str, object_id: UUID, domains: Collection[str] | None = None
    ) -> SemanticObject:
        """The current version of any versioned object, if it is inside `domains`."""
        _versioned_type(object_type, "get_object")
        async with CloudSqlFactory.get_session_factory()() as session:
            head = await _read_head(session, object_type, object_id)
        if head is None or _outside(domains, head["domain"]):
            raise ObjectNotFoundError(str(object_id), object_type)
        return _to_model(object_type, head)

    async def _list(
        self,
        object_type: str,
        filters: list[Any],
        params: dict[str, Any],
        order_by: Any,
    ) -> list[Any]:
        t, columns, _ = _HEADS[object_type]
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                select(*(t.c[c] for c in columns)).where(*filters).order_by(order_by), params
            )
            return [_to_model(object_type, r) for r in rows.mappings()]

    # ── Entity ────────────────────────────────────────────────────────────────

    async def get_entity(
        self, entity_id: UUID, domains: Collection[str] | None = None
    ) -> CausalEntity:
        """Fetch a CausalEntity by ID, if it is inside `domains`."""
        entity = await self.get_object("CausalEntity", entity_id, domains)
        return cast("CausalEntity", entity)

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
        return await self._list("CausalEntity", filters, params, t.c.created_at.desc())

    # ── Edge ─────────────────────────────────────────────────────────────────

    async def get_edge(
        self, edge_id: UUID, domains: Collection[str] | None = None
    ) -> CausalEdge:
        """Fetch a CausalEdge by ID, if it is inside `domains`."""
        edge = await self.get_object("CausalEdge", edge_id, domains)
        return cast("CausalEdge", edge)

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
        fragment = await self.get_object("GraphFragment", fragment_id, domains)
        return cast("GraphFragment", fragment)

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
        return await self._list("GraphFragment", filters, params, t.c.created_at.desc())

    # ── Assumption ────────────────────────────────────────────────────────────

    async def get_assumption(self, assumption_id: UUID) -> Assumption:
        """Fetch an Assumption by ID."""
        assumption = await self.get_object("Assumption", assumption_id)
        return cast("Assumption", assumption)

    async def list_pending_assumptions(self, domain: str | None = None) -> Sequence[Assumption]:
        """List assumptions in PENDING review status, for governance queue."""
        t = _ASSUMPTIONS
        params: dict[str, Any] = {"status": ReviewStatus.PENDING.value}
        filters = [t.c.review_status == bindparam("status")]
        if domain:
            filters.append(t.c.domain == bindparam("domain"))
            params["domain"] = domain
        return await self._list("Assumption", filters, params, t.c.elicited_at.asc())

    # ── History and lineage ───────────────────────────────────────────────────

    async def get_versions(
        self, object_type: str, object_id: UUID, domains: Collection[str] | None = None
    ) -> list[SemanticObjectVersion]:
        """Every recorded version of an object, oldest first, if it is inside `domains`.

        An object written before versioning (014) that has not changed since has no
        recorded version yet: its history is empty, and its head is its only state.
        """
        _versioned_type(object_type, "get_versions")
        async with CloudSqlFactory.get_session_factory()() as session:
            head = await _read_head(session, object_type, object_id)
            if head is None or _outside(domains, head["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            versions = await _read_versions(session, object_type, object_id)
        return [v for v in versions if not _outside(domains, v.domain)]

    async def get_object_version(
        self,
        object_type: str,
        object_id: UUID,
        revision: int,
        domains: Collection[str] | None = None,
    ) -> SemanticObject:
        """The object as it was at `revision`."""
        for version in await self.get_versions(object_type, object_id, domains):
            if version.revision == revision:
                return _to_model(object_type, version.state)
        raise ObjectNotFoundError(f"{object_id}@{revision}", object_type)

    async def get_lineage(
        self, object_type: str, object_id: UUID, domains: Collection[str] | None = None
    ) -> LineageChain:
        """The object's lineage: source data, drafter, verdicts, approver, and its history.

        Built from the version history and audit_log. A link that was never
        recorded is named in ``gaps``, never inferred. ``provenance_records`` is
        left for the caller to fill from the provenance store, which is keyed by
        the sessions in ``provenance_sessions``.
        """
        _versioned_type(object_type, "get_lineage")
        async with CloudSqlFactory.get_session_factory()() as session:
            head = await _read_head(session, object_type, object_id)
            if head is None or _outside(domains, head["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            versions = await _read_versions(session, object_type, object_id)
            state = _row_state(object_type, head)
            ids = sorted(_verdict_ids(state, versions))
            # Every verdict spent on a version, and every row issued for the object:
            # checks that were never spent, and the unsigned decision records.
            # (An empty IN renders as a typed NULL that PostgreSQL will not compare
            # with a uuid, hence the second form.)
            params: dict[str, Any] = {"oid": str(object_id)}
            if ids:
                statement = text(
                    "SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
                    "policy_rules_cited, session_id, created_at, subject_id, signature, notes "
                    "FROM audit_log WHERE subject_id = :oid OR id IN :ids "
                    "ORDER BY created_at ASC"
                ).bindparams(bindparam("ids", expanding=True))
                params["ids"] = ids
            else:
                statement = text(
                    "SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
                    "policy_rules_cited, session_id, created_at, subject_id, signature, notes "
                    "FROM audit_log WHERE subject_id = :oid ORDER BY created_at ASC"
                )
            records = [dict(r) for r in (await session.execute(statement, params)).mappings()]

        spent = {str(v.verdict_ref): v.revision for v in versions if v.verdict_ref}
        verdicts = [
            LineageVerdict(
                id=UUID(str(r["id"])),
                action_type=r["action_type"],
                guardian_verdict=str(r["guardian_verdict"]),
                policy_rules_cited=_as_rule_list(r["policy_rules_cited"]),
                agent_id=r["agent_id"],
                session_id=r["session_id"],
                created_at=r["created_at"],
                signed=_signed(r),
                spent_on_revision=spent.get(str(r["id"])),
                notes=r["notes"],
            )
            for r in records
        ]
        drafted = _drafting_of(object_type, state, versions)
        approved = _approval_of(state, versions)
        gaps = _gaps(state, versions, drafted, approved)
        status_column = "review_status" if object_type == "Assumption" else "approval_status"
        return LineageChain(
            object_type=object_type,
            object_id=object_id,
            domain=state["domain"],
            revision=int(state.get("revision") or 1),
            approval_status=str(state[status_column]),
            source_data=state.get("source_data"),
            drafted_by=drafted,
            verdicts=verdicts,
            approved_by=approved,
            steps=[
                LineageStep(
                    revision=v.revision, change=v.change, actor=v.actor, agent_id=v.agent_id,
                    agent_version=v.agent_version, verdict_ref=v.verdict_ref,
                    recorded_at=v.recorded_at,
                )
                for v in versions
            ],
            provenance_sessions=sorted({v.session_id for v in verdicts if v.session_id}),
            gaps=gaps,
            complete=not gaps,
        )

    # ── Draft proposal ────────────────────────────────────────────────────────

    async def propose_draft(
        self,
        draft: CausalEdge | GraphFragment | CausalEntity | Assumption,
        verdict_id: UUID,
        *,
        submitted_by: UserRef | None = None,
        agent_id: str | None = None,
    ) -> UUID:
        """
        Persist a draft semantic object after verifying the Guardian verdict.

        The draft is revision 1, and is recorded as such in the object's version
        history (change PROPOSED) with the person who submitted it -- or, when
        the caller names nobody, the object's own created_by / elicited_by --
        the agent it came through, and the verdict. Its ``source_data`` is
        stored with it; approval later requires one (septa-15).

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
        declared = getattr(draft, "source_data", None)
        source_data = declared.model_dump_json() if declared is not None else None

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
                        "  version, lineage, domain, created_at, created_by, revision, source_data"
                        ") VALUES ("
                        "  :id, :source_id, :source_type, :target_id, :target_type, "
                        "  :edge_type, :direction, :confidence, :evidence_refs, "
                        "  :assumption_refs, :approval_status, :verdict_ref, "
                        "  :version, :lineage, :domain, :created_at, :created_by, 1, :source_data"
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
                        "source_data": source_data,
                    },
                )

            elif isinstance(draft, GraphFragment):
                await session.execute(
                    typed_text(
                        "INSERT INTO graph_fragments ("
                        "  id, name, domain, description, edge_ids, entry_nodes, "
                        "  exit_nodes, approval_status, version, usage_count, tags, "
                        "  guardian_verdict_ref, created_by, created_at, revision, source_data"
                        ") VALUES ("
                        "  :id, :name, :domain, :description, :edge_ids, :entry_nodes, "
                        "  :exit_nodes, :approval_status, :version, 0, :tags, "
                        "  :verdict_ref, :created_by, :created_at, 1, :source_data"
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
                        "source_data": source_data,
                    },
                )

            elif isinstance(draft, CausalEntity):
                await session.execute(
                    typed_text(
                        "INSERT INTO causal_entities ("
                        "  id, name, entity_type, domain, description, attributes, "
                        "  ontology_version, approval_status, created_by, created_at, tags, "
                        "  lineage, revision, source_data"
                        ") VALUES ("
                        "  :id, :name, :entity_type, :domain, :description, "
                        "  :attributes, :ontology_version, :approval_status, "
                        "  :created_by, :created_at, :tags, :lineage, 1, :source_data"
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
                        # The submitter's own lineage claim, stored as for an edge; it
                        # used to be dropped. The platform's record is the history.
                        "lineage": json.dumps(
                            [r.model_dump() for r in draft.lineage], default=str
                        ),
                        "source_data": source_data,
                    },
                )

            elif isinstance(draft, Assumption):
                await _insert_assumption(session, draft)

            else:
                raise TypeError(f"Unsupported draft type: {type(draft).__name__}")

            # Revision 1, as stored: read back so the version is the row itself.
            object_type = _object_type_of(draft)
            head = await _read_head(session, object_type, obj_id)
            if head is None:  # pragma: no cover - inserted above, same transaction
                raise ObjectNotFoundError(str(obj_id), object_type)
            await _insert_version(
                session,
                object_type,
                _row_state(object_type, head),
                VersionChange.PROPOSED,
                actor=submitted_by or _drafter_of(object_type, _row_state(object_type, head)),
                agent_id=agent_id,
                verdict_ref=verdict_id,
            )

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
        *,
        agent_id: str | None = None,
    ) -> None:
        """
        Promote a DRAFT / UNDER_REVIEW object to APPROVED: a new version, not an edit.

        Verifies the Guardian verdict before making any changes. The approved state
        is the next revision -- recorded in the version history with the approver
        and the verdict -- and the state it replaces stays in the history exactly
        as it was (ADR-001; septa-14). An APPROVED lineage step is appended to the
        object's ``lineage`` list as before.

        The object's lineage must already name its source data and its drafter,
        or the approval is refused with LineageIncompleteError (septa-15); the
        verdict is then not spent.

        object_type must be one of: "CausalEdge", "GraphFragment", "CausalEntity".
        """
        if object_type not in _REVIEWABLE_TABLES:
            raise ValueError(f"promote_to_approved: unknown object_type '{object_type}'")

        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=object_id,
                context=f"promote_to_approved({object_type})",
                action=GOVERNANCE_APPROVE,
            )

            # Checked inside the session that spent the verdict, so raising rolls the
            # spend back too.
            head = await _read_head(session, object_type, object_id)
            if head is None or _outside(domains, head["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            # A rejected (DEPRECATED) object could once be re-promoted by any fresh
            # APPROVED verdict issued for it.
            _assert_reviewable(head["approval_status"], object_id, object_type, "approved")
            versions = await _read_versions(session, object_type, object_id)
            missing = _lineage_missing(object_type, head, versions[0] if versions else None)
            if missing:
                raise LineageIncompleteError(str(object_id), object_type, missing)

            now = datetime.now(UTC)
            history = _as_lineage(head["lineage"])
            history.append(
                {
                    "step_type": "APPROVED",
                    "actor_id": approver.id,
                    "timestamp": now.isoformat(),
                    "description": f"Promoted to APPROVED via verdict {verdict_id}",
                }
            )
            await _record_change(
                session,
                object_type,
                head,
                {
                    "approval_status": ("APPROVED", None),
                    "approved_at": (now, TIMESTAMP),
                    "approved_by": (approver.model_dump_json(), None),
                    "guardian_verdict_ref": (str(verdict_id), None),
                    "lineage": (json.dumps(history), None),
                },
                change=VersionChange.APPROVED,
                actor=approver,
                agent_id=agent_id,
                verdict_ref=verdict_id,
                attempted="approved",
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
        *,
        agent_id: str | None = None,
    ) -> None:
        """Mark a draft REJECTED (stored as DEPRECATED), against a Guardian verdict.

        The verdict must be APPROVED and issued for action ``GOVERNANCE_REJECT`` on
        this object: the Guardian ruled that this rejection may go ahead, exactly as
        an approval needs a ruling that the approval may. It gets every check approval
        gets -- exists, APPROVED, signed by the Guardian, issued for this action and
        this object, not already spent. It used to need a REJECTED verdict, which only
        an unclassified (DISORDER) check produced, so a reviewer who classified the
        situation could not reject at all (septa-53).

        Like approval, the rejected state is a new revision (change REJECTED); the
        draft stays in the history as it was.
        """
        if object_type not in _REVIEWABLE_TABLES:
            raise ValueError(f"reject_draft: unknown object_type '{object_type}'")

        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=object_id,
                context=f"reject_draft({object_type})",
                action=GOVERNANCE_REJECT,
            )

            head = await _read_head(session, object_type, object_id)
            if head is None or _outside(domains, head["domain"]):
                raise ObjectNotFoundError(str(object_id), object_type)
            # Without this an APPROVED object's verdict was spent and the rejection
            # logged as done while nothing changed.
            _assert_reviewable(head["approval_status"], object_id, object_type, "rejected")
            now = datetime.now(UTC)
            history = _as_lineage(head["lineage"])
            history.append(
                {
                    "step_type": "REJECTED",
                    "actor_id": rejector.id,
                    "timestamp": now.isoformat(),
                    "description": f"Rejected via verdict {verdict_id}: {reason}",
                }
            )
            await _record_change(
                session,
                object_type,
                head,
                {
                    "approval_status": ("DEPRECATED", None),
                    "lineage": (json.dumps(history), None),
                },
                change=VersionChange.REJECTED,
                actor=rejector,
                agent_id=agent_id,
                verdict_ref=verdict_id,
                attempted="rejected",
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
        *,
        agent_id: str | None = None,
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

        Both writes are versioned (014): the new assumption is recorded as its
        revision 1 (PROPOSED, by ``actor`` through ``agent_id``, on the verdict), and
        the edge's new ``assumption_refs`` and lineage are its next revision (change
        RECORDED, the same actor, agent and verdict), the state they replace kept
        in its history as it was. Its UPDATE is guarded on the revision read here,
        so an approval or rejection in between is not overwritten.
        """
        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session,
                verdict_id,
                subject_id=edge_id,
                context=RECORD_SENSITIVITY_CONTEXT,
                action=RECORD_SENSITIVITY,
            )
            found = await _read_head(session, "CausalEdge", edge_id)
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
            made = await _read_head(session, "Assumption", assumption.id)
            if made is None:  # pragma: no cover - inserted above, same transaction
                raise ObjectNotFoundError(str(assumption.id), "Assumption")
            await _insert_version(
                session,
                "Assumption",
                _row_state("Assumption", made),
                VersionChange.PROPOSED,
                actor=actor,
                agent_id=agent_id,
                verdict_ref=verdict_id,
            )

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
            await _record_change(
                session,
                "CausalEdge",
                found,
                {
                    "assumption_refs": ([*refs, str(assumption.id)], UUID_ARRAY),
                    "lineage": (json.dumps(history), None),
                },
                change=VersionChange.RECORDED,
                actor=actor,
                agent_id=agent_id,
                verdict_ref=verdict_id,
                attempted="given a sensitivity result",
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
        the sensitivity results the platform recorded for them, fragments by
        whether they carry an unapproved edge, entities LOW); the queue is ordered
        CRITICAL, HIGH, MEDIUM, LOW and oldest first within a tier. submitted_by is
        the id in the object's created_by, or None where none was recorded (edges
        drafted before 012).

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

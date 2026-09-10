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

The registry never imports or calls the GovernanceAgent directly — it only
reads the audit_log table to verify verdict status.  This prevents circular
dependencies and ensures the database is the single source of truth.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.factory import CloudSqlFactory
from src.exceptions import GuardianBlockedError, ObjectNotFoundError
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AuditEntry,
    CausalEdge,
    CausalEntity,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    ReviewStatus,
    UserRef,
)

log = structlog.get_logger(__name__)


# ── Verdict validation ────────────────────────────────────────────────────────


async def _assert_verdict_approved(
    session: AsyncSession,
    verdict_id: UUID,
    context: str = "write",
) -> AuditEntry:
    """
    Fetch the audit_log entry and assert it has verdict=APPROVED.

    Raises GuardianBlockedError if the entry is missing or not APPROVED.
    """
    row = await session.execute(
        text(
            "SELECT id, agent_id, action_type, payload_hash, guardian_verdict, "
            "policy_rules_cited, session_id, created_at "
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
            reason=f"Guardian verdict is {verdict_status}, not APPROVED — {context} blocked",
            verdict_status=verdict_status,
            verdict_id=str(verdict_id),
        )

    log.debug(
        "registry.verdict_verified",
        verdict_id=str(verdict_id),
        context=context,
    )

    return AuditEntry(
        id=UUID(str(record["id"])),
        agent_id=record["agent_id"],
        action_type=record["action_type"],
        payload_hash=record["payload_hash"],
        guardian_verdict=GuardianVerdict(record["guardian_verdict"]),
        policy_rules_cited=list(record["policy_rules_cited"] or []),
        session_id=record["session_id"],
        created_at=record["created_at"],
    )


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

    async def get_entity(self, entity_id: UUID) -> CausalEntity:
        """Fetch a CausalEntity by ID."""
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
        if record is None:
            raise ObjectNotFoundError(str(entity_id), "CausalEntity")
        raw = _parse_json_fields(
            dict(record), ["attributes", "lineage", "tags", "created_by", "approved_by"]
        )
        return CausalEntity.model_validate(raw)

    async def list_entities(
        self,
        domain: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> Sequence[CausalEntity]:
        """List CausalEntities, optionally filtered by domain and/or status."""
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if domain:
            conditions.append("domain = :domain")
            params["domain"] = domain
        if status:
            conditions.append("approval_status = :status")
            params["status"] = status.value

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    f"SELECT id, name, entity_type, domain, description, attributes, "
                    f"ontology_version, approval_status, created_by, created_at, "
                    f"approved_at, approved_by, lineage, tags "
                    f"FROM causal_entities {where} ORDER BY created_at DESC"
                ),
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

    async def get_edge(self, edge_id: UUID) -> CausalEdge:
        """Fetch a CausalEdge by ID."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT id, source_id, source_type, target_id, target_type, "
                    "edge_type, direction, confidence, evidence_refs, assumption_refs, "
                    "approval_status, approved_by, approved_at, guardian_verdict_ref, "
                    "version, lineage, domain, created_at "
                    "FROM causal_edges WHERE id = :eid"
                ),
                {"eid": str(edge_id)},
            )
            record = row.mappings().one_or_none()
        if record is None:
            raise ObjectNotFoundError(str(edge_id), "CausalEdge")
        # Reconstruct nested objects and parse JSON fields
        raw = dict(record)
        raw["source"] = {"id": raw.pop("source_id"), "object_type": raw.pop("source_type")}
        raw["target"] = {"id": raw.pop("target_id"), "object_type": raw.pop("target_type")}
        raw = _parse_json_fields(
            raw,
            ["evidence_refs", "assumption_refs", "lineage", "approved_by"],
        )
        return CausalEdge.model_validate(raw)

    async def get_edge_audit_trail(self, edge_id: UUID) -> Sequence[AuditEntry]:
        """Return all audit_log entries associated with a CausalEdge."""
        async with CloudSqlFactory.get_session_factory()() as session:
            # Get the edge's verdict ref
            edge_row = await session.execute(
                text("SELECT guardian_verdict_ref FROM causal_edges WHERE id = :eid"),
                {"eid": str(edge_id)},
            )
            record = edge_row.mappings().one_or_none()
            if record is None:
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

    async def get_fragment(self, fragment_id: UUID) -> GraphFragment:
        """Fetch a GraphFragment by ID."""
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
        if record is None:
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
    ) -> Sequence[GraphFragment]:
        """List GraphFragments filtered by domain and/or approval status."""
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if domain:
            conditions.append("domain = :domain")
            params["domain"] = domain
        if status:
            conditions.append("approval_status = :status")
            params["status"] = status.value

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    f"SELECT id, name, domain, description, edge_ids, entry_nodes, "
                    f"exit_nodes, approval_status, version, usage_count, tags, "
                    f"guardian_verdict_ref, created_by, created_at, approved_at, approved_by "
                    f"FROM graph_fragments {where} ORDER BY created_at DESC"
                ),
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
        raw = _parse_json_fields(dict(record), ["scope", "elicited_by"])
        return Assumption.model_validate(raw)

    async def list_pending_assumptions(self, domain: str | None = None) -> Sequence[Assumption]:
        """List assumptions in PENDING review status, for governance queue."""
        params: dict[str, Any] = {"status": ReviewStatus.PENDING.value}
        where_extra = ""
        if domain:
            where_extra = " AND domain = :domain"
            params["domain"] = domain
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    f"SELECT id, statement, assumption_type, scope, justification, "
                    f"elicited_from, elicited_by, elicited_at, review_status, "
                    f"last_reviewed_at, next_review_due, sensitivity_result, domain "
                    f"FROM assumptions WHERE review_status = :status{where_extra} "
                    f"ORDER BY elicited_at ASC"
                ),
                params,
            )
            return [
                Assumption.model_validate(_parse_json_fields(dict(r), ["scope", "elicited_by"]))
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

        The verdict must be APPROVED.  Draft objects may be persisted with
        approval_status=DRAFT — they require a *separate* promote_to_approved
        call after human review.

        Returns the UUID of the newly created object.
        """
        async with CloudSqlFactory.get_session_factory()() as session:
            # Verify verdict
            await _assert_verdict_approved(
                session, verdict_id, context=f"propose_draft({type(draft).__name__})"
            )

            obj_id = draft.id

            if isinstance(draft, CausalEdge):
                await session.execute(
                    text(
                        "INSERT INTO causal_edges ("
                        "  id, source_id, source_type, target_id, target_type, "
                        "  edge_type, direction, confidence, evidence_refs, "
                        "  assumption_refs, approval_status, guardian_verdict_ref, "
                        "  version, lineage, domain, created_at"
                        ") VALUES ("
                        "  :id, :source_id, :source_type, :target_id, :target_type, "
                        "  :edge_type, :direction, :confidence, :evidence_refs, "
                        "  :assumption_refs, :approval_status, :verdict_ref, "
                        "  :version, :lineage, :domain, :created_at"
                        ")"
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
                        "evidence_refs": json.dumps([str(e) for e in draft.evidence_refs]),
                        "assumption_refs": json.dumps([str(a) for a in draft.assumption_refs]),
                        "approval_status": _sv(draft.approval_status),
                        "verdict_ref": str(verdict_id),
                        "version": draft.version,
                        "lineage": json.dumps([r.model_dump() for r in draft.lineage], default=str),
                        "domain": draft.domain,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )

            elif isinstance(draft, GraphFragment):
                await session.execute(
                    text(
                        "INSERT INTO graph_fragments ("
                        "  id, name, domain, description, edge_ids, entry_nodes, "
                        "  exit_nodes, approval_status, version, usage_count, tags, "
                        "  guardian_verdict_ref, created_by, created_at"
                        ") VALUES ("
                        "  :id, :name, :domain, :description, :edge_ids, :entry_nodes, "
                        "  :exit_nodes, :approval_status, :version, 0, :tags, "
                        "  :verdict_ref, :created_by, :created_at"
                        ")"
                    ),
                    {
                        "id": str(obj_id),
                        "name": draft.name,
                        "domain": draft.domain,
                        "description": draft.description,
                        "edge_ids": json.dumps([str(e) for e in draft.edges]),
                        "entry_nodes": json.dumps(
                            [n.model_dump() for n in draft.entry_nodes], default=str
                        ),
                        "exit_nodes": json.dumps(
                            [n.model_dump() for n in draft.exit_nodes], default=str
                        ),
                        "approval_status": _sv(draft.approval_status),
                        "version": draft.version,
                        "tags": _sv(draft.tags),
                        "verdict_ref": str(verdict_id) if verdict_id else None,
                        "created_by": draft.created_by.model_dump_json(),
                        "created_at": _sv(draft.created_at),
                    },
                )

            elif isinstance(draft, CausalEntity):
                await session.execute(
                    text(
                        "INSERT INTO causal_entities ("
                        "  id, name, entity_type, domain, description, attributes, "
                        "  ontology_version, approval_status, created_by, created_at, tags"
                        ") VALUES ("
                        "  :id, :name, :entity_type, :domain, :description, "
                        "  :attributes, :ontology_version, :approval_status, "
                        "  :created_by, :created_at, :tags"
                        ")"
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
                        "created_at": _sv(draft.created_at),
                        "tags": _sv(draft.tags),
                    },
                )

            elif isinstance(draft, Assumption):
                await session.execute(
                    text(
                        "INSERT INTO assumptions ("
                        "  id, statement, assumption_type, scope, justification, "
                        "  elicited_from, elicited_by, elicited_at, review_status, domain"
                        ") VALUES ("
                        "  :id, :statement, :assumption_type, :scope, :justification, "
                        "  :elicited_from, :elicited_by, :elicited_at, :review_status, "
                        "  :domain"
                        ")"
                    ),
                    {
                        "id": str(obj_id),
                        "statement": draft.statement,
                        "assumption_type": _sv(draft.assumption_type),
                        "scope": json.dumps(
                            [s.model_dump() for s in draft.scope], default=str
                        ),
                        "justification": draft.justification,
                        "elicited_from": _sv(draft.elicited_from),
                        "elicited_by": draft.elicited_by.model_dump_json(),
                        "elicited_at": _sv(draft.elicited_at),
                        "review_status": _sv(draft.review_status),
                        "domain": draft.domain,
                    },
                )

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
    ) -> None:
        """
        Promote a DRAFT / UNDER_REVIEW object to APPROVED status.

        Verifies the Guardian verdict before making any changes.
        Records an approval lineage entry on the object.

        object_type must be one of: "CausalEdge", "GraphFragment", "CausalEntity".
        """
        table_map = {
            "CausalEdge": "causal_edges",
            "GraphFragment": "graph_fragments",
            "CausalEntity": "causal_entities",
        }
        if object_type not in table_map:
            raise ValueError(f"promote_to_approved: unknown object_type '{object_type}'")

        table = table_map[object_type]

        async with CloudSqlFactory.get_session_factory()() as session:
            await _assert_verdict_approved(
                session, verdict_id, context=f"promote_to_approved({object_type})"
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

            await session.execute(
                text(
                    f"UPDATE {table} "
                    f"SET approval_status = 'APPROVED', "
                    f"    approved_at = :approved_at, "
                    f"    approved_by = :approved_by, "
                    f"    guardian_verdict_ref = :verdict_ref, "
                    f"    lineage = lineage || :lineage_entry "
                    f"WHERE id = :oid"
                ),
                {
                    "oid": str(object_id),
                    "approved_at": now,
                    "approved_by": approver.model_dump_json(),
                    "verdict_ref": str(verdict_id),
                    "lineage_entry": f"[{lineage_entry}]",
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

    async def list_pending_review_queue(
        self, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Return all DRAFT / UNDER_REVIEW objects ordered by risk tier for the
        governance review queue.

        Returns a list of dicts with keys: id, object_type, domain, name,
        approval_status, created_at.
        """
        results: list[dict[str, Any]] = []
        statuses = ("'DRAFT'", "'UNDER_REVIEW'")
        async with CloudSqlFactory.get_session_factory()() as session:
            for obj_type, table, name_col in [
                ("CausalEdge", "causal_edges", "edge_type"),
                ("GraphFragment", "graph_fragments", "name"),
                ("CausalEntity", "causal_entities", "name"),
            ]:
                rows = await session.execute(
                    text(
                        f"SELECT id, {name_col} AS name, domain, approval_status, created_at "
                        f"FROM {table} "
                        f"WHERE approval_status IN ({', '.join(statuses)}) "
                        f"ORDER BY created_at ASC "
                        f"LIMIT :lim"
                    ),
                    {"lim": limit},
                )
                for r in rows.mappings():
                    results.append(
                        {
                            "id": str(r["id"]),
                            "object_type": obj_type,
                            "domain": r["domain"],
                            "name": r["name"],
                            "approval_status": r["approval_status"],
                            "created_at": (
                                r["created_at"].isoformat()
                                if hasattr(r["created_at"], "isoformat")
                                else str(r["created_at"])
                            ) if r["created_at"] else None,
                        }
                    )

        # Sort combined results chronologically (oldest first = highest priority)
        results.sort(key=lambda x: x.get("created_at") or "")
        return results[:limit]

"""
Semantic objects are versioned: a state change writes a new version, never over an old one.

Written for septa-14 (register entry `semantic-objects-mutated-in-place`).
ADR-001's principle, and the registry's docstring rule 2: "All objects are
versioned -- no in-place mutation; deprecate and replace." Before this,
promote_to_approved and reject_draft UPDATEd the object's own row -- status,
approver, verdict reference and lineage -- and nothing kept what the row said
before. `version` stayed at its 1.0.0 default for ever.

The design under test (docs/ADR-001-canonical-semantic-layer.md, "Versioning"):
every state of a CausalEntity, CausalEdge, GraphFragment or Assumption is a row
in `semantic_object_versions`, keyed (object_type, object_id, revision), holding
the object's complete stored state at that revision. The row in the object's
own table is the head: the current version, and only that. A state change
records the new version and advances the head's `revision` by one; the version
rows themselves are append-only.

Preconditions: a registry over a clean database (SQLite, or PostgreSQL built
from the migrations when TEST_DATABASE_URL is set); signed, subject-bound
verdicts from `verdict_for`; objects that name their source data and drafter,
which approval requires (septa-15, tests/integration/test_lineage_chain.py).

Postconditions:
  - propose_draft writes revision 1 (change PROPOSED), and the head reads back
    as revision 1 with the same state;
  - approving or rejecting leaves revision 1's row exactly as it was -- every
    column, compared before and after -- and writes revision 2 (APPROVED, or
    REJECTED with status DEPRECATED) naming the reviewer and the verdict;
  - reads return the current version: the head is revision 2, and the object
    as of revision 1 is still readable, as it was (DRAFT);
  - history is queryable through the registry, bounded by domain scope like
    every other read: outside the scope it is not found;
  - a refused review (a terminal object) writes no version;
  - an object written before versioning (a row with no history) keeps its
    pre-change state: the first review records it as revision 1 (RECORDED)
    before writing revision 2;
  - the database holds the invariant, not only the registry: a version row
    cannot be updated or deleted; a head UPDATE that does not advance the
    revision by exactly one, or whose prior revision is not recorded, is
    refused; on PostgreSQL an UPDATE whose new revision is not recorded fails
    at commit, and causal_app may only SELECT and INSERT version rows;
  - on PostgreSQL, 014's backfill records every pre-existing row as revision 1
    and is idempotent.

Undefined, and not asserted: the exact text of trigger errors beyond naming
the rule, the order of keys inside a stored state, and the timestamp's
sub-second precision.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.db.types import json_value
from src.exceptions import InvalidTransitionError, ObjectNotFoundError
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    DataSource,
    EdgeDirection,
    EdgeType,
    ElicitedFrom,
    EntityType,
    GraphFragment,
    SensitivityResult,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry
from src.semantic_layer.verdict import RECORD_SENSITIVITY
from tests.conftest import migration_files, postgres_only

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from tests.conftest import PostgresTestDatabase, VerdictFactory

pytestmark = pytest.mark.asyncio

AUTHOR = UserRef(id="author-001", email="author@test.com", name="Author")
REVIEWER = UserRef(id="reviewer-001", email="reviewer@test.com", name="Reviewer")
SOURCE = DataSource(
    kind="dataset",
    ref="s3://lake/devops/deploys-2026-09.parquet",
    digest="sha256:" + hashlib.sha256(b"deploys-2026-09").hexdigest(),
)


def _entity(domain: str = "devops") -> CausalEntity:
    return CausalEntity(
        name="checkout-service",
        entity_type=EntityType.SERVICE,
        domain=domain,
        created_by=AUTHOR,
        source_data=SOURCE,
    )


def _edge(verdict_ref: UUID, domain: str = "devops") -> CausalEdge:
    return CausalEdge(
        source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
        target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        direction=EdgeDirection.FORWARD,
        confidence=0.8,
        guardian_verdict_ref=verdict_ref,
        domain=domain,
        source_data=SOURCE,
    )


def _fragment(domain: str = "devops") -> GraphFragment:
    return GraphFragment(
        name=f"fragment-{uuid4().hex[:8]}",
        domain=domain,
        created_by=AUTHOR,
        source_data=SOURCE,
    )


def _assumption(domain: str = "devops") -> Assumption:
    return Assumption(
        statement="Deploy frequency is not confounded by team size.",
        assumption_type=AssumptionType.IGNORABILITY,
        justification="Teams deploy on a shared release train.",
        elicited_from=ElicitedFrom.DOMAIN_EXPERT,
        elicited_by=AUTHOR,
        domain=domain,
        source_data=SOURCE,
    )


async def _draft(
    kind: str, verdict_for: VerdictFactory, approved_audit_entry_id: UUID, domain: str = "devops"
) -> Any:
    registry = SemanticLayerRegistry()
    obj: Any
    if kind == "CausalEntity":
        obj = _entity(domain)
    elif kind == "CausalEdge":
        obj = _edge(approved_audit_entry_id, domain)
    elif kind == "GraphFragment":
        obj = _fragment(domain)
    else:
        obj = _assumption(domain)
    await registry.propose_draft(obj, await verdict_for(obj.id), submitted_by=AUTHOR)
    return obj


_READ_VERSIONS = text(
    "SELECT object_type, object_id, revision, change, state, domain, actor, agent_id, "
    "verdict_ref, recorded_at FROM semantic_object_versions "
    "WHERE object_type = :t AND object_id = :i ORDER BY revision"
)


async def _version_rows(session: AsyncSession, kind: str, object_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(_READ_VERSIONS, {"t": kind, "i": str(object_id)})
    result = [dict(r) for r in rows.mappings()]
    await session.commit()
    return result


REVIEWABLE = ("CausalEntity", "CausalEdge", "GraphFragment")
_TABLE = {
    "CausalEntity": "causal_entities",
    "CausalEdge": "causal_edges",
    "GraphFragment": "graph_fragments",
}


# ── Proposing writes revision 1 ──────────────────────────────────────────────


@pytest.mark.parametrize("kind", [*REVIEWABLE, "Assumption"])
async def test_a_draft_is_revision_one_and_its_history_holds_it(
    kind: str,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft(kind, verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()

    versions = await registry.get_versions(kind, obj.id)
    assert [v.revision for v in versions] == [1]
    assert versions[0].change == "PROPOSED"
    assert versions[0].actor is not None and versions[0].actor.id == AUTHOR.id
    assert versions[0].domain == "devops"

    head = await registry.get_object(kind, obj.id)
    assert head.revision == 1
    assert await registry.get_object_version(kind, obj.id, 1) == head

    rows = await _version_rows(db_session, kind, obj.id)
    assert len(rows) == 1 and rows[0]["revision"] == 1


# ── Reviewing writes revision 2 and leaves revision 1 alone ──────────────────


@pytest.mark.parametrize("kind", REVIEWABLE)
async def test_approval_writes_a_successor_and_leaves_the_prior_version_unchanged(
    kind: str,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft(kind, verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()
    before = await _version_rows(db_session, kind, obj.id)

    approving = await verdict_for(obj.id, action_type="GOVERNANCE_APPROVE")
    await registry.promote_to_approved(obj.id, kind, approving, REVIEWER)

    after = await _version_rows(db_session, kind, obj.id)
    assert after[0] == before[0], "revision 1 must be exactly what it was"
    assert [r["revision"] for r in after] == [1, 2]

    versions = await registry.get_versions(kind, obj.id)
    latest = versions[-1]
    assert latest.revision == 2
    assert latest.change == "APPROVED"
    assert latest.actor is not None and latest.actor.id == REVIEWER.id
    assert str(latest.verdict_ref) == str(approving)
    assert latest.state["approval_status"] == "APPROVED"
    assert versions[0].state["approval_status"] == "DRAFT"

    head = await registry.get_object(kind, obj.id)
    assert head.revision == 2
    assert head.approval_status == "APPROVED"
    assert await registry.get_object_version(kind, obj.id, 2) == head
    prior = await registry.get_object_version(kind, obj.id, 1)
    assert prior.approval_status == "DRAFT"
    assert prior.revision == 1


@pytest.mark.parametrize("kind", REVIEWABLE)
async def test_rejection_writes_a_deprecated_successor_and_leaves_the_draft_unchanged(
    kind: str,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft(kind, verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()
    before = await _version_rows(db_session, kind, obj.id)

    rejecting = await verdict_for(obj.id, action_type="GOVERNANCE_REJECT")
    await registry.reject_draft(obj.id, kind, rejecting, REVIEWER, "confounded by team size")

    after = await _version_rows(db_session, kind, obj.id)
    assert after[0] == before[0]
    assert [r["revision"] for r in after] == [1, 2]
    assert after[1]["change"] == "REJECTED"
    assert json_value(after[1]["state"])["approval_status"] == "DEPRECATED"

    head = await registry.get_object(kind, obj.id)
    assert head.revision == 2 and head.approval_status == "DEPRECATED"
    assert (await registry.get_object_version(kind, obj.id, 1)).approval_status == "DRAFT"


async def test_a_refused_review_writes_no_version(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft("CausalEntity", verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()
    await registry.promote_to_approved(
        obj.id, "CausalEntity", await verdict_for(obj.id, action_type="GOVERNANCE_APPROVE"),
        REVIEWER,
    )
    with pytest.raises(InvalidTransitionError):
        await registry.reject_draft(
            obj.id, "CausalEntity", await verdict_for(obj.id, action_type="GOVERNANCE_REJECT"),
            REVIEWER, "too late",
        )
    assert [r["revision"] for r in await _version_rows(db_session, "CausalEntity", obj.id)] == [
        1,
        2,
    ]


# ── Recording a sensitivity result is a state change too (septa-62) ──────────


def _sensitivity(e_value: float) -> SensitivityResult:
    return SensitivityResult(
        e_value=e_value,
        robust_to_unmeasured_confounding=e_value >= 2.0,
        analysis_method="e-value (VanderWeele-Ding)",
        computed_at=datetime.now(UTC),
        computed_by="causal-core",
    )


async def test_recording_a_sensitivity_result_writes_the_edges_next_revision(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    """record_edge_sensitivity changes a draft edge (assumption_refs, lineage): a version.

    The edge advances by one revision (change RECORDED, the recorder and the
    RECORD_SENSITIVITY verdict), its draft stays in history exactly as it was, and
    the assumption it creates is recorded as its own revision 1. Each further
    recording is a further revision.
    """
    edge = await _draft("CausalEdge", verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()
    before = await _version_rows(db_session, "CausalEdge", edge.id)

    recording = await verdict_for(edge.id, action_type=RECORD_SENSITIVITY)
    first = await registry.record_edge_sensitivity(edge.id, _sensitivity(2.4), recording, REVIEWER)

    after = await _version_rows(db_session, "CausalEdge", edge.id)
    assert after[0] == before[0], "the draft must be exactly what it was"
    assert [r["revision"] for r in after] == [1, 2]

    versions = await registry.get_versions("CausalEdge", edge.id)
    latest = versions[-1]
    assert latest.change == "RECORDED"
    assert latest.actor is not None and latest.actor.id == REVIEWER.id
    assert str(latest.verdict_ref) == str(recording)
    assert [str(a) for a in latest.state["assumption_refs"]] == [str(first)]
    assert versions[0].state["assumption_refs"] == []

    head = await registry.get_object("CausalEdge", edge.id)
    assert head.revision == 2 and head.approval_status == "DRAFT"
    assert [str(a) for a in head.assumption_refs] == [str(first)]
    assert await registry.get_object_version("CausalEdge", edge.id, 2) == head

    made = await registry.get_versions("Assumption", first)
    assert [v.revision for v in made] == [1] and made[0].change == "PROPOSED"
    assert str(made[0].verdict_ref) == str(recording)
    assert made[0].state["sensitivity_verdict_id"] == str(recording)

    again = await verdict_for(edge.id, action_type=RECORD_SENSITIVITY)
    second = await registry.record_edge_sensitivity(edge.id, _sensitivity(3.1), again, REVIEWER)
    head = await registry.get_object("CausalEdge", edge.id)
    assert head.revision == 3
    assert [str(a) for a in head.assumption_refs] == [str(first), str(second)]
    rows = await _version_rows(db_session, "CausalEdge", edge.id)
    assert [r["revision"] for r in rows] == [1, 2, 3] and rows[:2] == after


async def test_a_refused_recording_writes_no_version(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    edge = await _draft("CausalEdge", verdict_for, approved_audit_entry_id)
    registry = SemanticLayerRegistry()
    await registry.promote_to_approved(
        edge.id, "CausalEdge", await verdict_for(edge.id, action_type="GOVERNANCE_APPROVE"),
        REVIEWER,
    )
    with pytest.raises(InvalidTransitionError):
        await registry.record_edge_sensitivity(
            edge.id, _sensitivity(2.4),
            await verdict_for(edge.id, action_type=RECORD_SENSITIVITY), REVIEWER,
        )
    rows = await _version_rows(db_session, "CausalEdge", edge.id)
    assert [r["revision"] for r in rows] == [1, 2]


async def test_history_is_bounded_by_domain_scope(
    db_engine: AsyncEngine, verdict_for: VerdictFactory, approved_audit_entry_id: UUID
) -> None:
    obj = await _draft("CausalEntity", verdict_for, approved_audit_entry_id, domain="finance")
    registry = SemanticLayerRegistry()
    assert len(await registry.get_versions("CausalEntity", obj.id, domains={"finance"})) == 1
    with pytest.raises(ObjectNotFoundError):
        await registry.get_versions("CausalEntity", obj.id, domains={"devops"})
    with pytest.raises(ObjectNotFoundError):
        await registry.get_object_version("CausalEntity", obj.id, 1, domains={"devops"})
    with pytest.raises(ObjectNotFoundError):
        await registry.get_versions("CausalEntity", uuid4())
    with pytest.raises(ValueError, match="object_type"):
        await registry.get_versions("Treatment", obj.id)


# ── Rows written before versioning ───────────────────────────────────────────


async def _seed_unversioned_entity(session: AsyncSession, domain: str = "devops") -> UUID:
    """A DRAFT written straight into the table: what 014 found in existing databases."""
    entity_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO causal_entities (id, name, entity_type, domain, description, "
            " attributes, ontology_version, approval_status, created_by, created_at, source_data) "
            "VALUES (:id, 'legacy', 'SERVICE', :domain, 'before versioning', '{}', '1.0.0', "
            " 'DRAFT', '{\"id\": \"legacy-author\"}', '2026-09-01T00:00:00+00:00', :source)"
        ),
        {"id": str(entity_id), "domain": domain, "source": SOURCE.model_dump_json()},
    )
    await session.commit()
    return entity_id


async def test_a_row_written_before_versioning_keeps_its_prior_state(
    db_engine: AsyncEngine, db_session: AsyncSession, verdict_for: VerdictFactory
) -> None:
    entity_id = await _seed_unversioned_entity(db_session)
    registry = SemanticLayerRegistry()

    await registry.promote_to_approved(
        entity_id, "CausalEntity",
        await verdict_for(entity_id, action_type="GOVERNANCE_APPROVE"), REVIEWER,
    )

    versions = await registry.get_versions("CausalEntity", entity_id)
    assert [(v.revision, v.change) for v in versions] == [(1, "RECORDED"), (2, "APPROVED")]
    assert versions[0].state["approval_status"] == "DRAFT"
    assert versions[0].state["description"] == "before versioning"
    assert versions[1].state["approval_status"] == "APPROVED"


# ── The database holds the rule ──────────────────────────────────────────────


async def test_a_version_row_cannot_be_updated_or_deleted(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft("CausalEntity", verdict_for, approved_audit_entry_id)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE semantic_object_versions SET change = 'APPROVED' WHERE object_id = :i"),
            {"i": str(obj.id)},
        )
    await db_session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("DELETE FROM semantic_object_versions WHERE object_id = :i"),
            {"i": str(obj.id)},
        )
    await db_session.rollback()
    assert len(await _version_rows(db_session, "CausalEntity", obj.id)) == 1


@pytest.mark.parametrize("kind", REVIEWABLE)
async def test_a_head_update_that_does_not_advance_the_revision_is_refused(
    kind: str,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft(kind, verdict_for, approved_audit_entry_id)
    statements = {
        "causal_entities": "UPDATE causal_entities SET approval_status = 'APPROVED' WHERE id = :i",
        "causal_edges": "UPDATE causal_edges SET approval_status = 'APPROVED' WHERE id = :i",
        "graph_fragments": "UPDATE graph_fragments SET approval_status = 'APPROVED' WHERE id = :i",
    }
    with pytest.raises(DBAPIError, match="revision"):
        await db_session.execute(text(statements[_TABLE[kind]]), {"i": str(obj.id)})
    await db_session.rollback()
    head = await SemanticLayerRegistry().get_object(kind, obj.id)
    assert head.approval_status == "DRAFT" and head.revision == 1


async def test_an_assumption_head_is_held_to_the_same_rule(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft("Assumption", verdict_for, approved_audit_entry_id)
    with pytest.raises(DBAPIError, match="revision"):
        await db_session.execute(
            text("UPDATE assumptions SET review_status = 'ACCEPTED' WHERE id = :i"),
            {"i": str(obj.id)},
        )
    await db_session.rollback()


async def test_an_update_over_an_unrecorded_state_is_refused(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    """Advancing the revision is not enough: the state being replaced must be in history."""
    entity_id = await _seed_unversioned_entity(db_session)
    with pytest.raises(DBAPIError, match="revision"):
        await db_session.execute(
            text(
                "UPDATE causal_entities SET approval_status = 'APPROVED', "
                "revision = revision + 1 WHERE id = :i"
            ),
            {"i": str(entity_id)},
        )
    await db_session.rollback()


@postgres_only
async def test_on_postgres_an_unrecorded_new_revision_fails_at_commit(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    verdict_for: VerdictFactory,
    approved_audit_entry_id: UUID,
) -> None:
    obj = await _draft("CausalEntity", verdict_for, approved_audit_entry_id)
    await db_session.execute(
        text(
            "UPDATE causal_entities SET description = 'unrecorded', revision = revision + 1 "
            "WHERE id = :i"
        ),
        {"i": str(obj.id)},
    )
    with pytest.raises(DBAPIError, match="revision"):
        await db_session.commit()
    await db_session.rollback()
    head = await SemanticLayerRegistry().get_object("CausalEntity", obj.id)
    assert head.revision == 1 and head.description != "unrecorded"


@postgres_only
async def test_on_postgres_the_app_role_may_only_read_and_append_versions(
    db_engine: AsyncEngine,
) -> None:
    async with db_engine.connect() as conn:
        grants = (
            await conn.execute(
                text(
                    "SELECT has_table_privilege('causal_app', 'semantic_object_versions', p) "
                    "FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p"
                )
            )
        ).scalars().all()
        rls = (
            await conn.execute(
                text(
                    "SELECT relrowsecurity FROM pg_class WHERE relname = 'semantic_object_versions'"
                )
            )
        ).scalar_one()
    assert list(grants) == [True, True, False, False, False]
    assert rls is True


@postgres_only
async def test_on_postgres_the_migration_records_existing_rows_once(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    postgres_test_db: PostgresTestDatabase,
) -> None:
    import asyncpg

    entity_id = await _seed_unversioned_entity(db_session)
    migration = next(p for p in migration_files() if p.name.startswith("014_"))
    dsn = postgres_test_db.owner_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    for _ in range(2):  # idempotent: a second run records nothing twice
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(migration.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    rows = await _version_rows(db_session, "CausalEntity", entity_id)
    assert [(r["revision"], r["change"]) for r in rows] == [(1, "RECORDED")]
    state = json_value(rows[0]["state"])
    assert state["approval_status"] == "DRAFT"
    assert state["description"] == "before versioning"
    assert ApprovalStatus(state["approval_status"]) is ApprovalStatus.DRAFT

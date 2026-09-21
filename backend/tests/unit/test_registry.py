"""
Unit tests for SemanticLayerRegistry.

Invariants tested:
  1.  propose_draft(CausalEntity) persists entity and returns its UUID
  2.  propose_draft(GraphFragment) persists fragment and returns its UUID
  3.  propose_draft(Assumption) persists assumption and returns its UUID
  4.  propose_draft raises GuardianBlockedError for non-APPROVED verdict
  5.  get_entity returns the correct entity after propose_draft
  6.  get_entity raises ObjectNotFoundError for unknown ID
  7.  list_entities returns all entities (no filter)
  8.  list_entities filters by domain
  9.  get_fragment returns the correct fragment after propose_draft
 10.  get_fragment raises ObjectNotFoundError for unknown ID
 11.  list_fragments returns results filtered by status
 12.  get_assumption returns the correct assumption
 13.  get_assumption raises ObjectNotFoundError for unknown ID
 14.  list_pending_assumptions returns only PENDING review_status assumptions
 15.  list_pending_review_queue returns mixed draft objects across tables
 16.  _object_hash is deterministic and order-independent
 17.  _sv serializes enums, lists, and datetimes correctly
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import VerdictFactory

from src.exceptions import GuardianBlockedError, ObjectNotFoundError
from src.semantic_layer.models import (
    ApprovalStatus,
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    EdgeDirection,
    EdgeType,
    ElicitedFrom,
    EntityType,
    FragmentApprovalStatus,
    GraphFragment,
    GuardianVerdict,
    UserRef,
)
from src.semantic_layer.registry import SemanticLayerRegistry, _object_hash, _sv

pytestmark = pytest.mark.asyncio


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _user() -> UserRef:
    return UserRef(id="test-user-001", email="test@test.com", name="Test User")


def _entity(name: str = "test-service", domain: str = "devops") -> CausalEntity:
    return CausalEntity(
        name=name,
        entity_type=EntityType.SERVICE,
        domain=domain,
        description="Unit test entity",
        ontology_version="1.0.0",
        created_by=_user(),
    )


def _fragment(name: str = "test-fragment", domain: str = "devops") -> GraphFragment:
    return GraphFragment(
        name=name,
        domain=domain,
        description="Unit test fragment",
        created_by=_user(),
    )


def _assumption(domain: str = "devops") -> Assumption:
    return Assumption(
        statement="Test assumption: service A causes service B latency.",
        assumption_type=AssumptionType.STRUCTURAL,
        justification="Derived from service dependency analysis.",
        elicited_from=ElicitedFrom.DOMAIN_EXPERT,
        elicited_by=_user(),
        domain=domain,
    )


def _edge(verdict_id: UUID, domain: str = "devops") -> CausalEdge:
    src_id = uuid4()
    tgt_id = uuid4()
    return CausalEdge(
        source=CausalObjectRef(id=src_id, object_type="CausalEntity"),
        target=CausalObjectRef(id=tgt_id, object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        direction=EdgeDirection.FORWARD,
        confidence=0.80,
        guardian_verdict_ref=verdict_id,
        domain=domain,
    )


# ── 1. propose_draft(CausalEntity) ────────────────────────────────────────────


async def test_propose_draft_causal_entity(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """propose_draft(CausalEntity) must persist the entity and return its UUID."""
    registry = SemanticLayerRegistry()
    entity = _entity()

    result_id = await registry.propose_draft(entity, await verdict_for(entity.id))

    assert isinstance(result_id, UUID)
    assert result_id == entity.id


# ── 2. propose_draft(GraphFragment) ──────────────────────────────────────────


async def test_propose_draft_graph_fragment(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """propose_draft(GraphFragment) must persist the fragment and return its UUID."""
    registry = SemanticLayerRegistry()
    fragment = _fragment()

    result_id = await registry.propose_draft(fragment, await verdict_for(fragment.id))

    assert isinstance(result_id, UUID)
    assert result_id == fragment.id


# ── 3. propose_draft(Assumption) ─────────────────────────────────────────────


async def test_propose_draft_assumption(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """propose_draft(Assumption) must persist the assumption and return its UUID."""
    registry = SemanticLayerRegistry()
    assumption = _assumption()

    result_id = await registry.propose_draft(assumption, await verdict_for(assumption.id))

    assert isinstance(result_id, UUID)
    assert result_id == assumption.id


# ── 4. propose_draft raises on non-APPROVED verdict ──────────────────────────


async def test_propose_draft_rejects_non_approved_verdict(
    db_engine: object,
    rejected_audit_entry_id: UUID,
) -> None:
    """propose_draft must raise GuardianBlockedError when verdict is REJECTED."""
    registry = SemanticLayerRegistry()
    entity = _entity()

    with pytest.raises(GuardianBlockedError):
        await registry.propose_draft(entity, rejected_audit_entry_id)


async def test_propose_draft_rejects_unknown_verdict(db_engine: object) -> None:
    """propose_draft must raise GuardianBlockedError for a verdict_id not in audit_log."""
    registry = SemanticLayerRegistry()
    entity = _entity()

    with pytest.raises(GuardianBlockedError):
        await registry.propose_draft(entity, uuid4())  # random — not in DB


# ── 5. get_entity after propose_draft ────────────────────────────────────────


async def test_get_entity_returns_persisted_entity(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """get_entity must return the correct entity after it has been proposed."""
    registry = SemanticLayerRegistry()
    entity = _entity(name="payment-service")

    await registry.propose_draft(entity, await verdict_for(entity.id))
    fetched = await registry.get_entity(entity.id)

    assert fetched.id == entity.id
    assert fetched.name == "payment-service"
    assert fetched.domain == "devops"


# ── 6. get_entity raises ObjectNotFoundError for unknown ID ───────────────────


async def test_get_entity_not_found(db_engine: object) -> None:
    """get_entity must raise ObjectNotFoundError for an unknown entity ID."""
    registry = SemanticLayerRegistry()

    with pytest.raises(ObjectNotFoundError):
        await registry.get_entity(uuid4())


# ── 7. list_entities without filter ──────────────────────────────────────────


async def test_list_entities_returns_all(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_entities() without filters must return all persisted entities."""
    registry = SemanticLayerRegistry()
    e1 = _entity(name="svc-a")
    e2 = _entity(name="svc-b")

    await registry.propose_draft(e1, await verdict_for(e1.id))
    await registry.propose_draft(e2, await verdict_for(e2.id))

    results = await registry.list_entities()
    ids = {e.id for e in results}
    assert e1.id in ids
    assert e2.id in ids


# ── 8. list_entities filters by domain ───────────────────────────────────────


async def test_list_entities_filters_by_domain(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_entities(domain=X) must return only entities in domain X."""
    registry = SemanticLayerRegistry()
    devops_entity = _entity(domain="devops")
    finance_entity = _entity(domain="finance")

    await registry.propose_draft(devops_entity, await verdict_for(devops_entity.id))
    await registry.propose_draft(finance_entity, await verdict_for(finance_entity.id))

    devops_results = await registry.list_entities(domain="devops")
    finance_results = await registry.list_entities(domain="finance")

    devops_ids = {e.id for e in devops_results}
    finance_ids = {e.id for e in finance_results}

    assert devops_entity.id in devops_ids
    assert finance_entity.id not in devops_ids
    assert finance_entity.id in finance_ids


# ── 9. get_fragment after propose_draft ───────────────────────────────────────


async def test_get_fragment_returns_persisted_fragment(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """get_fragment must return the correct fragment after propose_draft."""
    registry = SemanticLayerRegistry()
    fragment = _fragment(name="payment-flow")

    await registry.propose_draft(fragment, await verdict_for(fragment.id))
    fetched = await registry.get_fragment(fragment.id)

    assert fetched.id == fragment.id
    assert fetched.name == "payment-flow"


# ── 10. get_fragment raises ObjectNotFoundError ───────────────────────────────


async def test_get_fragment_not_found(db_engine: object) -> None:
    """get_fragment must raise ObjectNotFoundError for an unknown fragment ID."""
    registry = SemanticLayerRegistry()

    with pytest.raises(ObjectNotFoundError):
        await registry.get_fragment(uuid4())


# ── 11. list_fragments filters by status ─────────────────────────────────────


async def test_list_fragments_filters_by_domain(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_fragments(domain=X) must return only fragments in domain X."""
    registry = SemanticLayerRegistry()
    f1 = _fragment(domain="devops")
    f2 = _fragment(domain="finance")

    await registry.propose_draft(f1, await verdict_for(f1.id))
    await registry.propose_draft(f2, await verdict_for(f2.id))

    devops_frags = await registry.list_fragments(domain="devops")
    finance_frags = await registry.list_fragments(domain="finance")

    assert f1.id in {f.id for f in devops_frags}
    assert f2.id not in {f.id for f in devops_frags}
    assert f2.id in {f.id for f in finance_frags}


# ── 12. get_assumption ────────────────────────────────────────────────────────


async def test_get_assumption_returns_persisted_assumption(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """get_assumption must return the correct assumption after propose_draft."""
    registry = SemanticLayerRegistry()
    assumption = _assumption()

    await registry.propose_draft(assumption, await verdict_for(assumption.id))
    fetched = await registry.get_assumption(assumption.id)

    assert fetched.id == assumption.id
    assert "service A" in fetched.statement


# ── 13. get_assumption raises ObjectNotFoundError ─────────────────────────────


async def test_get_assumption_not_found(db_engine: object) -> None:
    """get_assumption must raise ObjectNotFoundError for an unknown assumption ID."""
    registry = SemanticLayerRegistry()

    with pytest.raises(ObjectNotFoundError):
        await registry.get_assumption(uuid4())


# ── 14. list_pending_assumptions ─────────────────────────────────────────────


async def test_list_pending_assumptions_returns_pending_only(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_pending_assumptions() must return only PENDING review_status assumptions."""
    registry = SemanticLayerRegistry()
    assumption = _assumption()

    await registry.propose_draft(assumption, await verdict_for(assumption.id))

    pending = await registry.list_pending_assumptions()
    assert any(a.id == assumption.id for a in pending), (
        "Newly proposed assumption must appear in pending list"
    )


async def test_list_pending_assumptions_filters_by_domain(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_pending_assumptions(domain=X) returns only assumptions in domain X."""
    registry = SemanticLayerRegistry()
    a_devops = _assumption(domain="devops")
    a_finance = _assumption(domain="finance")

    await registry.propose_draft(a_devops, await verdict_for(a_devops.id))
    await registry.propose_draft(a_finance, await verdict_for(a_finance.id))

    devops_pending = await registry.list_pending_assumptions(domain="devops")
    ids = {a.id for a in devops_pending}
    assert a_devops.id in ids
    assert a_finance.id not in ids


# ── 15. list_pending_review_queue ─────────────────────────────────────────────


async def test_list_pending_review_queue_returns_draft_objects(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """
    list_pending_review_queue() must return DRAFT entities, fragments, and edges
    in the governance review queue.
    """
    registry = SemanticLayerRegistry()
    entity = _entity(name="queue-entity")
    fragment = _fragment(name="queue-fragment")

    await registry.propose_draft(entity, await verdict_for(entity.id))
    await registry.propose_draft(fragment, await verdict_for(fragment.id))

    queue = await registry.list_pending_review_queue(limit=50)

    ids_in_queue = {item["id"] for item in queue}
    assert str(entity.id) in ids_in_queue
    assert str(fragment.id) in ids_in_queue

    # All items must have required fields
    for item in queue:
        assert "id" in item
        assert "object_type" in item
        assert "domain" in item
        assert "approval_status" in item


# ── 16. _object_hash ──────────────────────────────────────────────────────────


def test_object_hash_is_deterministic() -> None:
    """_object_hash must return the same value for equal dicts."""
    data = {"name": "test-service", "domain": "devops", "confidence": 0.8}
    assert _object_hash(data) == _object_hash(data)


def test_object_hash_is_order_independent() -> None:
    """_object_hash must produce the same result regardless of key insertion order."""
    data_a = {"a": 1, "b": 2, "c": 3}
    data_b = {"c": 3, "a": 1, "b": 2}
    assert _object_hash(data_a) == _object_hash(data_b)


def test_object_hash_is_unique_for_different_inputs() -> None:
    """_object_hash must produce different values for different inputs."""
    assert _object_hash({"a": 1}) != _object_hash({"a": 2})
    assert _object_hash({"a": 1}) != _object_hash({"b": 1})


# ── 17. _sv helper ────────────────────────────────────────────────────────────


def test_sv_returns_none_for_none() -> None:
    assert _sv(None) is None


def test_sv_returns_enum_value() -> None:
    assert _sv(ApprovalStatus.DRAFT) == "DRAFT"
    assert _sv(GuardianVerdict.APPROVED) == "APPROVED"


def test_sv_serializes_list_as_json() -> None:
    result = _sv(["a", "b", "c"])
    assert json.loads(result) == ["a", "b", "c"]


def test_sv_serializes_datetime_as_iso() -> None:
    now = datetime.now(UTC)
    result = _sv(now)
    assert isinstance(result, str)
    # Must be parseable as ISO
    datetime.fromisoformat(result)


def test_sv_passes_through_primitives() -> None:
    assert _sv("hello") == "hello"
    assert _sv(42) == 42
    assert _sv(3.14) == 3.14


# ── Extra coverage: filter paths and edge/promotion methods ──────────────────


def test_parse_json_fields_invalid_json_leaves_unchanged() -> None:
    """_parse_json_fields must leave a field unchanged when JSON parsing fails."""
    from src.semantic_layer.registry import _parse_json_fields

    raw = {"good": '["a", "b"]', "bad": "not-valid-json{{"}
    result = _parse_json_fields(raw, ["good", "bad"])
    assert result["good"] == ["a", "b"]
    assert result["bad"] == "not-valid-json{{"  # left as-is on JSONDecodeError


async def test_list_entities_with_status_filter(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_entities(status=DRAFT) must only return entities with DRAFT status."""
    registry = SemanticLayerRegistry()
    entity = _entity(name="status-filter-entity")
    await registry.propose_draft(entity, await verdict_for(entity.id))

    # Newly proposed entities default to DRAFT
    draft_results = await registry.list_entities(status=ApprovalStatus.DRAFT)
    ids = {e.id for e in draft_results}
    assert entity.id in ids


async def test_list_fragments_with_status_filter(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """list_fragments(status=DRAFT) must only return DRAFT fragments."""
    registry = SemanticLayerRegistry()
    fragment = _fragment(name="status-filter-fragment")
    await registry.propose_draft(fragment, await verdict_for(fragment.id))


    draft_results = await registry.list_fragments(
        status=FragmentApprovalStatus.DRAFT
    )
    ids = {f.id for f in draft_results}
    assert fragment.id in ids


async def test_get_edge_audit_trail_returns_entries(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """
    get_edge_audit_trail must return AuditEntry records associated with the edge.

    propose_draft records the verdict that authorised the write as the edge's
    guardian_verdict_ref, so the trail must lead back to that verdict -- not to
    whatever the caller happened to put on the draft.
    """
    registry = SemanticLayerRegistry()
    edge = _edge(verdict_id=approved_audit_entry_id)
    authorising = await verdict_for(edge.id)

    await registry.propose_draft(edge, authorising)

    trail = await registry.get_edge_audit_trail(edge.id)
    assert len(trail) >= 1
    assert any(str(entry.id) == str(authorising) for entry in trail)
    assert trail[0].policy_rules_cited == ["PR-FIXTURE"], (
        "policy rules must survive the round trip as a list, not as characters"
    )


async def test_get_edge_audit_trail_not_found(db_engine: object) -> None:
    """get_edge_audit_trail must raise ObjectNotFoundError for an unknown edge ID."""
    registry = SemanticLayerRegistry()

    with pytest.raises(ObjectNotFoundError):
        await registry.get_edge_audit_trail(uuid4())


async def test_get_edge_returns_persisted_edge(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """get_edge must return the correct CausalEdge after propose_draft."""
    registry = SemanticLayerRegistry()
    edge = _edge(verdict_id=approved_audit_entry_id)

    await registry.propose_draft(edge, await verdict_for(edge.id))
    fetched = await registry.get_edge(edge.id)

    assert fetched.id == edge.id
    assert fetched.edge_type == edge.edge_type
    assert 0.0 <= fetched.confidence <= 1.0


async def test_get_edge_not_found(db_engine: object) -> None:
    """get_edge must raise ObjectNotFoundError for an unknown edge ID."""
    registry = SemanticLayerRegistry()

    with pytest.raises(ObjectNotFoundError):
        await registry.get_edge(uuid4())


async def test_propose_draft_raises_type_error_for_unsupported_type(
    db_engine: object,
    verdict_for: VerdictFactory,
) -> None:
    """propose_draft must raise TypeError when given an unsupported object type."""
    registry = SemanticLayerRegistry()

    class _UnsupportedType:
        id = uuid4()

    draft = _UnsupportedType()
    with pytest.raises(TypeError, match="Unsupported draft type"):
        await registry.propose_draft(draft, await verdict_for(draft.id))  # type: ignore[arg-type]


async def test_promote_to_approved_updates_edge_status(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    """
    promote_to_approved must update the approval_status of the target object.

    It used to assert only "must not raise", which is why neither the status flip
    nor the lineage it appends was ever checked -- and the lineage was being
    corrupted under the dev engine the whole time.
    """
    registry = SemanticLayerRegistry()
    edge = _edge(verdict_id=approved_audit_entry_id)
    await registry.propose_draft(edge, await verdict_for(edge.id))

    approving = await verdict_for(edge.id)
    await registry.promote_to_approved(
        object_id=edge.id,
        object_type="CausalEdge",
        verdict_id=approving,
        approver=_user(),
    )

    row = await db_session.execute(
        text(
            "SELECT approval_status, approved_by, guardian_verdict_ref, lineage "
            "FROM causal_edges WHERE id = :eid"
        ),
        {"eid": str(edge.id)},
    )
    record = row.mappings().one()
    assert record["approval_status"] == "APPROVED"
    assert str(record["guardian_verdict_ref"]) == str(approving)
    lineage = json.loads(record["lineage"])
    assert isinstance(lineage, list) and lineage, "lineage must be a JSON array"
    assert lineage[-1]["step_type"] == "APPROVED"
    assert lineage[-1]["actor_id"] == _user().id


async def test_promote_to_approved_raises_for_unknown_type(
    db_engine: object,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
) -> None:
    """promote_to_approved must raise ValueError for an unsupported object_type."""
    registry = SemanticLayerRegistry()

    object_id = uuid4()
    with pytest.raises(ValueError, match="unknown object_type"):
        await registry.promote_to_approved(
            object_id=object_id,
            object_type="UnknownType",
            verdict_id=await verdict_for(object_id),
            approver=_user(),
        )

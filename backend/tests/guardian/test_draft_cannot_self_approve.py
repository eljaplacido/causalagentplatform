"""A draft may not arrive already approved.

`POST /api/v1/semantic/drafts` says of itself:

    Drafts persisted here still have approval_status=DRAFT.  A separate
    /governance/approve/{id} call by a Governance Officer is required to
    promote to APPROVED.

Nothing enforced the first sentence. `propose_draft` bound `approval_status`
straight from the submitted object, and every field the model validators check
before allowing APPROVED -- `approved_by`, `approved_at`,
`guardian_verdict_ref` -- is supplied by the same caller. So a submitter who
could legitimately get a DRAFT_SUBMISSION past the Guardian could hand the
registry an object that was already APPROVED, and skip the review the endpoint
promises.

The Guardian verdict is not the hole. It is checked, signed, bound to the
object and spent on use. The hole is that the verdict authorises *a write* and
the object decides *what gets written* -- which is the same shape as the
verdict-binding bug fixed in 41f60ce, one level up.

These tests pin the invariant at the registry, not at the endpoint: the
registry is the trust boundary, and an agent calling it directly must be held
to the same rule as one arriving over HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.semantic_layer.models import (
    ApprovalStatus,
    CausalEntity,
    EntityType,
    FragmentApprovalStatus,
    GraphFragment,
)
from src.semantic_layer.registry import SemanticLayerRegistry
from tests.conftest import VerdictFactory, make_user_ref

pytestmark = pytest.mark.guardian


async def _status_of(engine: AsyncEngine, table: str, obj_id: object) -> str:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(f"SELECT approval_status FROM {table} WHERE id = :id"),
                {"id": str(obj_id)},
            )
        ).first()
    assert row is not None, f"{table} row {obj_id} was not persisted"
    return str(row[0])


@pytest.mark.asyncio
async def test_entity_submitted_as_approved_is_stored_as_draft(
    registry: SemanticLayerRegistry,
    db_engine: AsyncEngine,
    verdict_for: VerdictFactory,
) -> None:
    """A CausalEntity that arrives APPROVED, with its own approver, lands as DRAFT."""
    entity = CausalEntity(
        name="self-approved-service",
        entity_type=EntityType.SERVICE,
        domain="devops",
        description="submitted already approved",
        created_by=make_user_ref("attacker"),
        approval_status=ApprovalStatus.APPROVED,
        approved_by=make_user_ref("attacker"),
        approved_at=datetime.now(UTC),
    )

    await registry.propose_draft(entity, await verdict_for(entity.id))

    assert await _status_of(db_engine, "causal_entities", entity.id) == "DRAFT", (
        "an object that named itself APPROVED was persisted as APPROVED -- the "
        "Governance Officer review the endpoint promises was skipped"
    )


@pytest.mark.asyncio
async def test_fragment_submitted_as_approved_is_stored_as_draft(
    registry: SemanticLayerRegistry,
    db_engine: AsyncEngine,
    verdict_for: VerdictFactory,
) -> None:
    """The same for a GraphFragment, whose validator only asks for *a* verdict reference.

    `validate_approved_has_verdict` is satisfied by any UUID, including one the
    submitter invented, so it is not a control on who approved the fragment.
    """
    fragment = GraphFragment(
        name="self-approved-fragment",
        domain="devops",
        description="submitted already approved",
        created_by=make_user_ref("attacker"),
        approval_status=FragmentApprovalStatus.APPROVED,
        guardian_verdict_ref=uuid4(),
    )

    await registry.propose_draft(fragment, await verdict_for(fragment.id))

    assert await _status_of(db_engine, "graph_fragments", fragment.id) == "DRAFT", (
        "a fragment that named itself APPROVED, citing a verdict reference it "
        "chose, was persisted as APPROVED"
    )


@pytest.mark.asyncio
async def test_an_ordinary_draft_is_unaffected(
    registry: SemanticLayerRegistry,
    db_engine: AsyncEngine,
    verdict_for: VerdictFactory,
) -> None:
    """The guard forces a status, so prove it does not break the normal path."""
    entity = CausalEntity(
        name="ordinary-service",
        entity_type=EntityType.SERVICE,
        domain="devops",
        description="an ordinary draft",
        created_by=make_user_ref(),
    )

    returned = await registry.propose_draft(entity, await verdict_for(entity.id))

    assert returned == entity.id
    assert await _status_of(db_engine, "causal_entities", entity.id) == "DRAFT"

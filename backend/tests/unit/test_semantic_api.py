"""
Unit tests for semantic layer API (src/api/semantic.py).

Tests entity/fragment listing, draft submission, and audit trail endpoints
with mocked SemanticLayerRegistry and GovernanceAgent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.semantic import (
    DraftSubmissionRequest,
    get_edge_audit_trail,
    get_entity,
    get_fragment,
    list_entities,
    list_fragments,
    submit_draft,
)
from src.exceptions import ObjectNotFoundError
from src.semantic_layer.models import (
    ApprovalStatus,
    CausalEntity,
    GraphFragment,
    GuardianVerdict,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_user() -> dict[str, Any]:
    # An analyst reads all statuses; the domains claim is their domain scope
    # (src/middleware/rbac.py), which the handlers pass on to the registry.
    return {
        "uid": "semantic-test-001",
        "email": "semantic@test.com",
        "roles": ["analyst"],
        "domains": ["devops"],
    }


# ── list_entities ─────────────────────────────────────────────────────────────


async def test_list_entities_no_filters(mock_user: dict[str, Any]) -> None:
    """List entities without filters returns all entities."""
    mock_entity = MagicMock(spec=CausalEntity)
    mock_entity.id = uuid4()

    with patch(
        "src.api.semantic._registry.list_entities",
        new_callable=AsyncMock,
        return_value=[mock_entity],
    ):
        result = await list_entities(user=mock_user)

    assert len(result) == 1


async def test_list_entities_with_domain_filter(mock_user: dict[str, Any]) -> None:
    """List entities with domain filter passes filter to registry."""
    with patch(
        "src.api.semantic._registry.list_entities",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        await list_entities(domain="devops", user=mock_user)
        mock_list.assert_awaited_once_with(
            domain="devops", status=None, domains=frozenset({"devops"})
        )


async def test_list_entities_with_status_filter(mock_user: dict[str, Any]) -> None:
    """List entities with status filter passes filter to registry."""
    with patch(
        "src.api.semantic._registry.list_entities",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        await list_entities(status=ApprovalStatus.APPROVED, user=mock_user)
        mock_list.assert_awaited_once_with(
            domain=None, status=ApprovalStatus.APPROVED, domains=frozenset({"devops"})
        )


# ── get_entity ────────────────────────────────────────────────────────────────


async def test_get_entity_found(mock_user: dict[str, Any]) -> None:
    """Get entity by ID returns the entity when found."""
    entity_id = uuid4()
    mock_entity = MagicMock(spec=CausalEntity)
    mock_entity.id = entity_id

    with patch(
        "src.api.semantic._registry.get_entity",
        new_callable=AsyncMock,
        return_value=mock_entity,
    ):
        result = await get_entity(entity_id=entity_id, user=mock_user)

    assert result.id == entity_id


async def test_get_entity_not_found(mock_user: dict[str, Any]) -> None:
    """Get entity by ID raises 404 when not found."""
    entity_id = uuid4()

    with patch(
        "src.api.semantic._registry.get_entity",
        new_callable=AsyncMock,
        side_effect=ObjectNotFoundError(f"Entity {entity_id} not found"),
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_entity(entity_id=entity_id, user=mock_user)
        assert exc_info.value.status_code == 404


# ── list_fragments ────────────────────────────────────────────────────────────


async def test_list_fragments_no_filters(mock_user: dict[str, Any]) -> None:
    """List fragments without filters returns all fragments."""
    mock_frag = MagicMock(spec=GraphFragment)
    mock_frag.id = uuid4()

    with patch(
        "src.api.semantic._registry.list_fragments",
        new_callable=AsyncMock,
        return_value=[mock_frag],
    ):
        result = await list_fragments(user=mock_user)

    assert len(result) == 1


# ── get_fragment ──────────────────────────────────────────────────────────────


async def test_get_fragment_found(mock_user: dict[str, Any]) -> None:
    """Get fragment by ID returns the fragment when found."""
    frag_id = uuid4()
    mock_frag = MagicMock(spec=GraphFragment)
    mock_frag.id = frag_id

    with patch(
        "src.api.semantic._registry.get_fragment",
        new_callable=AsyncMock,
        return_value=mock_frag,
    ):
        result = await get_fragment(fragment_id=frag_id, user=mock_user)

    assert result.id == frag_id


async def test_get_fragment_not_found(mock_user: dict[str, Any]) -> None:
    """Get fragment by ID raises 404 when not found."""
    frag_id = uuid4()

    with patch(
        "src.api.semantic._registry.get_fragment",
        new_callable=AsyncMock,
        side_effect=ObjectNotFoundError(f"Fragment {frag_id} not found"),
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_fragment(fragment_id=frag_id, user=mock_user)
        assert exc_info.value.status_code == 404


# ── submit_draft ──────────────────────────────────────────────────────────────


async def test_submit_draft_unknown_type(mock_user: dict[str, Any]) -> None:
    """Submit draft with unknown object_type returns 422."""
    body = DraftSubmissionRequest(
        object_type="UnknownType",
        payload={"id": str(uuid4()), "name": "test"},
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await submit_draft(body=body, request=MagicMock(), user=mock_user)
    assert exc_info.value.status_code == 422
    assert "UnknownType" in str(exc_info.value.detail)


async def test_submit_draft_invalid_payload(mock_user: dict[str, Any]) -> None:
    """Submit draft with invalid payload returns 422."""
    body = DraftSubmissionRequest(
        object_type="CausalEdge",
        payload={"invalid": "data"},
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await submit_draft(body=body, request=MagicMock(), user=mock_user)
    assert exc_info.value.status_code == 422


async def test_submit_draft_guardian_rejected(mock_user: dict[str, Any]) -> None:
    """Submit draft blocked by Guardian returns 403."""
    edge_id = uuid4()
    body = DraftSubmissionRequest(
        object_type="CausalEdge",
        payload={
            "id": str(edge_id),
            "source": {"id": str(uuid4()), "object_type": "CausalEntity"},
            "target": {"id": str(uuid4()), "object_type": "CausalEntity"},
            "edge_type": "CAUSES",
            "direction": "FORWARD",
            "confidence": 0.85,
            "evidence_refs": [],
            "assumption_refs": [],
            "approval_status": "DRAFT",
            "version": "1.0.0",
            "lineage": [],
            "domain": "devops",
            "guardian_verdict_ref": str(uuid4()),
            "created_at": "2026-01-01T00:00:00",
        },
    )

    mock_result = MagicMock()
    mock_result.verdict = GuardianVerdict.REJECTED
    mock_result.reason = "Test rejection"
    mock_result.audit_entry_id = uuid4()

    with patch(
        "src.api.semantic._governance.check",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await submit_draft(body=body, request=MagicMock(), user=mock_user)
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert detail["verdict"] == "REJECTED"


# ── get_edge_audit_trail ──────────────────────────────────────────────────────


async def test_get_edge_audit_trail(mock_user: dict[str, Any]) -> None:
    """Get audit trail for an edge returns trail entries."""
    edge_id = uuid4()
    mock_entry = MagicMock()

    with patch(
        "src.api.semantic._registry.get_edge_audit_trail",
        new_callable=AsyncMock,
        return_value=[mock_entry],
    ):
        result = await get_edge_audit_trail(edge_id=edge_id, user=mock_user)

    assert len(result) == 1


async def test_get_edge_audit_trail_not_found(mock_user: dict[str, Any]) -> None:
    """Get audit trail for missing edge raises 404."""
    edge_id = uuid4()

    with patch(
        "src.api.semantic._registry.get_edge_audit_trail",
        new_callable=AsyncMock,
        side_effect=ObjectNotFoundError(f"Edge {edge_id} not found"),
    ):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_edge_audit_trail(edge_id=edge_id, user=mock_user)
        assert exc_info.value.status_code == 404

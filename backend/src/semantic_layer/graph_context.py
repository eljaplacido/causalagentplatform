"""Helpers for loading graph fragments with resolved edge payloads."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.exceptions import ObjectNotFoundError
from src.semantic_layer.registry import SemanticLayerRegistry


async def load_fragment_with_edges(
    fragment_id: str | UUID,
    *,
    require_approved: bool = True,
) -> dict[str, Any] | None:
    """Return a graph fragment plus resolved edge payloads."""
    registry = SemanticLayerRegistry()
    fragment = await registry.get_fragment(UUID(str(fragment_id)))

    if require_approved and str(fragment.approval_status) != "APPROVED":
        return None

    resolved_edges: list[dict[str, Any]] = []
    missing_edge_refs: list[str] = []
    for edge_id in fragment.edges:
        try:
            edge = await registry.get_edge(edge_id)
            resolved_edges.append(edge.model_dump(mode="json"))
        except ObjectNotFoundError:
            missing_edge_refs.append(str(edge_id))

    payload = fragment.model_dump(mode="json")
    payload["edge_refs"] = list(payload.get("edges", []))
    payload["edges"] = resolved_edges
    payload["missing_edge_refs"] = missing_edge_refs
    return payload

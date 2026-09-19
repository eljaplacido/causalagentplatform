"""
Adapter between the backend's graph fragments and the causal-core kernel.

Fragments are the semantic layer's dicts::

    {"approval_status": "APPROVED",
     "edges": [{"source": {"id", "object_type"}, "target": {"id", "object_type"},
                "edge_type": "CAUSES" | "MEDIATES" | "CONFOUNDS" | "MODERATES" | "BLOCKS",
                "direction": "FORWARD" | "BACKWARD" (optional), "confidence": float}]}

Every edge type above is a directed influence from source to target, so each
becomes one directed edge of a causal-core graph. Node ids are used in full --
the old DoWhy path truncated them to eight characters, which silently failed to
match longer column names.
"""

from __future__ import annotations

from typing import Any

from causal_core.contract import CausalGraph, Edge, EdgeOrigin, EdgeStatus


def treatment_id(fragment: dict[str, Any]) -> str | None:
    for edge in fragment.get("edges", []):
        source = edge.get("source", {})
        if source.get("object_type") == "Treatment":
            return str(source.get("id"))
    return None


def outcome_id(fragment: dict[str, Any]) -> str | None:
    for edge in fragment.get("edges", []):
        target = edge.get("target", {})
        if target.get("object_type") == "Outcome":
            return str(target.get("id"))
    return None


def fragment_to_graph(fragment: dict[str, Any]) -> CausalGraph:
    """An APPROVED fragment's edges are approved causal claims; anything else is proposed."""
    approved = fragment.get("approval_status") == "APPROVED"
    status = EdgeStatus.approved if approved else EdgeStatus.proposed
    nodes: dict[str, None] = {}
    edges: list[Edge] = []
    for e in fragment.get("edges", []):
        a = str(e.get("source", {}).get("id"))
        b = str(e.get("target", {}).get("id"))
        if e.get("direction") == "BACKWARD":
            a, b = b, a
        nodes[a] = None
        nodes[b] = None
        edges.append(Edge(source=a, target=b, origin=EdgeOrigin.human, status=status))
    return CausalGraph(nodes=tuple(nodes), edges=tuple(edges))

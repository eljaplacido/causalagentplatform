"""
Provenance API — trace and export agentic provenance records.

Endpoints:
  GET  /api/v1/provenance/trace/{session_id}   — trace full provenance chain
  GET  /api/v1/provenance/export/{session_id}  — PROV-JSON export of provenance graph
  GET  /api/v1/provenance/invocations/{session_id} — LLM invocation metadata
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.middleware.rbac import guard
from src.semantic_layer.provenance import (
    LLMInvocation,
    ProvenanceGraph,
    ProvenanceLink,
    ProvenanceRecord,
    ProvenanceRecordType,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/provenance", tags=["Provenance"])

# ── In-memory store (SQLite-backed in production; session-scoped for dev) ──────
# Keyed by session_id for simplicity. Production replaces with Neo4j + PostgreSQL.

_provenance_store: dict[str, list[ProvenanceRecord]] = {}
_invocation_store: dict[str, list[LLMInvocation]] = {}


# ── Store helpers (called by llm_factory.py and agents) ────────────────────────


def store_provenance_record(record: ProvenanceRecord) -> None:
    """Append a provenance record to the in-memory store for its session."""
    sid = record.session_id
    _provenance_store.setdefault(sid, []).append(record)


def store_llm_invocation(invocation: LLMInvocation) -> None:
    """Append an LLM invocation record to the in-memory store for its session."""
    sid = invocation.session_id
    _invocation_store.setdefault(sid, []).append(invocation)


def get_provenance_graph(session_id: str) -> ProvenanceGraph:
    """Build a ProvenanceGraph from stored records and invocations."""
    records = _provenance_store.get(session_id, [])
    invocations = _invocation_store.get(session_id, [])

    links = _build_links(records)
    gap_flags = _detect_gaps(records)

    return ProvenanceGraph(
        session_id=session_id,
        records=records,
        links=links,
        llm_invocations=invocations,
        gap_flags=gap_flags,
    )


def _build_links(records: list[ProvenanceRecord]) -> list[ProvenanceLink]:
    """Build PROV-compliant links from provenance records."""
    links: list[ProvenanceLink] = []
    for r in records:
        if r.was_generated_by:
            links.append(
                ProvenanceLink(
                    source=r.id,
                    target=r.was_generated_by,
                    relation_type="wasGeneratedBy",
                )
            )
        for derived in r.was_derived_from:
            links.append(
                ProvenanceLink(
                    source=r.id,
                    target=derived,
                    relation_type="wasDerivedFrom",
                )
            )
        if r.was_attributed_to:
            links.append(
                ProvenanceLink(
                    source=r.id,
                    target=r.was_attributed_to,
                    relation_type="wasAttributedTo",
                )
            )
        for used_ref in r.used:
            links.append(
                ProvenanceLink(
                    source=r.id,
                    target=used_ref,
                    relation_type="used",
                )
            )
    return links


def _detect_gaps(records: list[ProvenanceRecord]) -> list[str]:
    """Detect provenance gaps in the record set."""
    gaps: list[str] = []
    for r in records:
        if r.gap_status == "PROV_GAP":
            gaps.append(f"Gap in record {r.id}: {r.gap_status}")
        # Check for dangling references
        if r.was_generated_by and not _has_record(records, r.was_generated_by):
            gaps.append(f"Dangling wasGeneratedBy in record {r.id}")
        if r.was_attributed_to and not _has_record(records, r.was_attributed_to):
            gaps.append(f"Dangling wasAttributedTo in record {r.id}")
    return gaps


def _has_record(records: list[ProvenanceRecord], target_id: UUID) -> bool:
    return any(r.id == target_id for r in records)


# ── Response models ────────────────────────────────────────────────────────────


class TraceResponse(BaseModel):
    session_id: str
    record_count: int
    invocation_count: int
    gap_count: int
    records: list[dict[str, Any]]
    invocations: list[dict[str, Any]]
    incomplete: bool


class ProvJsonResponse(BaseModel):
    prefix: dict[str, str]  # PROV namespace prefixes
    entity: list[dict[str, Any]]
    activity: list[dict[str, Any]]
    agent: list[dict[str, Any]]
    wasGeneratedBy: list[dict[str, Any]]
    wasDerivedFrom: list[dict[str, Any]]
    wasAttributedTo: list[dict[str, Any]]
    used: list[dict[str, Any]]


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "/trace/{session_id}",
    response_model=TraceResponse,
    summary="Trace full provenance chain for a session",
)
async def trace_provenance(
    session_id: str,
    include_invocations: bool = Query(default=True),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/provenance/trace/{session_id}")),
) -> TraceResponse:
    """
    Return the full provenance chain for a session.

    Includes all ProvenanceRecords and optionally LLM invocations.
    """
    graph = get_provenance_graph(session_id)

    return TraceResponse(
        session_id=graph.session_id,
        record_count=len(graph.records),
        invocation_count=len(graph.llm_invocations) if include_invocations else 0,
        gap_count=len(graph.gap_flags),
        records=[r.model_dump(mode="json") for r in graph.records],
        invocations=(
            [i.model_dump(mode="json") for i in graph.llm_invocations]
            if include_invocations
            else []
        ),
        incomplete=not graph.is_complete,
    )


@router.get(
    "/export/{session_id}",
    response_model=ProvJsonResponse,
    summary="Export provenance as W3C PROV-JSON",
)
async def export_prov_json(
    session_id: str,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/provenance/export/{session_id}")),
) -> ProvJsonResponse:
    """
    Export the provenance graph as W3C PROV-JSON format.

    Compatible with standard PROV validators and visualisers.
    """
    graph = get_provenance_graph(session_id)
    records = graph.records

    entities = [
        r.model_dump(mode="json")
        for r in records
        if r.record_type == ProvenanceRecordType.ENTITY
    ]
    activities = [
        r.model_dump(mode="json")
        for r in records
        if r.record_type == ProvenanceRecordType.ACTIVITY
    ]
    agents_data = [
        r.model_dump(mode="json")
        for r in records
        if r.record_type == ProvenanceRecordType.AGENT
    ]

    links = graph.links

    return ProvJsonResponse(
        prefix={
            "prov": "http://www.w3.org/ns/prov#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
        },
        entity=entities,
        activity=activities,
        agent=agents_data,
        wasGeneratedBy=[
            {"prov:entity": str(l.source), "prov:activity": str(l.target)}
            for l in links
            if l.relation_type == "wasGeneratedBy"
        ],
        wasDerivedFrom=[
            {"prov:generatedEntity": str(l.source), "prov:usedEntity": str(l.target)}
            for l in links
            if l.relation_type == "wasDerivedFrom"
        ],
        wasAttributedTo=[
            {"prov:entity": str(l.source), "prov:agent": str(l.target)}
            for l in links
            if l.relation_type == "wasAttributedTo"
        ],
        used=[
            {"prov:activity": str(l.source), "prov:entity": str(l.target)}
            for l in links
            if l.relation_type == "used"
        ],
    )


@router.get(
    "/invocations/{session_id}",
    response_model=list[dict[str, Any]],
    summary="List LLM invocations for a session",
)
async def list_invocations(
    session_id: str,
    capability: str | None = Query(default=None),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/provenance/invocations/{session_id}")),
) -> list[dict[str, Any]]:
    """
    List all LLM invocations for a session, optionally filtered by capability.
    """
    invocations = _invocation_store.get(session_id, [])
    if capability:
        invocations = [i for i in invocations if i.capability == capability]
    return [i.model_dump(mode="json") for i in invocations]


@router.delete(
    "/session/{session_id}",
    status_code=204,
    summary="Clear provenance data for a session (test cleanup)",
)
async def clear_session(
    session_id: str,
    user: dict[str, Any] = Depends(guard("DELETE", "/api/v1/provenance/session/{session_id}")),
) -> None:
    """Remove all provenance and invocation data for a session."""
    _provenance_store.pop(session_id, None)
    _invocation_store.pop(session_id, None)

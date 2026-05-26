"""
Unit tests for provenance models and API (src/semantic_layer/provenance.py,
src/api/provenance.py).

Tests:
  1. LLMInvocation model validation
  2. ProvenanceRecord model validation + PROV fields
  3. ProvenanceGraph construction + gap detection
  4. Store helpers (store_provenance_record, store_llm_invocation)
  5. get_provenance_graph with mixed records
  6. _build_links and _detect_gaps
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.semantic_layer.provenance import (
    LLMCapability,
    LLMInvocation,
    LLMProvider,
    ProvenanceGapStatus,
    ProvenanceGraph,
    ProvenanceLink,
    ProvenanceRecord,
    ProvenanceRecordType,
)
from src.api.provenance import (
    _build_links,
    _detect_gaps,
    get_provenance_graph,
    store_llm_invocation,
    store_provenance_record,
)


# ── LLMInvocation ─────────────────────────────────────────────────────────────


def test_llm_invocation_defaults() -> None:
    """LLMInvocation creates with mandatory fields and sensible defaults."""
    inv = LLMInvocation(
        session_id="sess-1",
        agent_id="agent-1",
        capability=LLMCapability.CAUSAL_REASONING,
        provider=LLMProvider.OLLAMA,
        model_name="qwen3.5:122b",
    )
    assert inv.session_id == "sess-1"
    assert inv.agent_id == "agent-1"
    assert inv.capability == LLMCapability.CAUSAL_REASONING
    assert inv.provider == LLMProvider.OLLAMA
    assert inv.model_name == "qwen3.5:122b"
    assert inv.temperature == 0.0
    assert inv.max_tokens == 4096
    assert inv.input_token_count == 0
    assert inv.output_token_count == 0
    assert inv.latency_ms == 0.0
    assert inv.context_window_truncated is False


def test_llm_invocation_full() -> None:
    """LLMInvocation accepts all optional fields."""
    inv = LLMInvocation(
        session_id="sess-2",
        agent_id="agent-2",
        capability=LLMCapability.DOCUMENT_EXTRACTION,
        provider=LLMProvider.ANTHROPIC,
        model_name="claude-sonnet",
        prompt_template_id="prompts/causal_propose_edge.yaml",
        prompt_template_hash="abc123",
        temperature=0.7,
        max_tokens=8192,
        input_token_count=1500,
        output_token_count=350,
        latency_ms=1250.5,
        context_window_truncated=True,
    )
    assert inv.prompt_template_id == "prompts/causal_propose_edge.yaml"
    assert inv.prompt_template_hash == "abc123"
    assert inv.temperature == 0.7
    assert inv.input_token_count == 1500
    assert inv.output_token_count == 350
    assert inv.latency_ms == 1250.5
    assert inv.context_window_truncated is True


# ── ProvenanceRecord ──────────────────────────────────────────────────────────


def test_provenance_record_defaults() -> None:
    """ProvenanceRecord creates with required fields and type defaults."""
    record = ProvenanceRecord(
        session_id="sess-1",
        agent_id="agent-1",
    )
    assert record.record_type == ProvenanceRecordType.ENTITY
    assert record.gap_status == ProvenanceGapStatus.COMPLETE
    assert record.was_generated_by is None
    assert record.was_derived_from == []
    assert record.was_attributed_to is None
    assert record.used == []
    assert record.tool_calls == []


def test_provenance_record_entity() -> None:
    """ProvenanceRecord as ENTITY with PROV links."""
    activity_id = uuid4()
    agent_id = uuid4()
    derived_id = uuid4()

    record = ProvenanceRecord(
        session_id="sess-1",
        agent_id="agent-1",
        record_type=ProvenanceRecordType.ENTITY,
        was_generated_by=activity_id,
        was_derived_from=[derived_id],
        was_attributed_to=agent_id,
        model_identifier="qwen3.5:122b",
        prompt_version="v1.0.0",
    )
    assert record.was_generated_by == activity_id
    assert derived_id in record.was_derived_from
    assert record.was_attributed_to == agent_id
    assert record.model_identifier == "qwen3.5:122b"


def test_provenance_record_activity() -> None:
    """ProvenanceRecord as ACTIVITY with used entities."""
    used_ids = [uuid4(), uuid4()]

    record = ProvenanceRecord(
        session_id="sess-1",
        agent_id="agent-1",
        record_type=ProvenanceRecordType.ACTIVITY,
        used=used_ids,
    )
    assert len(record.used) == 2
    assert record.used == used_ids


def test_provenance_record_gap() -> None:
    """ProvenanceRecord with gap status."""
    record = ProvenanceRecord(
        session_id="sess-1",
        agent_id="agent-1",
        gap_status=ProvenanceGapStatus.PROV_GAP,
    )
    assert record.gap_status == ProvenanceGapStatus.PROV_GAP


# ── Store helpers ─────────────────────────────────────────────────────────────


def test_store_and_retrieve_provenance() -> None:
    """Store provenance records and retrieve via get_provenance_graph."""
    sid = "test-session-store"
    rec = ProvenanceRecord(session_id=sid, agent_id="a1")

    store_provenance_record(rec)
    graph = get_provenance_graph(sid)

    assert graph.session_id == sid
    assert len(graph.records) == 1
    assert graph.records[0].id == rec.id


def test_store_and_retrieve_invocations() -> None:
    """Store LLM invocations and retrieve via get_provenance_graph."""
    sid = "test-session-inv"
    inv = LLMInvocation(
        session_id=sid,
        agent_id="a1",
        capability=LLMCapability.FAST_CLASSIFICATION,
        provider=LLMProvider.OLLAMA,
        model_name="phi4:14b",
    )

    store_llm_invocation(inv)
    graph = get_provenance_graph(sid)

    assert len(graph.llm_invocations) == 1
    assert graph.llm_invocations[0].model_name == "phi4:14b"


# ── ProvenanceGraph ───────────────────────────────────────────────────────────


def test_provenance_graph_empty() -> None:
    """Empty provenance graph has no records and is complete."""
    graph = get_provenance_graph("nonexistent-session")
    assert len(graph.records) == 0
    assert len(graph.llm_invocations) == 0
    assert graph.is_complete is True
    assert len(graph.gap_flags) == 0


def test_provenance_graph_with_gaps() -> None:
    """ProvenanceGraph detects dangling references."""
    sid = "test-session-gaps"
    gap_record = ProvenanceRecord(
        session_id=sid,
        agent_id="a1",
        gap_status=ProvenanceGapStatus.PROV_GAP,
    )
    store_provenance_record(gap_record)

    # Record with dangling wasAttributedTo
    orphan_id = uuid4()
    orphan_record = ProvenanceRecord(
        session_id=sid,
        agent_id="a1",
        was_attributed_to=orphan_id,
    )
    store_provenance_record(orphan_record)

    # Clear previous stores to isolate
    graph = get_provenance_graph(sid)

    assert not graph.is_complete
    assert len(graph.gap_flags) > 0


# ── _build_links ──────────────────────────────────────────────────────────────


def test_build_links_generated_by() -> None:
    """Link created for wasGeneratedBy."""
    activity_id = uuid4()
    records = [
        ProvenanceRecord(
            session_id="s",
            agent_id="a",
            record_type=ProvenanceRecordType.ENTITY,
            was_generated_by=activity_id,
        )
    ]
    links = _build_links(records)
    assert len(links) == 1
    assert links[0].relation_type == "wasGeneratedBy"
    assert links[0].source == records[0].id
    assert links[0].target == activity_id


def test_build_links_multiple_types() -> None:
    """Multiple link types are all built."""
    activity_id = uuid4()
    agent_id = uuid4()
    derived_id = uuid4()
    used_id = uuid4()

    record = ProvenanceRecord(
        session_id="s",
        agent_id="a",
        was_generated_by=activity_id,
        was_derived_from=[derived_id],
        was_attributed_to=agent_id,
        used=[used_id],
    )
    links = _build_links([record])
    assert len(links) == 4  # one per relation type

    link_types = {l.relation_type for l in links}
    assert "wasGeneratedBy" in link_types
    assert "wasDerivedFrom" in link_types
    assert "wasAttributedTo" in link_types
    assert "used" in link_types


# ── _detect_gaps ──────────────────────────────────────────────────────────────


def test_detect_gaps_no_gaps() -> None:
    """No gaps detected when all references are valid."""
    activity = ProvenanceRecord(
        session_id="s",
        agent_id="a",
        record_type=ProvenanceRecordType.ACTIVITY,
    )
    entity = ProvenanceRecord(
        session_id="s",
        agent_id="a",
        record_type=ProvenanceRecordType.ENTITY,
        was_generated_by=activity.id,
    )
    gaps = _detect_gaps([activity, entity])
    assert len(gaps) == 0


def test_detect_gaps_explicit_gap() -> None:
    """Explicit PROV_GAP status is detected."""
    record = ProvenanceRecord(
        session_id="s",
        agent_id="a",
        gap_status=ProvenanceGapStatus.PROV_GAP,
    )
    gaps = _detect_gaps([record])
    assert len(gaps) >= 1
    assert any("PROV_GAP" in g for g in gaps)


def test_detect_gaps_dangling_ref() -> None:
    """Dangling references are detected."""
    orphan_id = uuid4()
    record = ProvenanceRecord(
        session_id="s",
        agent_id="a",
        was_generated_by=orphan_id,
    )
    gaps = _detect_gaps([record])
    assert len(gaps) >= 1
    assert any("Dangling" in g for g in gaps)


# ── ProvenanceGraph property ──────────────────────────────────────────────────


def test_provenance_graph_is_complete_property() -> None:
    """is_complete is True when no gap flags."""
    graph = ProvenanceGraph(
        session_id="test",
        gap_flags=[],
    )
    assert graph.is_complete is True

    graph_with_gaps = ProvenanceGraph(
        session_id="test",
        gap_flags=["Gap in record X"],
    )
    assert graph_with_gaps.is_complete is False

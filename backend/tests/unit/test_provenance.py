"""
Unit tests for provenance models and API (src/semantic_layer/provenance.py,
src/api/provenance.py).

Tests:
  1. LLMInvocation model validation
  2. ProvenanceRecord model validation + PROV fields
  3. ProvenanceGraph construction + gap detection
  4. build_graph over records and invocations (the durable store's round trip
     is tests/integration/test_provenance_append_only.py)
  5. _build_links and _detect_gaps
  6. The records each flow writes (src/semantic_layer/provenance_capture.py)
"""

from __future__ import annotations

from uuid import uuid4

from src.api.provenance import (
    _build_links,
    _detect_gaps,
    build_graph,
)
from src.semantic_layer.models import EpistemicState, ReasoningStep
from src.semantic_layer.provenance import (
    LLMCapability,
    LLMInvocation,
    LLMProvider,
    ProvenanceGapStatus,
    ProvenanceGraph,
    ProvenanceRecord,
    ProvenanceRecordType,
)
from src.semantic_layer.provenance_capture import (
    decision_records,
    digest,
    guardian_check_record,
    session_run_records,
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


def test_build_graph_keeps_records() -> None:
    """build_graph carries the records it is given."""
    sid = "test-session-store"
    rec = ProvenanceRecord(session_id=sid, agent_id="a1")

    graph = build_graph(sid, [rec], [])

    assert graph.session_id == sid
    assert len(graph.records) == 1
    assert graph.records[0].id == rec.id


def test_build_graph_keeps_invocations() -> None:
    """build_graph carries the invocations it is given."""
    sid = "test-session-inv"
    inv = LLMInvocation(
        session_id=sid,
        agent_id="a1",
        capability=LLMCapability.FAST_CLASSIFICATION,
        provider=LLMProvider.OLLAMA,
        model_name="phi4:14b",
    )

    graph = build_graph(sid, [], [inv])

    assert len(graph.llm_invocations) == 1
    assert graph.llm_invocations[0].model_name == "phi4:14b"


# ── ProvenanceGraph ───────────────────────────────────────────────────────────


def test_provenance_graph_empty() -> None:
    """Empty provenance graph has no records and is complete."""
    graph = build_graph("nonexistent-session", [], [])
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
    orphan_record = ProvenanceRecord(
        session_id=sid,
        agent_id="a1",
        was_attributed_to=uuid4(),
    )

    graph = build_graph(sid, [gap_record, orphan_record], [])

    assert not graph.is_complete
    assert len(graph.gap_flags) == 2


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

    link_types = {link.relation_type for link in links}
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


# ── What the flows record ─────────────────────────────────────────────────────


def _step(node: str, outputs: dict[str, object]) -> ReasoningStep:
    return ReasoningStep(node=node, action=node.upper(), inputs={"x": 1}, outputs=outputs)


def test_session_run_output_cites_the_guardian_row_of_this_run() -> None:
    """The output cites the audit row of the run's last GuardianCheck step, and the
    steps a resumed session already had are not recorded again."""
    audit_id = uuid4()
    earlier = _step("GuardianCheck", {"audit_entry_id": str(uuid4())})
    initial = EpistemicState(
        session_id="s", agent_id="agent-x", reasoning_steps=[earlier], draft_explanation="words"
    )
    final = initial.model_copy(
        update={
            "reasoning_steps": [
                earlier,
                _step("CausalAnalysis", {}),
                _step("GuardianCheck", {"audit_entry_id": str(audit_id)}),
            ]
        }
    )

    records = session_run_records(
        initial, final, route="POST /api/v1/session/run", user_id="u1", resumed=True
    )

    graph = build_graph("s", records, [])
    assert graph.is_complete, graph.gap_flags
    output = records[-1]
    assert output.guardian_verdict_ref == audit_id
    steps = [r for r in records if r.attributes.get("part_of")]
    assert [r.attributes["node"] for r in steps] == ["CausalAnalysis", "GuardianCheck"]
    assert records[1].attributes["description_sha256"] == digest("words")
    assert "words" not in str([r.model_dump() for r in records])


def test_session_run_without_a_guardian_step_cites_no_row() -> None:
    state = EpistemicState(session_id="s", agent_id="a")
    records = session_run_records(state, state, route="r", user_id=None, resumed=False)
    assert records[-1].guardian_verdict_ref is None


def test_guardian_record_cites_its_audit_row() -> None:
    audit_id = uuid4()
    record = guardian_check_record(
        session_id="s",
        agent_id="guardian",
        audit_entry_id=audit_id,
        verdict="REJECTED",
        policy_rules_cited=["PR-006"],
        action_type="EDGE_PROPOSAL",
        payload_hash="h",
    )
    assert record.record_type == ProvenanceRecordType.ACTIVITY
    assert record.guardian_verdict_ref == audit_id
    assert record.attributes["policy_rules_cited"] == ["PR-006"]


def test_decision_records_link_data_and_graph_to_the_entry() -> None:
    sid, records = decision_records(
        entry_digest="e" * 64,
        sequence=3,
        verdict="experiment",
        decision_digest="d",
        outcome_kind="result",
        data={"data_sha256": "x"},
        graph={"graph_sha256": "y"},
        analysis={"causal_core_version": "causal_core 0"},
        user_id="u",
    )
    assert sid == "decision:" + "e" * 64
    agent, data, graph_entity, activity, decided = records
    assert set(activity.used) == {data.id, graph_entity.id}
    assert decided.was_generated_by == activity.id
    assert decided.attributes["sequence"] == 3
    assert build_graph(sid, records, []).is_complete

"""
Provenance and LLM invocation models for agentic data lineage.

Implements PROV-AGENT-aligned provenance records capturing the full chain
from source data → model invocation → tool use → reasoning → final artefact,
plus LLM invocation metadata for every model call in an agent reasoning cycle.

Object types (DATA_LAYER.md §2):
  - ProvenanceRecord  — W3C PROV-compliant graph node (Entity / Activity / Agent)
  - LLMInvocation      — Metadata for a single LLM call within an agent session
  - ProvenanceGraph    — Aggregated provenance view for export (PROV-JSON)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ── Shared serialisation config (matches models.py) ────────────────────────────

def _camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


_SHARED_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=_camel,
    from_attributes=True,
)


# ── Enumerations ───────────────────────────────────────────────────────────────


class ProvenanceRecordType(StrEnum):
    ENTITY = "ENTITY"
    ACTIVITY = "ACTIVITY"
    AGENT = "AGENT"


class LLMCapability(StrEnum):
    CAUSAL_REASONING = "causal_reasoning"
    FAST_CLASSIFICATION = "fast_classification"
    DOCUMENT_EXTRACTION = "document_extraction"
    CODE_GENERATION = "code_generation"
    PLANNING = "planning"


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"


class ProvenanceGapStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PROV_GAP = "PROV_GAP"


# ── LLM Invocation Metadata ────────────────────────────────────────────────────


class LLMInvocation(BaseModel):
    """
    Metadata for a single LLM call within an agent reasoning cycle.

    Captured automatically by llm_factory.py on every call.
    Mirrors DATA_LAYER.md §2 LLMInvocation interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    capability: LLMCapability
    provider: LLMProvider
    model_name: str = Field(..., min_length=1)
    prompt_template_id: str | None = None
    prompt_template_hash: str | None = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(4096, gt=0)
    input_token_count: int = Field(0, ge=0)
    output_token_count: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    context_window_truncated: bool = False


# ── Provenance Record ──────────────────────────────────────────────────────────


class ProvenanceRecord(BaseModel):
    """
    A W3C PROV-compliant node in an agentic provenance graph.

    Mirrors the PROV-AGENT proposal (arXiv 2508.02866) and extends
    W3C PROV with agentic-specific fields (prompt version, model identifier,
    tool calls, inference params).

    Mirrors DATA_LAYER.md §2 ProvenanceRecord interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    record_type: ProvenanceRecordType = ProvenanceRecordType.ENTITY

    # ── W3C PROV core fields ───────────────────────────────────────────────────
    was_generated_by: UUID | None = None
    was_derived_from: list[UUID] = Field(default_factory=list)
    was_attributed_to: UUID | None = None
    used: list[UUID] = Field(default_factory=list)

    # ── PROV-AGENT extensions ──────────────────────────────────────────────────
    prompt_version: str | None = None
    model_identifier: str | None = None
    inference_params: dict[str, Any] | None = None
    tool_calls: list[UUID] = Field(default_factory=list)
    context_window_truncated: bool = False

    # ── Governance ──────────────────────────────────────────────────────────────
    guardian_verdict_ref: UUID | None = None
    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    gap_status: ProvenanceGapStatus = ProvenanceGapStatus.COMPLETE
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Provenance Graph (aggregate export view) ────────────────────────────────────


class ProvenanceLink(BaseModel):
    """Directed edge in a provenance graph (for PROV-JSON serialisation)."""

    model_config = _SHARED_CONFIG

    source: UUID
    target: UUID
    relation_type: str  # "wasGeneratedBy", "wasDerivedFrom", "wasAttributedTo", "used"


class ProvenanceGraph(BaseModel):
    """
    Aggregated provenance view for a session.
    Suitable for PROV-JSON export.
    """

    model_config = _SHARED_CONFIG

    session_id: str
    records: list[ProvenanceRecord] = Field(default_factory=list)
    links: list[ProvenanceLink] = Field(default_factory=list)
    llm_invocations: list[LLMInvocation] = Field(default_factory=list)
    gap_flags: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return len(self.gap_flags) == 0

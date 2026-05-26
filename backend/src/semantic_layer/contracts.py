"""
Data contract models for runtime enforcement of data product quality.

Implements agent-consumable data contracts with freshness SLAs, schema
constraints, semantic bindings, and quality thresholds. Enforced via
Guardian policy PR-008 before agent data consumption.

Object types (DATA_LAYER.md §2):
  - DataContract     — Enforceable contract between a data product and consumer
  - FreshnessSLA     — Maximum acceptable data staleness
  - SemanticBinding  — Ontology term mapping for a field
  - ContractViolation — Recorded when a contract enforcement check fails
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Shared serialisation config ────────────────────────────────────────────────

def _camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


_SHARED_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=_camel,
    from_attributes=True,
)


# ── Enumerations ───────────────────────────────────────────────────────────────


class ContractEnforcementMode(StrEnum):
    BLOCK = "BLOCK"         # Block agent action on violation
    WARN = "WARN"           # Allow action, log warning
    LOG_ONLY = "LOG_ONLY"   # Log violation, no action taken


class ViolationType(StrEnum):
    STALENESS = "STALENESS"
    SCHEMA = "SCHEMA"
    QUALITY = "QUALITY"
    SEMANTIC = "SEMANTIC"


class MappingType(StrEnum):
    DIRECT = "DIRECT"
    DERIVED = "DERIVED"
    APPROXIMATE = "APPROXIMATE"


class ConsumerType(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class ViolationSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ── Core Contract Models ───────────────────────────────────────────────────────


class FreshnessSLA(BaseModel):
    """
    Maximum acceptable data staleness for agent consumption.

    Mirrors DATA_LAYER.md §2 FreshnessSLA interface.
    """

    model_config = _SHARED_CONFIG

    max_staleness_seconds: int = Field(300, gt=0)   # Default 5 minutes
    enforcement_window_seconds: int = Field(60, ge=0)  # Grace period
    severity: ViolationSeverity = ViolationSeverity.HIGH


class SemanticBinding(BaseModel):
    """
    Binding between a data field and an ontology term.

    Allows agents to verify they are interpreting field semantics correctly.

    Mirrors DATA_LAYER.md §2 SemanticBinding interface.
    """

    model_config = _SHARED_CONFIG

    field_name: str = Field(..., min_length=1)
    ontology_term: str = Field(..., min_length=1)
    ontology_version: str = Field(default="1.0.0")
    mapping_type: MappingType = MappingType.DIRECT


class SchemaConstraint(BaseModel):
    """Schema-level constraint on a data product field."""

    model_config = _SHARED_CONFIG

    field_name: str = Field(..., min_length=1)
    data_type: str = Field(..., min_length=1)    # "string", "number", "boolean", etc.
    required: bool = True
    allowed_values: list[str] | None = None


class QualityThreshold(BaseModel):
    """Quality dimension threshold for a data product."""

    model_config = _SHARED_CONFIG

    dimension: str = Field(..., min_length=1)     # "accuracy", "completeness", "timeliness"
    threshold: float = Field(..., ge=0.0, le=1.0)
    severity: ViolationSeverity = ViolationSeverity.MEDIUM


# ── Data Contract ──────────────────────────────────────────────────────────────


class DataContract(BaseModel):
    """
    An enforceable runtime contract between a data product and an agent consumer.

    Validated before agent data access. Violations block agent action
    in BLOCK mode under Guardian policy PR-008.

    Mirrors DATA_LAYER.md §2 DataContract interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1)
    data_product_ref: UUID
    consumer_type: ConsumerType = ConsumerType.AGENT
    schema_constraints: list[SchemaConstraint] = Field(default_factory=list)
    freshness_sla: FreshnessSLA = Field(default_factory=FreshnessSLA)
    quality_thresholds: list[QualityThreshold] = Field(default_factory=list)
    semantic_bindings: list[SemanticBinding] = Field(default_factory=list)
    enforcement_mode: ContractEnforcementMode = ContractEnforcementMode.WARN
    version: str = Field(default="1.0.0")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("enforcement_mode", mode="before")
    @classmethod
    def _coerce_enforcement_mode(cls, v: object) -> str:
        if isinstance(v, str):
            return v.upper()
        return str(v)


# ── Contract Violation ─────────────────────────────────────────────────────────


class ContractViolation(BaseModel):
    """
    Recorded when a data contract enforcement check fails.

    Written to audit_log via Guardian. Blocked violations prevent
    the agent action from proceeding.

    Mirrors DATA_LAYER.md §2 ContractViolation interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    contract_ref: UUID
    violation_type: ViolationType
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str = Field(..., min_length=1)
    blocked: bool = False
    audit_entry_ref: UUID | None = None


# ── Contract Registry ──────────────────────────────────────────────────────────


class ContractRegistry:
    """
    In-memory registry for data contracts (SQLite-backed in production).

    Provides lookup and validation of contracts at runtime.
    Production implementation should use PostgreSQL + Redis cache.
    """

    def __init__(self) -> None:
        self._contracts: dict[UUID, DataContract] = {}
        self._contracts_by_product: dict[UUID, list[UUID]] = {}

    def register(self, contract: DataContract) -> None:
        self._contracts[contract.id] = contract
        self._contracts_by_product.setdefault(contract.data_product_ref, []).append(
            contract.id
        )

    def get(self, contract_id: UUID) -> DataContract | None:
        return self._contracts.get(contract_id)

    def get_for_product(self, product_ref: UUID) -> list[DataContract]:
        ids = self._contracts_by_product.get(product_ref, [])
        return [self._contracts[cid] for cid in ids if cid in self._contracts]

    def remove(self, contract_id: UUID) -> None:
        contract = self._contracts.pop(contract_id, None)
        if contract:
            product_contracts = self._contracts_by_product.get(
                contract.data_product_ref, []
            )
            if contract_id in product_contracts:
                product_contracts.remove(contract_id)

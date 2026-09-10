"""
Unit tests for data contract models (src/semantic_layer/contracts.py).

Tests:
  1. DataContract model validation
  2. FreshnessSLA defaults and validation
  3. ContractViolation record
  4. SemanticBinding field mapping
  5. ContractRegistry CRUD operations
  6. Guardian PR-009 enforcement
"""

from __future__ import annotations

from uuid import uuid4

from src.semantic_layer.contracts import (
    ConsumerType,
    ContractEnforcementMode,
    ContractRegistry,
    ContractViolation,
    DataContract,
    FreshnessSLA,
    MappingType,
    QualityThreshold,
    SchemaConstraint,
    SemanticBinding,
    ViolationSeverity,
    ViolationType,
)

# ── FreshnessSLA ──────────────────────────────────────────────────────────────


def test_freshness_sla_defaults() -> None:
    """FreshnessSLA creates with sensible defaults."""
    sla = FreshnessSLA()
    assert sla.max_staleness_seconds == 300
    assert sla.enforcement_window_seconds == 60
    assert sla.severity == ViolationSeverity.HIGH


def test_freshness_sla_custom() -> None:
    """FreshnessSLA accepts custom values."""
    sla = FreshnessSLA(
        max_staleness_seconds=600,
        enforcement_window_seconds=120,
        severity=ViolationSeverity.CRITICAL,
    )
    assert sla.max_staleness_seconds == 600
    assert sla.enforcement_window_seconds == 120
    assert sla.severity == ViolationSeverity.CRITICAL


# ── SemanticBinding ───────────────────────────────────────────────────────────


def test_semantic_binding_direct() -> None:
    """SemanticBinding with DIRECT mapping type."""
    binding = SemanticBinding(
        field_name="response_time",
        ontology_term="devops.Service.responseTime",
        mapping_type=MappingType.DIRECT,
    )
    assert binding.field_name == "response_time"
    assert binding.ontology_term == "devops.Service.responseTime"
    assert binding.mapping_type == MappingType.DIRECT


def test_semantic_binding_approximate() -> None:
    """SemanticBinding with APPROXIMATE mapping type."""
    binding = SemanticBinding(
        field_name="cpu_usage",
        ontology_term="devops.Host.cpuUtilization",
        mapping_type=MappingType.APPROXIMATE,
        ontology_version="2.0.0",
    )
    assert binding.mapping_type == MappingType.APPROXIMATE
    assert binding.ontology_version == "2.0.0"


# ── SchemaConstraint / QualityThreshold ────────────────────────────────────────


def test_schema_constraint() -> None:
    """SchemaConstraint validates field type and required flag."""
    constraint = SchemaConstraint(
        field_name="service_name",
        data_type="string",
        required=True,
    )
    assert constraint.field_name == "service_name"
    assert constraint.data_type == "string"
    assert constraint.required is True
    assert constraint.allowed_values is None


def test_quality_threshold() -> None:
    """QualityThreshold validates dimension and threshold."""
    threshold = QualityThreshold(
        dimension="accuracy",
        threshold=0.95,
        severity=ViolationSeverity.HIGH,
    )
    assert threshold.dimension == "accuracy"
    assert threshold.threshold == 0.95
    assert threshold.severity == ViolationSeverity.HIGH


# ── DataContract ──────────────────────────────────────────────────────────────


def test_data_contract_minimal() -> None:
    """DataContract creates with minimum required fields."""
    contract = DataContract(
        name="SLO Response Time Contract",
        data_product_ref=uuid4(),
    )
    assert contract.consumer_type == ConsumerType.AGENT
    assert contract.enforcement_mode == ContractEnforcementMode.WARN
    assert contract.freshness_sla.max_staleness_seconds == 300
    assert contract.schema_constraints == []
    assert contract.quality_thresholds == []
    assert contract.semantic_bindings == []


def test_data_contract_full() -> None:
    """DataContract creates with all fields populated."""
    product_id = uuid4()
    contract = DataContract(
        name="Critical SLO Contract",
        data_product_ref=product_id,
        consumer_type=ConsumerType.AGENT,
        enforcement_mode=ContractEnforcementMode.BLOCK,
        freshness_sla=FreshnessSLA(
            max_staleness_seconds=120,
            severity=ViolationSeverity.CRITICAL,
        ),
        schema_constraints=[
            SchemaConstraint(field_name="service_name", data_type="string", required=True),
        ],
        quality_thresholds=[
            QualityThreshold(dimension="completeness", threshold=0.99),
        ],
        semantic_bindings=[
            SemanticBinding(
                field_name="response_time",
                ontology_term="devops.Service.responseTime",
            ),
        ],
        version="1.0.0",
    )
    assert contract.enforcement_mode == ContractEnforcementMode.BLOCK
    assert contract.freshness_sla.max_staleness_seconds == 120
    assert contract.freshness_sla.severity == ViolationSeverity.CRITICAL
    assert len(contract.schema_constraints) == 1
    assert len(contract.quality_thresholds) == 1
    assert len(contract.semantic_bindings) == 1


def test_data_contract_enforcement_mode_casing() -> None:
    """Enforcement mode is coerced to uppercase."""
    contract = DataContract(
        name="Test",
        data_product_ref=uuid4(),
        enforcement_mode="block",  # type: ignore[arg-type]
    )
    assert contract.enforcement_mode == ContractEnforcementMode.BLOCK


# ── ContractViolation ─────────────────────────────────────────────────────────


def test_contract_violation_staleness() -> None:
    """ContractViolation for staleness."""
    violation = ContractViolation(
        contract_ref=uuid4(),
        violation_type=ViolationType.STALENESS,
        detail="Data is 10 seconds stale — exceeds 5-second SLA",
        blocked=True,
    )
    assert violation.violation_type == ViolationType.STALENESS
    assert violation.blocked is True
    assert violation.audit_entry_ref is None


def test_contract_violation_not_blocked() -> None:
    """ContractViolation without blocking (WARN mode)."""
    violation = ContractViolation(
        contract_ref=uuid4(),
        violation_type=ViolationType.SCHEMA,
        detail="Missing optional field 'description'",
        blocked=False,
    )
    assert violation.blocked is False


# ── ContractRegistry ──────────────────────────────────────────────────────────


def test_contract_registry_register_and_get() -> None:
    """Register a contract and retrieve by ID."""
    registry = ContractRegistry()
    contract = DataContract(name="Test Contract", data_product_ref=uuid4())

    registry.register(contract)
    retrieved = registry.get(contract.id)

    assert retrieved is not None
    assert retrieved.id == contract.id
    assert retrieved.name == "Test Contract"


def test_contract_registry_get_nonexistent() -> None:
    """Get returns None for unregistered ID."""
    registry = ContractRegistry()
    assert registry.get(uuid4()) is None


def test_contract_registry_get_for_product() -> None:
    """Get contracts for a product returns all registered."""
    registry = ContractRegistry()
    product_id = uuid4()

    c1 = DataContract(name="C1", data_product_ref=product_id)
    c2 = DataContract(name="C2", data_product_ref=product_id)
    c3 = DataContract(name="C3", data_product_ref=uuid4())  # Different product

    registry.register(c1)
    registry.register(c2)
    registry.register(c3)

    product_contracts = registry.get_for_product(product_id)
    assert len(product_contracts) == 2
    names = {c.name for c in product_contracts}
    assert names == {"C1", "C2"}


def test_contract_registry_remove() -> None:
    """Remove a contract removes from both indices."""
    registry = ContractRegistry()
    product_id = uuid4()
    contract = DataContract(name="To Remove", data_product_ref=product_id)

    registry.register(contract)
    registry.remove(contract.id)

    assert registry.get(contract.id) is None
    assert len(registry.get_for_product(product_id)) == 0

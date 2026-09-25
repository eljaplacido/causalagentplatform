"""
Pydantic v2 models for the Causal Semantic Layer.

Every model mirrors the TypeScript interfaces defined in DATA_LAYER.md exactly.
All models are configured for JSON serialisation (snake_case storage, camelCase
wire format via model_config alias_generator).

Enum members intentionally match the exact string literals in the TypeScript
spec so that JSON payloads are compatible between the Python backend and the
React frontend without transformation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Shared serialisation config ───────────────────────────────────────────────

def _camel(s: str) -> str:
    """snake_case → camelCase for wire-format aliases."""
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


_SHARED_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=_camel,
    use_enum_values=True,
    from_attributes=True,
    # Pydantic v2: use field_serializer for datetime → ISO 8601, not json_encoders
    ser_json_timedelta="iso8601",
)


# ── Enumerations ──────────────────────────────────────────────────────────────


class EntityType(StrEnum):
    SERVICE = "SERVICE"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    CAMPAIGN = "CAMPAIGN"
    ACCOUNT = "ACCOUNT"
    INCIDENT = "INCIDENT"
    DEPLOYMENT = "DEPLOYMENT"
    FEATURE = "FEATURE"
    EMPLOYEE = "EMPLOYEE"
    OTHER = "OTHER"


class TreatmentType(StrEnum):
    BINARY = "BINARY"
    CONTINUOUS = "CONTINUOUS"
    MULTI_VALUED = "MULTI_VALUED"


class MeasurementType(StrEnum):
    COUNT = "COUNT"
    RATE = "RATE"
    DURATION = "DURATION"
    BINARY = "BINARY"
    CONTINUOUS = "CONTINUOUS"


class BusinessValue(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class MeasuredStatus(StrEnum):
    OBSERVED = "OBSERVED"
    PARTIALLY_OBSERVED = "PARTIALLY_OBSERVED"
    LATENT = "LATENT"


class EvidenceStrength(StrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    ASSUMED = "ASSUMED"


class MediationType(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"


class EdgeType(StrEnum):
    CAUSES = "CAUSES"
    MEDIATES = "MEDIATES"
    CONFOUNDS = "CONFOUNDS"
    MODERATES = "MODERATES"
    BLOCKS = "BLOCKS"


class EdgeDirection(StrEnum):
    FORWARD = "FORWARD"
    BACKWARD = "BACKWARD"
    BIDIRECTIONAL_FORBIDDEN = "BIDIRECTIONAL_FORBIDDEN"


class ApprovalStatus(StrEnum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    DEPRECATED = "DEPRECATED"


class FragmentApprovalStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    DEPRECATED = "DEPRECATED"


class AssumptionType(StrEnum):
    IGNORABILITY = "IGNORABILITY"
    POSITIVITY = "POSITIVITY"
    SUTVA = "SUTVA"
    EXCLUSION_RESTRICTION = "EXCLUSION_RESTRICTION"
    STRUCTURAL = "STRUCTURAL"
    DOMAIN = "DOMAIN"


class ElicitedFrom(StrEnum):
    DOMAIN_EXPERT = "DOMAIN_EXPERT"
    AGENT = "AGENT"
    LITERATURE = "LITERATURE"
    PRIOR_STUDY = "PRIOR_STUDY"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    CONTESTED = "CONTESTED"
    REJECTED = "REJECTED"


class EvidenceType(StrEnum):
    STATISTICAL_TEST = "STATISTICAL_TEST"
    DOCUMENT = "DOCUMENT"
    EXPERT_KNOWLEDGE = "EXPERT_KNOWLEDGE"
    EXPERIMENT = "EXPERIMENT"
    SIMULATION = "SIMULATION"


class GuardianVerdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUIRES_ESCALATION = "REQUIRES_ESCALATION"
    PENDING = "PENDING"


class CynefinDomain(StrEnum):
    CLEAR = "CLEAR"
    COMPLICATED = "COMPLICATED"
    COMPLEX = "COMPLEX"
    CHAOTIC = "CHAOTIC"
    DISORDER = "DISORDER"


# ── Sub-objects ───────────────────────────────────────────────────────────────


class UserRef(BaseModel):
    """Reference to a platform user (human or service account)."""

    model_config = _SHARED_CONFIG

    id: str
    email: str | None = None
    name: str | None = None


class CausalObjectRef(BaseModel):
    """Typed reference to any causal semantic object by ID."""

    model_config = _SHARED_CONFIG

    id: UUID
    object_type: str  # "CausalEntity" | "Treatment" | "Outcome" | etc.


class AttributeDef(BaseModel):
    """Definition of an attribute on a CausalEntity."""

    model_config = _SHARED_CONFIG

    data_type: str  # "number" | "string" | "boolean" | "datetime"
    description: str | None = None
    unit: str | None = None
    is_observable: bool = True


class LineageRecord(BaseModel):
    """Single lineage step in the provenance chain of a causal asset."""

    model_config = _SHARED_CONFIG

    step_type: str               # "CREATED" | "MODIFIED" | "APPROVED" | "DEPRECATED"
    actor_id: str                # agent ID or UserRef.id
    timestamp: datetime
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Constraint(BaseModel):
    """Feasibility constraint on a Treatment."""

    model_config = _SHARED_CONFIG

    constraint_type: str
    description: str
    bound_lower: float | None = None
    bound_upper: float | None = None


class CostModel(BaseModel):
    """Cost model attached to a Treatment."""

    model_config = _SHARED_CONFIG

    currency: str = "USD"
    fixed_cost: float = 0.0
    variable_cost_per_unit: float = 0.0
    cost_function: str | None = None  # e.g. "linear", "step"


class StatisticalResult(BaseModel):
    """Result of a statistical test (e.g. t-test, chi-squared)."""

    model_config = _SHARED_CONFIG

    test_name: str
    p_value: float
    statistic: float
    confidence_interval: list[float] | None = None
    sample_size: int | None = None
    degrees_of_freedom: float | None = None


class SensitivityResult(BaseModel):
    """Rosenbaum / E-value sensitivity analysis result for an Assumption."""

    model_config = _SHARED_CONFIG

    e_value: float | None = None
    rosenbaum_gamma: float | None = None
    robust_to_unmeasured_confounding: bool
    analysis_method: str
    computed_at: datetime
    computed_by: str  # agent_id or user_id


class EffectEstimate(BaseModel):
    """Expected intervention effect (attached to Intervention before execution)."""

    model_config = _SHARED_CONFIG

    point_estimate: float
    confidence_interval_lower: float
    confidence_interval_upper: float
    estimator: str
    assumption_ids_used: list[UUID] = Field(default_factory=list)
    identification_status: str  # "IDENTIFIED" | "PARTIALLY_IDENTIFIED" | "BOUNDS_ONLY"


class EffectMeasurement(BaseModel):
    """Actual measured effect (filled after outcome observation)."""

    model_config = _SHARED_CONFIG

    observed_value: float
    measurement_method: str
    measured_at: datetime
    notes: str | None = None


# ── Core Semantic Objects ─────────────────────────────────────────────────────


class CausalEntity(BaseModel):
    """
    A business-meaningful unit that can cause or be caused by something.

    Mirrors DATA_LAYER.md §2.1 CausalEntity interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=255)
    entity_type: EntityType
    domain: str = Field(..., min_length=1)
    description: str | None = None
    attributes: dict[str, AttributeDef] = Field(default_factory=dict)
    ontology_version: str = "1.0.0"
    approval_status: ApprovalStatus = ApprovalStatus.DRAFT
    created_by: UserRef
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: datetime | None = None
    approved_by: UserRef | None = None
    lineage: list[LineageRecord] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("approved_at", mode="before")
    @classmethod
    def validate_approved_at(cls, v: Any) -> Any:
        return v

    @model_validator(mode="after")
    def validate_approval_fields(self) -> CausalEntity:
        if self.approval_status == ApprovalStatus.APPROVED:
            if self.approved_by is None or self.approved_at is None:
                raise ValueError(
                    "approved_by and approved_at are required when approval_status=APPROVED"
                )
        return self


class Treatment(BaseModel):
    """
    A variable representing an action or intervention that can be applied.

    Mirrors DATA_LAYER.md §2.1 Treatment interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=255)
    entity_ref: UUID
    treatment_type: TreatmentType
    units: str | None = None
    feasibility_constraints: list[Constraint] = Field(default_factory=list)
    cost_model: CostModel | None = None
    owner_team: str


class Outcome(BaseModel):
    """
    The effect variable whose causal drivers we want to understand.

    Mirrors DATA_LAYER.md §2.1 Outcome interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=255)
    entity_ref: UUID
    measurement_type: MeasurementType
    time_horizon: str  # ISO 8601 duration, e.g. "P7D"
    business_value: BusinessValue
    slo_ref: str | None = None


class Confounder(BaseModel):
    """
    A variable that causally influences both a treatment and an outcome.

    Mirrors DATA_LAYER.md §2.1 Confounder interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1)
    treatment_ref: UUID
    outcome_ref: UUID
    measured_status: MeasuredStatus
    proxy_variables: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength


class Mediator(BaseModel):
    """
    A variable on the causal path between treatment and outcome.

    Mirrors DATA_LAYER.md §2.1 Mediator interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1)
    treatment_ref: UUID
    outcome_ref: UUID
    mediation_type: MediationType


class CausalEdge(BaseModel):
    """
    A directed, typed, evidenced causal relationship between two semantic objects.

    Invariant: guardian_verdict_ref is mandatory.  The registry enforces that the
    referenced audit entry has verdict=APPROVED before the edge is promoted.

    Mirrors DATA_LAYER.md §2.1 CausalEdge interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    source: CausalObjectRef
    target: CausalObjectRef
    edge_type: EdgeType
    direction: EdgeDirection = EdgeDirection.FORWARD
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_refs: list[UUID] = Field(default_factory=list)
    assumption_refs: list[UUID] = Field(default_factory=list)
    approval_status: ApprovalStatus = ApprovalStatus.DRAFT
    approved_by: UserRef | None = None
    approved_at: datetime | None = None
    guardian_verdict_ref: UUID
    version: str = "1.0.0"
    lineage: list[LineageRecord] = Field(default_factory=list)
    domain: str

    @model_validator(mode="after")
    def validate_no_self_edge(self) -> CausalEdge:
        if self.source.id == self.target.id:
            raise ValueError("CausalEdge source and target cannot be the same object")
        return self

    @model_validator(mode="after")
    def validate_approval_consistency(self) -> CausalEdge:
        if self.approval_status == ApprovalStatus.APPROVED:
            if self.approved_by is None or self.approved_at is None:
                raise ValueError(
                    "approved_by and approved_at are required when approval_status=APPROVED"
                )
        return self


class GraphFragment(BaseModel):
    """
    A named, versioned, reusable sub-DAG over a set of semantic objects.

    Only APPROVED fragments may be used by SimulationAgent.

    Mirrors DATA_LAYER.md §2.1 GraphFragment interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=255)
    domain: str
    description: str = ""
    edges: list[UUID] = Field(default_factory=list)
    entry_nodes: list[CausalObjectRef] = Field(default_factory=list)
    exit_nodes: list[CausalObjectRef] = Field(default_factory=list)
    approval_status: FragmentApprovalStatus = FragmentApprovalStatus.DRAFT
    version: str = "1.0.0"
    usage_count: int = 0
    tags: list[str] = Field(default_factory=list)
    guardian_verdict_ref: UUID | None = None
    created_by: UserRef
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_approved_has_verdict(self) -> GraphFragment:
        if (
            self.approval_status == FragmentApprovalStatus.APPROVED
            and self.guardian_verdict_ref is None
        ):
            raise ValueError(
                "guardian_verdict_ref is required when approval_status=APPROVED"
            )
        return self


class Assumption(BaseModel):
    """
    An explicit causal assumption that cannot be derived from data alone.

    Mirrors DATA_LAYER.md §2.1 Assumption interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    statement: str = Field(..., min_length=10)
    assumption_type: AssumptionType
    scope: list[CausalObjectRef] = Field(default_factory=list)
    justification: str = Field(..., min_length=5)
    elicited_from: ElicitedFrom
    elicited_by: UserRef
    elicited_at: datetime = Field(default_factory=datetime.utcnow)
    review_status: ReviewStatus = ReviewStatus.PENDING
    last_reviewed_at: datetime | None = None
    next_review_due: datetime | None = None
    sensitivity_result: SensitivityResult | None = None
    domain: str


class Intervention(BaseModel):
    """
    A logged, governed action taken in the real world with causal intent.

    Invariant: guardian_verdict_ref must point to an APPROVED audit entry.

    Mirrors DATA_LAYER.md §2.1 Intervention interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1)
    treatment_ref: UUID
    target_entity_ref: UUID
    initiated_by: UserRef
    initiated_at: datetime = Field(default_factory=datetime.utcnow)
    approved_by: UserRef
    approval_timestamp: datetime
    guardian_verdict_ref: UUID
    expected_effect: EffectEstimate
    actual_effect: EffectMeasurement | None = None
    outcome_observed_at: datetime | None = None
    feedback_loop_closed: bool = False

    @model_validator(mode="after")
    def validate_feedback_consistency(self) -> Intervention:
        if self.feedback_loop_closed and self.actual_effect is None:
            raise ValueError(
                "actual_effect must be set before feedback_loop_closed=True"
            )
        return self


class Evidence(BaseModel):
    """
    A citation linking a causal claim to a supporting source.

    Mirrors DATA_LAYER.md §2.1 Evidence interface.
    """

    model_config = _SHARED_CONFIG

    id: UUID = Field(default_factory=uuid4)
    evidence_type: EvidenceType
    source_ref: str = Field(..., min_length=1)   # file path, URL, or metric ID
    snippet: str
    statistical_result: StatisticalResult | None = None
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    created_by: UserRef
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEntry(BaseModel):
    """
    An immutable record in the append-only audit_log table.

    Mirrors the audit_log DDL in 001_initial.sql and the AuditEntry TS interface.
    """

    id: UUID = Field(default_factory=uuid4)
    agent_id: str = Field(..., min_length=1)
    action_type: str = Field(..., min_length=1)
    payload_hash: str = Field(..., min_length=1)
    guardian_verdict: GuardianVerdict
    policy_rules_cited: list[str] = Field(default_factory=list)
    session_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    subject_id: UUID | None = Field(
        default=None, description="The object this entry is about, when it is about one."
    )
    notes: str | None = Field(
        default=None, description="What a person said when they decided: notes, or a reason."
    )

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_camel,
        use_enum_values=True,
        json_encoders={datetime: lambda v: v.isoformat()},
        from_attributes=True,
        frozen=True,  # AuditEntry is immutable after creation
    )


# ── EpistemicState ────────────────────────────────────────────────────────────

class UncertaintySource(BaseModel):
    """A named source of uncertainty in the current reasoning cycle."""

    model_config = _SHARED_CONFIG

    source_type: str  # "DATA_GAP" | "ASSUMPTION_CONTESTED" | "MODEL_MISMATCH" | ...
    description: str
    severity: float = Field(..., ge=0.0, le=1.0)


class EdgeProposal(BaseModel):
    """A candidate edge proposed during a reasoning cycle (not yet in semantic layer)."""

    model_config = _SHARED_CONFIG

    source: CausalObjectRef
    target: CausalObjectRef
    edge_type: EdgeType
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_refs: list[UUID] = Field(default_factory=list)
    rationale: str


class ReasoningStep(BaseModel):
    """A single step in the agent's reasoning chain."""

    model_config = _SHARED_CONFIG

    node: str           # LangGraph node name
    action: str         # "CLASSIFY" | "PROPOSE_EDGE" | "GUARDIAN_CHECK" | ...
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolCall(BaseModel):
    """Log entry for a single agent skill invocation."""

    model_config = _SHARED_CONFIG

    skill_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    success: bool = True
    error: str | None = None


class Recommendation(BaseModel):
    """An actionable recommendation generated by an agent."""

    model_config = _SHARED_CONFIG

    rank: int
    action: str
    rationale: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_fragment_id: UUID | None = None
    risk_tier: str = "MEDIUM"  # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"


class SimulationResult(BaseModel):
    """Output of a SimulationAgent run."""

    model_config = _SHARED_CONFIG

    fragment_id: UUID
    treatment_id: UUID
    outcome_id: UUID
    ate_estimate: float
    confidence_interval: list[float]
    identification_status: str
    sensitivity_label: str | None = None
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class EpistemicState(BaseModel):
    """
    The single in-flight session state object shared across all agent nodes.

    Every agent receives an EpistemicState and returns an EpistemicState.
    No agent stores mutable state outside this object during a reasoning cycle.

    Mirrors AGENTS.md §4 EpistemicState interface.
    """

    model_config = _SHARED_CONFIG

    # Identity
    session_id: str
    agent_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Problem classification
    cynefin_domain: CynefinDomain = CynefinDomain.DISORDER
    domain_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # Causal evidence
    active_graph_fragment_id: str | None = None
    proposed_edges: list[EdgeProposal] = Field(default_factory=list)
    active_assumptions: list[Assumption] = Field(default_factory=list)
    evidence_citations: list[Evidence] = Field(default_factory=list)

    # Uncertainty
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty_sources: list[UncertaintySource] = Field(default_factory=list)
    hallucination_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # Governance
    guardian_verdict: GuardianVerdict = GuardianVerdict.PENDING
    policy_rules_cited: list[str] = Field(default_factory=list)

    # Reasoning chain
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    tool_call_log: list[ToolCall] = Field(default_factory=list)

    # Cycle tracking (circuit breaker)
    cycle_count: int = 0

    # Output
    recommendations: list[Recommendation] = Field(default_factory=list)
    simulation_results: list[SimulationResult] = Field(default_factory=list)
    draft_explanation: str = ""

    # Human review flag
    requires_human_review: bool = False
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hallucination_flag(self) -> EpistemicState:
        """Auto-flag for human review if the grounding-risk score exceeds threshold."""
        if self.hallucination_risk_score > 0.7:
            object.__setattr__(self, "requires_human_review", True)
            if "hallucination_risk" not in self.flags:
                object.__setattr__(self, "flags", [*self.flags, "hallucination_risk"])
        return self

    def can_write_to_semantic_layer(self) -> bool:
        """Return True only when Guardian has APPROVED the current state."""
        return self.guardian_verdict == GuardianVerdict.APPROVED

    def increment_cycle(self) -> EpistemicState:
        """Return a new EpistemicState with cycle_count incremented by 1."""
        return self.model_copy(update={"cycle_count": self.cycle_count + 1})

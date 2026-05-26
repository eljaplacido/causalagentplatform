/**
 * @causalplatform/semantic-types
 *
 * Canonical TypeScript type definitions for the Unified Enterprise Causal Platform.
 * This package is the single source of truth for all semantic layer object types,
 * shared across the frontend, backend, and agent implementations.
 *
 * Import path:
 *   import type { CausalEdge, EpistemicState } from "@causalplatform/semantic-types"
 */

export type {
  // Primitives
  UUID,
  ISO8601,
  Duration,
  ApprovalStatus,
  ReviewStatus,
  GuardianVerdict,
  CynefinDomain,
  BusinessValue,
  RiskTier,
  DriftType,
  DriftSeverity,
  CausalEntityType,
  EdgeType,
  EdgeDirection,
  TreatmentType,
  MeasurementType,
  MeasuredStatus,
  MediationType,
  AssumptionType,
  EvidenceType,
  ElicitedFrom,
  EvidenceStrength,
  UserRef,
  AttributeDef,
  LineageRecord,
  Constraint,
  CostModel,
  EffectEstimate,
  EffectMeasurement,
  StatisticalResult,
  SensitivityResult,
  CausalObjectRef,
  KafkaTopic,
  Cursor,
} from "./primitives.js";

export type {
  // Core semantic layer objects
  CausalEntity,
  Treatment,
  Outcome,
  Confounder,
  Mediator,
  Evidence,
  Assumption,
  CausalEdge,
  GraphFragment,
  Intervention,
  AuditEntry,
  CausalSemanticDraft,
} from "./entities.js";

export type {
  // EpistemicState and sub-types
  EdgeProposal,
  UncertaintySource,
  ReasoningStep,
  ToolCall,
  Recommendation,
  SimulationResult,
  EpistemicState,
} from "./epistemic.js";

export {
  // EpistemicState utilities
  createInitialEpistemicState,
  shouldEscalate,
  hasGuardianVerdict,
  isApprovedForWrite,
} from "./epistemic.js";

export type {
  // Connectors and events
  DomainEvent,
  IncidentCreatedEvent,
  DeploymentCompletedEvent,
  GraphEdgeProposedEvent,
  GovernanceVerdictIssuedEvent,
  ConnectorHealthResult,
  CausalConnector,
  KafkaTopicName,
} from "./connectors.js";

export { KAFKA_TOPICS } from "./connectors.js";

export type {
  // Skill I/O contracts
  CynefinClassifyInput,
  CynefinClassifyOutput,
  CausalProposeEdgeInput,
  CausalProposeEdgeOutput,
  CounterfactualReasonInput,
  CounterfactualReasonOutput,
  GuardianCheckInput,
  GuardianCheckOutput,
  EvidenceLinkInput,
  EvidenceLinkOutput,
  AssumptionElicitInput,
  AssumptionElicitOutput,
  DriftDetectInput,
  DriftDetectOutput,
  ExplanationPersona,
  ExplanationGenerateInput,
  ExplanationGenerateOutput,
  ConfounderScreenInput,
  ConfounderScreenOutput,
  SemanticLayerReadInput,
  SemanticLayerReadOutput,
  AuditWriteInput,
  AuditWriteOutput,
  SkillName,
} from "./skills.js";

/**
 * Shared primitive types used across the causal semantic layer.
 * These are the building blocks for all semantic layer objects.
 */

export type UUID = string;
export type ISO8601 = string;
export type Duration = string; // ISO 8601 duration, e.g. "P30D"

export type ApprovalStatus = "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "DEPRECATED";
export type ReviewStatus = "PENDING" | "ACCEPTED" | "CONTESTED" | "REJECTED";
export type GuardianVerdict = "APPROVED" | "REJECTED" | "REQUIRES_ESCALATION" | "PENDING";

export type CynefinDomain = "CLEAR" | "COMPLICATED" | "COMPLEX" | "CHAOTIC" | "DISORDER";

export type BusinessValue = "HIGH" | "MEDIUM" | "LOW";
export type RiskTier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type DriftType = "CAUSAL" | "DISTRIBUTION" | "SEMANTIC" | "CONCEPT";
export type DriftSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type CausalEntityType =
  | "SERVICE"
  | "CUSTOMER"
  | "SUPPLIER"
  | "CAMPAIGN"
  | "ACCOUNT"
  | "INCIDENT"
  | "DEPLOYMENT"
  | "FEATURE"
  | "EMPLOYEE"
  | "OTHER";

export type EdgeType = "CAUSES" | "MEDIATES" | "CONFOUNDS" | "MODERATES" | "BLOCKS";
export type EdgeDirection = "FORWARD" | "BACKWARD" | "BIDIRECTIONAL_FORBIDDEN";

export type TreatmentType = "BINARY" | "CONTINUOUS" | "MULTI_VALUED";
export type MeasurementType = "COUNT" | "RATE" | "DURATION" | "BINARY" | "CONTINUOUS";
export type MeasuredStatus = "OBSERVED" | "PARTIALLY_OBSERVED" | "LATENT";
export type MediationType = "FULL" | "PARTIAL";

export type AssumptionType =
  | "IGNORABILITY"
  | "POSITIVITY"
  | "SUTVA"
  | "EXCLUSION_RESTRICTION"
  | "STRUCTURAL"
  | "DOMAIN";

export type EvidenceType =
  | "STATISTICAL_TEST"
  | "DOCUMENT"
  | "EXPERT_KNOWLEDGE"
  | "EXPERIMENT"
  | "SIMULATION";

export type ElicitedFrom = "DOMAIN_EXPERT" | "AGENT" | "LITERATURE" | "PRIOR_STUDY";

export type EvidenceStrength = "STRONG" | "MODERATE" | "WEAK" | "ASSUMED";

export interface UserRef {
  userId: UUID;
  displayName: string;
  role: string;
}

export interface AttributeDef {
  type: "string" | "number" | "boolean" | "datetime" | "enum";
  required: boolean;
  description: string;
  enumValues?: string[];
}

export interface LineageRecord {
  step: string;
  agentId: string | null;
  userId: UserRef | null;
  timestamp: ISO8601;
  description: string;
  toolsUsed: string[];
}

export interface Constraint {
  type: string;
  description: string;
  value: unknown;
}

export interface CostModel {
  currency: string;
  fixedCost: number | null;
  variableCost: number | null;
  unit: string;
}

export interface EffectEstimate {
  point: number;
  lower95: number;
  upper95: number;
  unit: string;
  estimationMethod: string;
}

export interface EffectMeasurement {
  observed: number;
  unit: string;
  measuredAt: ISO8601;
  measuredBy: UserRef;
}

export interface StatisticalResult {
  testName: string;
  statistic: number;
  pValue: number | null;
  confidence: number | null;
  sampleSize: number | null;
  notes: string | null;
}

export interface SensitivityResult {
  method: "ROSENBAUM" | "E_VALUE" | "OTHER";
  eValue: number | null;
  rosenbaumGamma: number | null;
  interpretation: string;
  passedThreshold: boolean;
}

export type CausalObjectRef = {
  objectType: "CausalEntity" | "Treatment" | "Outcome" | "Confounder" | "Mediator";
  objectId: UUID;
  name: string;
};

export interface KafkaTopic {
  domain: string;
  entity: string;
  eventType: string;
  version: string;
}

export interface Cursor {
  lastEventId: string | null;
  timestamp: ISO8601 | null;
}

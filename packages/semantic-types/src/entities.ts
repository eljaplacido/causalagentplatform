/**
 * Core causal semantic layer object types.
 * These are the governed, versioned entities that form the causal knowledge base.
 * ALL writes require a Guardian APPROVED verdict before persistence.
 */

import type {
  UUID,
  ISO8601,
  CausalEntityType,
  TreatmentType,
  MeasurementType,
  Duration,
  MeasuredStatus,
  MediationType,
  ApprovalStatus,
  FragmentApprovalStatus,
  EdgeType,
  EdgeDirection,
  BusinessValue,
  UserRef,
  AttributeDef,
  LineageRecord,
  Constraint,
  CostModel,
  CausalObjectRef,
  EvidenceStrength,
} from "./primitives.js";

// ─── CausalEntity ────────────────────────────────────────────────────────────

/**
 * A business-meaningful unit that can cause or be caused by something.
 * Examples: service, customer, incident, deployment, campaign.
 */
export interface CausalEntity {
  id: UUID;
  name: string;
  entityType: CausalEntityType;
  domain: string;
  description: string;
  attributes: Record<string, AttributeDef>;
  ontologyVersion: string;
  approvalStatus: ApprovalStatus;
  createdBy: UserRef;
  createdAt: ISO8601;
  approvedAt: ISO8601 | null;
  approvedBy: UserRef | null;
  lineage: LineageRecord[];
  tags: string[];
}

// ─── Treatment ───────────────────────────────────────────────────────────────

/**
 * A variable that represents an action or intervention that can be applied.
 * Treatments are what we can do to change outcomes.
 */
export interface Treatment {
  id: UUID;
  name: string;
  entityRef: CausalEntity["id"];
  treatmentType: TreatmentType;
  description: string;
  units: string | null;
  feasibilityConstraints: Constraint[];
  costModel: CostModel | null;
  ownerTeam: string;
  domain: string;
  approvalStatus: ApprovalStatus;
  createdBy: UserRef;
  createdAt: ISO8601;
  lineage: LineageRecord[];
}

// ─── Outcome ─────────────────────────────────────────────────────────────────

/**
 * The effect variable whose causal drivers we want to understand.
 * Outcomes are what we care about measuring and improving.
 */
export interface Outcome {
  id: UUID;
  name: string;
  entityRef: CausalEntity["id"];
  measurementType: MeasurementType;
  description: string;
  units: string | null;
  timeHorizon: Duration;
  businessValue: BusinessValue;
  sloRef: string | null;
  domain: string;
  approvalStatus: ApprovalStatus;
  createdBy: UserRef;
  createdAt: ISO8601;
  lineage: LineageRecord[];
}

// ─── Confounder ───────────────────────────────────────────────────────────────

/**
 * A variable that causally influences both a treatment and an outcome,
 * creating spurious associations if not adjusted for.
 */
export interface Confounder {
  id: UUID;
  name: string;
  description: string;
  treatmentRef: Treatment["id"];
  outcomeRef: Outcome["id"];
  measuredStatus: MeasuredStatus;
  proxyVariables: string[];
  evidenceStrength: EvidenceStrength;
  domain: string;
  approvalStatus: ApprovalStatus;
  createdBy: UserRef;
  createdAt: ISO8601;
  lineage: LineageRecord[];
}

// ─── Mediator ────────────────────────────────────────────────────────────────

/**
 * A variable on the causal path between treatment and outcome.
 * Understanding mediators is key to mechanistic causal explanations.
 */
export interface Mediator {
  id: UUID;
  name: string;
  description: string;
  treatmentRef: Treatment["id"];
  outcomeRef: Outcome["id"];
  mediationType: MediationType;
  domain: string;
  approvalStatus: ApprovalStatus;
  createdBy: UserRef;
  createdAt: ISO8601;
  lineage: LineageRecord[];
}

// ─── Evidence ────────────────────────────────────────────────────────────────

import type { EvidenceType, StatisticalResult } from "./primitives.js";

/**
 * A citation linking a causal claim to a supporting source.
 * Every causal edge must have at least one evidence record.
 */
export interface Evidence {
  id: UUID;
  evidenceType: EvidenceType;
  sourceRef: string;        // File path, URL, metric ID, document ID
  snippet: string;
  statisticalResult: StatisticalResult | null;
  relevanceScore: number;   // 0.0 – 1.0
  createdBy: UserRef | { agentId: string };
  createdAt: ISO8601;
}

// ─── Assumption ──────────────────────────────────────────────────────────────

import type { AssumptionType, ElicitedFrom, ReviewStatus, SensitivityResult } from "./primitives.js";

/**
 * An explicit statement about the causal structure that cannot be derived
 * from data alone. All assumptions must be documented and periodically reviewed.
 */
export interface Assumption {
  id: UUID;
  statement: string;
  assumptionType: AssumptionType;
  scope: CausalObjectRef[];
  justification: string;
  elicitedFrom: ElicitedFrom;
  elicitedBy: UserRef;
  elicitedAt: ISO8601;
  reviewStatus: ReviewStatus;
  lastReviewedAt: ISO8601 | null;
  nextReviewDue: ISO8601 | null;  // 90-day review cycle
  sensitivityResult: SensitivityResult | null;
  domain: string;
}

// ─── CausalEdge ──────────────────────────────────────────────────────────────

/**
 * A directed, typed, evidenced causal relationship between two semantic objects.
 * The core unit of the causal graph. All edges must pass Guardian review.
 */
export interface CausalEdge {
  id: UUID;
  source: CausalObjectRef;
  target: CausalObjectRef;
  edgeType: EdgeType;
  direction: EdgeDirection;
  confidence: number;            // 0.0 – 1.0
  evidenceRefs: Evidence["id"][];
  assumptionRefs: Assumption["id"][];
  approvalStatus: ApprovalStatus;
  approvedBy: UserRef | null;
  approvedAt: ISO8601 | null;
  guardianVerdictRef: string;    // AuditEntry.id
  version: string;
  domain: string;
  lineage: LineageRecord[];
  createdBy: UserRef;
  createdAt: ISO8601;
}

// ─── GraphFragment ───────────────────────────────────────────────────────────

/**
 * A named, versioned, reusable sub-DAG over a set of semantic objects.
 * Graph fragments are the reusable causal assets that power simulations
 * and can be shared across teams and domains.
 */
export interface GraphFragment {
  id: UUID;
  name: string;
  domain: string;
  description: string;
  edges: CausalEdge["id"][];
  entryNodes: CausalObjectRef[];
  exitNodes: CausalObjectRef[];
  approvalStatus: FragmentApprovalStatus;
  version: string;
  usageCount: number;
  tags: string[];
  createdBy: UserRef;
  createdAt: ISO8601;
  guardianVerdictRef: string | null;  // AuditEntry.id of the approving verdict
  // graph_fragments stores approved_at / approved_by, but the backend
  // GraphFragment model does not serialise them, so no endpoint sends them.
  // Optional here so a reader must handle their absence.
  approvedBy?: UserRef | null;
  approvedAt?: ISO8601 | null;
  lineage?: LineageRecord[];
}

// ─── Intervention ────────────────────────────────────────────────────────────

import type { EffectEstimate, EffectMeasurement } from "./primitives.js";

/**
 * A logged, governed action taken in the real world with causal intent.
 * Every intervention requires prior Guardian approval and tracks actual outcomes
 * for closing the feedback loop.
 */
export interface Intervention {
  id: UUID;
  name: string;
  description: string;
  treatmentRef: Treatment["id"];
  targetEntityRef: CausalEntity["id"];
  graphFragmentRef: GraphFragment["id"];  // The causal basis used
  initiatedBy: UserRef;
  initiatedAt: ISO8601;
  approvedBy: UserRef;
  approvalTimestamp: ISO8601;
  approvalReason: string;
  guardianVerdictRef: string;    // AuditEntry.id
  expectedEffect: EffectEstimate;
  actualEffect: EffectMeasurement | null;
  outcomeObservedAt: ISO8601 | null;
  feedbackLoopClosed: boolean;
  domain: string;
}

// ─── AuditEntry ──────────────────────────────────────────────────────────────

import type { GuardianVerdict } from "./primitives.js";

/**
 * An immutable audit trail entry. Append-only — never updated or deleted.
 * Required for EU AI Act Art. 30 compliance and Guardian integrity.
 */
export interface AuditEntry {
  readonly id: UUID;
  readonly agentId: string;
  readonly actionType: string;
  readonly payloadHash: string;
  readonly guardianVerdict: GuardianVerdict;
  readonly policyRulesCited: string[];
  readonly sessionId: string | null;
  readonly createdAt: ISO8601;
  /** The object this entry is about, when it is about one. */
  readonly subjectId: UUID | null;
  /** What a person said when they decided: notes, or a reason. */
  readonly notes: string | null;
}

// ─── Draft types ─────────────────────────────────────────────────────────────

/**
 * Draft semantic objects proposed by agents or users before Guardian review.
 * These never participate in simulations until promoted to APPROVED.
 */
export type CausalSemanticDraft =
  | { draftType: "edge"; payload: Omit<CausalEdge, "id" | "approvedBy" | "approvedAt" | "guardianVerdictRef" | "lineage"> & { approvalStatus: "DRAFT" } }
  | { draftType: "entity"; payload: Omit<CausalEntity, "id" | "approvedBy" | "approvedAt" | "lineage"> & { approvalStatus: "DRAFT" } }
  | { draftType: "assumption"; payload: Omit<Assumption, "id" | "sensitivityResult" | "reviewStatus"> & { reviewStatus: "PENDING" } }
  | { draftType: "fragment"; payload: Omit<GraphFragment, "id" | "approvedBy" | "approvedAt" | "guardianVerdictRef" | "lineage" | "usageCount"> & { approvalStatus: "DRAFT" } };

/**
 * Core platform types — UI-specific extensions over the canonical schema.
 *
 * Canonical semantic layer types are imported from @causalplatform/semantic-types.
 * This file contains ONLY frontend-specific extensions and UI-facing types that
 * are not part of the canonical semantic layer spec (AGENTS.md §4).
 */

import type {
  CynefinDomain,
  EvidenceType,
  GuardianVerdict,
} from "@causalplatform/semantic-types";

// Re-export canonical enums for convenience
export type { CynefinDomain, EvidenceType, GuardianVerdict };

// ─── Confidence level (derived from numeric score) ───────────────────────────

export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";

export function toConfidenceLevel(score: number): ConfidenceLevel {
  if (score > 0.7) return "HIGH";
  if (score >= 0.4) return "MEDIUM";
  return "LOW";
}

// ─── Causal graph primitives (UI-specific view, not canonical) ────────────────
// A causal edge as the backend sends it is CausalEdgeResponse in @/lib/api
// (EdgeType and ApprovalStatus from @causalplatform/semantic-types).

export interface EdgeProposal {
  id: string;
  sourceVariable: string;
  targetVariable: string;
  confidence: number;
  evidenceCitations: Evidence[];
  statisticalTests: StatisticalTestResult[];
  proposedBy: string;
  proposedAt: string; // ISO8601
}

// ─── Assumptions (canonical Evidence type extended for UI) ────────────────────

export type AssumptionStrength = "STRONG" | "MODERATE" | "WEAK" | "SPECULATIVE";
export type AssumptionReviewStatus =
  | "CURRENT"
  | "DUE_REVIEW"
  | "OVERDUE"
  | "DRAFT";

export interface Assumption {
  id: string;
  statement: string;
  strength: AssumptionStrength;
  reviewStatus: AssumptionReviewStatus;
  lastReviewedAt: string | null;
  reviewedBy: string | null;
  scope: string;
  linkedEdgeIds: string[];
}

// ─── Evidence (canonical type extended for UI) ────────────────────────────────
// EvidenceType is the canonical one (STATISTICAL_TEST, DOCUMENT,
// EXPERT_KNOWLEDGE, EXPERIMENT, SIMULATION), the values backend Evidence sends.

export interface Evidence {
  id: string;
  type: EvidenceType;
  source: string;
  snippet: string;
  relevanceScore: number;
  url?: string;
}

export interface StatisticalTestResult {
  testName: string; // e.g. "chi-squared", "Rosenbaum E-value"
  statistic: number;
  pValue: number | null;
  eValue: number | null; // for sensitivity analysis
  significant: boolean;
  interpretation: string; // plain-language
}

// ─── Uncertainty ──────────────────────────────────────────────────────────────

export interface UncertaintySource {
  type:
    | "MISSING_CONFOUNDERS"
    | "INSUFFICIENT_DATA"
    | "EXPERT_DISAGREEMENT"
    | "MODEL_MISSPECIFICATION";
  description: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
}

// ─── Reasoning & tool calls ───────────────────────────────────────────────────

export interface ReasoningStep {
  stepIndex: number;
  description: string;
  skillInvoked: string | null;
  durationMs: number;
}

export interface ToolCall {
  toolName: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  calledAt: string; // ISO8601
  durationMs: number;
}

// ─── Recommendations & simulation ─────────────────────────────────────────────

export interface EffectEstimate {
  pointEstimate: number;
  lowerBound: number; // 95% CI lower
  upperBound: number; // 95% CI upper
  unit: string;
  direction: "POSITIVE" | "NEGATIVE" | "UNCERTAIN";
}

export interface Recommendation {
  id: string;
  title: string;
  description: string;
  estimatedEffect: EffectEstimate;
  confidence: number; // 0.0 – 1.0
  assumptions: Assumption[];
  evidenceCitations: Evidence[];
  cost?: string;
  riskTier: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
}

export interface SimulationResult {
  id: string;
  interventionTitle: string;
  estimatedEffect: EffectEstimate;
  confidence: number;
  assumptions: Assumption[];
  narrativeExplanation: string;
  sensitivityLabeled: boolean; // flagged if near assumption violation bounds
}

// ─── EpistemicState (canonical per AGENTS.md §4) ─────────────────────────────

export interface EpistemicState {
  // Identity
  sessionId: string;
  agentId: string;
  timestamp: string; // ISO8601

  // Problem classification
  cynefinDomain: CynefinDomain;
  domainConfidence: number; // 0.0 – 1.0

  // Causal evidence
  activeGraphFragmentId: string;
  proposedEdges: EdgeProposal[];
  activeAssumptions: Assumption[];
  evidenceCitations: Evidence[];

  // Uncertainty
  overallConfidence: number; // 0.0 – 1.0
  uncertaintySources: UncertaintySource[];
  hallucinationRiskScore: number; // grounding risk, 0.0 – 1.0 (Guardian PR-002)

  // Governance
  guardianVerdict: GuardianVerdict;
  policyRulesCited: string[];

  // Reasoning chain
  reasoningSteps: ReasoningStep[];
  toolCallLog: ToolCall[];

  // Output
  recommendations: Recommendation[];
  simulationResults: SimulationResult[];
  draftExplanation: string;
}

// ─── Incident types ───────────────────────────────────────────────────────────

export interface CausalHypothesis {
  id: string;
  rank: number;
  description: string;
  confidence: number; // 0.0 – 1.0
  causalChain: CausalChainLink[];
  evidenceCitations: Evidence[];
}

export interface CausalChainLink {
  variable: string;
  role: "CAUSE" | "MEDIATOR" | "EFFECT";
  confidence: number;
}

export interface Intervention {
  id: string;
  title: string;
  description: string;
  estimatedEffect: EffectEstimate;
  confidence: number;
  assumptions: Assumption[];
  cost?: string;
  riskTier: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  guardianVerdict: GuardianVerdict;
  /**
   * The human decision the interventions API recorded (GET
   * /api/v1/interventions/decisions), if any. The first decision stands: a
   * decided intervention offers no second one.
   */
  humanDecision?: "APPROVED" | "REJECTED";
  humanDecisionBy?: string;
  humanDecisionAt?: string;
  approvalReason?: string;
}

export interface Incident {
  id: string;
  title: string;
  affectedServices: string[];
  severity: "SEV1" | "SEV2" | "SEV3" | "SEV4";
  status: "ACTIVE" | "INVESTIGATING" | "RESOLVED";
  acknowledgedAt: string; // ISO8601
  cynefinDomain?: CynefinDomain;
  domainConfidence?: number;
  causalHypotheses: CausalHypothesis[];
  proposedInterventions: Intervention[];
  epistemicState?: EpistemicState;
}

// ─── Governance / Review queue ─────────────────────────────────────────────────

export type RiskTier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type ReviewObjectType =
  | "CAUSAL_EDGE"
  | "GRAPH_FRAGMENT"
  | "ASSUMPTION"
  | "INTERVENTION";
/**
 * How a review-queue row is presented. NOT the backend's ReviewStatus, which is
 * the Assumption review lifecycle (PENDING/ACCEPTED/CONTESTED/REJECTED): a queue
 * row is derived from the object's ApprovalStatus (DRAFT → PENDING,
 * UNDER_REVIEW → UNDER_REVIEW) by mapBackendItem in app/governance/page.tsx.
 */
export type ReviewQueueStatus =
  | "PENDING"
  | "UNDER_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "ESCALATED";

export interface ReviewQueueItem {
  id: string;
  objectName: string;
  objectType: ReviewObjectType;
  domain: CynefinDomain;
  riskTier: RiskTier;
  submittedBy: string;
  submittedAt: string; // ISO8601
  status: ReviewQueueStatus;
  evidenceCitations: Evidence[];
  sensitivityResults: StatisticalTestResult[];
  assumptions: Assumption[];
  epistemicState?: EpistemicState;
}

// ─── Audit log ────────────────────────────────────────────────────────────────

/**
 * One row of AuditLogPanel. A view model: the backend's audit entry is
 * AuditEntry from @causalplatform/semantic-types, mapped here by
 * toAuditLogRow in @/lib/mappers.
 */
export interface AuditLogRow {
  id: string;
  timestamp: string; // ISO8601
  agent: string;
  action: string;
  verdict: GuardianVerdict;
  policyRulesCited: string[];
  payloadHash?: string;
  actorId?: string;
  /** The semantic object the entry is about, when it is about one. */
  subjectId?: string;
}

// ─── Persona ──────────────────────────────────────────────────────────────────

export type Persona =
  | "OPERATOR"
  | "ANALYST"
  | "EXECUTIVE"
  | "GOVERNANCE"
  | "ENGINEER";

// ─── API response wrappers ─────────────────────────────────────────────────────

export interface ApiResponse<T> {
  data: T;
  meta: {
    requestId: string;
    timestamp: string;
    durationMs: number;
  };
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

// ─── Connector health (Platform Engineer persona) ─────────────────────────────

export type ConnectorType =
  | "PROMETHEUS"
  | "DATADOG"
  | "ELASTIC"
  | "POSTGRES"
  | "CUSTOM";

export interface ConnectorHealthResult {
  connectorId: string;
  connectorName: string;
  connectorType: ConnectorType;
  healthy: boolean;
  lastSuccessfulSync: string | null; // ISO8601
  lastAttemptAt: string | null; // ISO8601
  errorMessage: string | null;
  objectsMapped: number;
  syncDurationMs: number | null;
  rawOutput?: Record<string, unknown>;
}

export interface DriftAlert {
  id: string;
  domain: string;
  entityType: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  message: string;
  detectedAt: string; // ISO8601
  affectedFragmentIds: string[];
}

// ─── ThinkingIndicator stage ───────────────────────────────────────────────────

export type ThinkingStage =
  | "Classifying domain..."
  | "Analyzing causal paths..."
  | "Checking policy..."
  | "Retrieving evidence..."
  | "Running simulation..."
  | "Generating explanation..."
  | "Consulting Guardian..."
  | "Loading experience buffer...";

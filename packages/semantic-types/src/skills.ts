/**
 * Skill input/output type contracts for all platform skills.
 * Skills are atomic, testable, composable units of agent capability.
 * No agent implements business logic that duplicates a skill.
 *
 * Defined in AGENTS.md § 3.
 */

import type { CynefinDomain, GuardianVerdict, DriftType, DriftSeverity } from "./primitives.js";
import type { Assumption, Evidence, CausalEdge, GraphFragment, CausalEntity, Treatment, Outcome } from "./entities.js";
import type { EpistemicState } from "./epistemic.js";

// ─── cynefin_classify ────────────────────────────────────────────────────────

export interface CynefinClassifyInput {
  problemDescription: string;
  availableDataFeatures: string[];
  timeSensitive: boolean;
  domainContext: string;
}

export interface CynefinClassifyOutput {
  domain: CynefinDomain;
  confidence: number;
  reasoningStrategy: string;
  rationale: string;
}

// ─── causal_propose_edge ─────────────────────────────────────────────────────

export interface CausalProposeEdgeInput {
  sourceVariableId: string;
  sourceVariableName: string;
  targetVariableId: string;
  targetVariableName: string;
  datasetStatistics: Record<string, unknown>;
  domainContext: string;
  existingGraphFragmentId: string | null;
}

export interface CausalProposeEdgeOutput {
  proposedEdges: Array<{
    sourceId: string;
    targetId: string;
    confidence: number;
    statisticalTestResults: Record<string, unknown>;
    evidenceCitations: string[];
    contradictions: string[];
  }>;
  suggestedMissingVariables: string[];
  dSeparationViolations: string[];
}

// ─── counterfactual_reason ───────────────────────────────────────────────────

export interface CounterfactualReasonInput {
  structuralCausalModel: Record<string, unknown>;
  observation: Record<string, unknown>;
  query: {
    type: "INTERVENTION" | "COUNTERFACTUAL";
    variable: string;
    value: unknown;
    outcomeVariables: string[];
  };
}

export interface CounterfactualReasonOutput {
  estimatedDistribution: Record<string, number>;
  identificationStatus: "IDENTIFIED" | "PARTIALLY_IDENTIFIED" | "NOT_IDENTIFIED";
  sensitivityLabels: string[];
  assumptions: string[];
  narrative?: string;
}

// ─── intervention_estimate ───────────────────────────────────────────────────

export interface InterventionEstimateInput {
  sourceId: string;
  targetId: string;
  edgeType: "CAUSES" | "MEDIATES" | "CONFOUNDS" | "MODERATES" | "BLOCKS";
  confidence: number;
  assumptions?: Array<Record<string, unknown>>;
}

export interface InterventionEstimateOutput {
  estimatedEffect: number;
  signedEffect: number;
  confidenceInterval: [number, number] | number[];
  estimator: string;
  identificationStrategy: string;
  riskTier: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  action: string;
}

// ─── experience_buffer_query ─────────────────────────────────────────────────

export interface ExperienceBufferQueryInput {
  description: string;
  domain?: string;
  limit?: number;
  sessionId?: string;
}

export interface ExperienceBufferQueryOutput {
  similarIncidents: Array<{
    sessionId: string;
    title: string;
    summary: string;
    resolution: string;
    similarity: number;
    cynefinDomain: string;
    guardianVerdict: string;
    timestamp: string;
    source: "session_state_interim";
  }>;
  retrievalSource: "session_state_interim" | "neo4j_experience_buffer";
  candidateCount: number;
}

// ─── guardian_check ──────────────────────────────────────────────────────────

export interface GuardianCheckInput {
  actionType: string;
  actionPayload: Record<string, unknown>;
  currentState: Pick<EpistemicState, "cynefinDomain" | "overallConfidence" | "hallucinationRiskScore">;
  policyScope: string[];
  agentId: string;
  sessionId: string;
}

export interface GuardianCheckOutput {
  verdict: GuardianVerdict;
  verdictId: string;
  policyRulesCited: string[];
  reason: string;
  escalationTarget: string | null;  // Human reviewer ID if REQUIRES_ESCALATION
  timestamp: string;
}

// ─── evidence_link ───────────────────────────────────────────────────────────

export interface EvidenceLinkInput {
  claimText: string;
  edgeSpec: { sourceId: string; targetId: string } | null;
  documentCorpusRefs: string[];
  domainContext: string;
}

export interface EvidenceLinkOutput {
  evidenceItems: Array<{
    evidenceId: string;
    sourceRef: string;
    snippet: string;
    relevanceScore: number;
    evidenceType: string;
  }>;
  contradictions: string[];
  evidenceGaps: string[];  // Claims without citations
}

// ─── assumption_elicit ───────────────────────────────────────────────────────

export interface AssumptionElicitInput {
  graphFragmentId: string | null;
  domainContext: string;
  mode: "INTERVIEW" | "AUTO";
  expertResponse?: string;  // Present in INTERVIEW mode
}

export interface AssumptionElicitOutput {
  assumptions: Array<{
    statement: string;
    assumptionType: string;
    strength: "STRONG" | "MODERATE" | "WEAK";
    scope: string[];
    justification: string;
  }>;
}

// ─── drift_detect ────────────────────────────────────────────────────────────

export interface DriftDetectInput {
  referenceState: Record<string, unknown>;
  currentState: Record<string, unknown>;
  metricType: "DISTRIBUTION" | "GRAPH_STRUCTURE" | "ENTITY_DEFINITION";
  domainContext: string;
  thresholds: { warning: number; severe: number };
}

export interface DriftDetectOutput {
  driftScore: number;
  driftType: DriftType;
  severity: DriftSeverity;
  details: string;
  recommendedAction: "NO_ACTION" | "ALERT" | "ESCALATE";
  statisticalEvidence: Record<string, unknown>;
}

// ─── explanation_generate ────────────────────────────────────────────────────

export type ExplanationPersona = "EXECUTIVE" | "ANALYST" | "OPERATOR" | "COMPLIANCE";

export interface ExplanationGenerateInput {
  simulationResult: Record<string, unknown>;
  assumptionSet: Assumption[];
  targetPersona: ExplanationPersona;
  domainContext: string;
  confidenceLevel: number;
}

export interface ExplanationGenerateOutput {
  narrative: string;
  assumptionDisclosures: string[];
  confidenceRanges: string;
  caveats: string[];
  personaOptimized: ExplanationPersona;
}

// ─── confounder_screen ───────────────────────────────────────────────────────

export interface ConfounderScreenInput {
  treatmentId: string;
  outcomeId: string;
  covariateList: string[];
  domainContext: string;
  observedData: Record<string, unknown> | null;
}

export interface ConfounderScreenOutput {
  flaggedConfounders: Array<{
    variableName: string;
    evidenceBasis: string;
    severity: "HIGH" | "MEDIUM" | "LOW";
    measuredStatus: string;
  }>;
  completenessAssessment: string;
}

// ─── semantic_layer_read ─────────────────────────────────────────────────────

export interface SemanticLayerReadInput {
  query:
    | { type: "entity"; name?: string; domain?: string }
    | { type: "graph_fragment"; id?: string; domain?: string }
    | { type: "assumption"; scope?: string }
    | { type: "edge"; sourceId?: string; targetId?: string };
  approvalStatusFilter?: ("APPROVED" | "DRAFT" | "UNDER_REVIEW")[];
}

export interface SemanticLayerReadOutput {
  results: Array<CausalEntity | GraphFragment | Assumption | CausalEdge | Treatment | Outcome>;
  totalCount: number;
  truncated: boolean;
}

// ─── audit_write ─────────────────────────────────────────────────────────────

export interface AuditWriteInput {
  agentId: string;
  userId: string | null;
  actionType: string;
  payloadHash: string;
  guardianVerdict: GuardianVerdict;
  policyRulesCited: string[];
  sessionId: string;
}

export interface AuditWriteOutput {
  auditEntryId: string;
  createdAt: string;
}

// ─── Skill registry type ─────────────────────────────────────────────────────

export type SkillName =
  | "cynefin_classify"
  | "causal_propose_edge"
  | "counterfactual_reason"
  | "guardian_check"
  | "evidence_link"
  | "assumption_elicit"
  | "drift_detect"
  | "explanation_generate"
  | "confounder_screen"
  | "semantic_layer_read"
  | "audit_write"
  | "sensitivity_test"
  | "placebo_check"
  | "assumption_audit"
  | "distribution_compare"
  | "semantic_diff"
  | "alert_emit"
  | "document_search"
  | "metric_correlate"
  | "citation_validate"
  | "intervention_estimate"
  | "scenario_compare"
  | "experience_buffer_query"
  | "dependency_graph_read"
  | "risk_score"
  | "ontology_match"
  | "conflict_detect"
  | "assumption_write_draft";

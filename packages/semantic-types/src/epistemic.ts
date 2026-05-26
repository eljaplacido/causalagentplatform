/**
 * EpistemicState: the shared agent state contract.
 *
 * Every agent operates on and returns an EpistemicState object.
 * This is the single source of in-flight session truth.
 * No agent stores mutable state outside of EpistemicState during a reasoning cycle.
 *
 * Defined in AGENTS.md § 4.
 */

import type { UUID, ISO8601, CynefinDomain, GuardianVerdict } from "./primitives.js";
import type { CausalEdge, Assumption, Evidence } from "./entities.js";

// ─── Sub-types ───────────────────────────────────────────────────────────────

export interface EdgeProposal {
  proposalId: UUID;
  sourceId: UUID;
  sourceName: string;
  targetId: UUID;
  targetName: string;
  confidence: number;
  evidenceCitations: Evidence["id"][];
  proposedBy: string;         // agentId or userId
  proposedAt: ISO8601;
  status: "PENDING_REVIEW" | "ACCEPTED" | "REJECTED";
}

export interface UncertaintySource {
  source: "DATA_QUALITY" | "MODEL_SPEC" | "CONFOUNDING" | "TEMPORAL" | "DOMAIN_SHIFT" | "LLM_HALLUCINATION";
  description: string;
  magnitude: "LOW" | "MEDIUM" | "HIGH";
}

export interface ReasoningStep {
  stepIndex: number;
  node: string;             // LangGraph node name
  action: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  duration_ms: number;
  timestamp: ISO8601;
}

export interface ToolCall {
  callId: UUID;
  skillName: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown> | null;
  error: string | null;
  duration_ms: number;
  timestamp: ISO8601;
}

export interface Recommendation {
  id: UUID;
  title: string;
  description: string;
  actionType: string;
  treatmentRef: string | null;
  estimatedEffect: string;
  confidence: number;
  assumptionRefs: Assumption["id"][];
  rank: number;
  persona: "EXECUTIVE" | "ANALYST" | "OPERATOR" | "COMPLIANCE" | "ALL";
}

export interface SimulationResult {
  id: UUID;
  interventionSpec: Record<string, unknown>;
  estimatedEffect: number;
  lower95: number;
  upper95: number;
  estimationMethod: string;
  sensitivityLabeled: boolean;
  graphFragmentRef: string;
  assumptionSet: Assumption["id"][];
  createdAt: ISO8601;
}

// ─── EpistemicState ──────────────────────────────────────────────────────────

/**
 * The single state object that flows through the LangGraph state machine.
 * Prevents context fragmentation across multi-step agent cycles.
 * All agents receive this as input and return it as output (with modifications).
 */
export interface EpistemicState {
  // Identity
  sessionId: string;
  agentId: string;
  timestamp: ISO8601;

  // Problem classification (Cynefin)
  cynefinDomain: CynefinDomain;
  domainConfidence: number;          // 0.0 – 1.0

  // Causal evidence
  activeGraphFragmentId: string | null;
  proposedEdges: EdgeProposal[];
  activeAssumptions: Assumption[];
  evidenceCitations: Evidence[];

  // Uncertainty
  overallConfidence: number;
  uncertaintySources: UncertaintySource[];
  hallucinationRiskScore: number;    // H-Neuron output, 0.0 – 1.0

  // Governance
  guardianVerdict: GuardianVerdict;
  policyRulesCited: string[];
  requiresHumanReview: boolean;
  flags: string[];

  // Reasoning chain
  reasoningSteps: ReasoningStep[];
  toolCallLog: ToolCall[];
  cycleCount: number;                // Circuit breaker: max 5 before escalation

  // Output
  recommendations: Recommendation[];
  simulationResults: SimulationResult[];
  draftExplanation: string;

  // Context-specific (domain-dependent payload)
  domainContext: Record<string, unknown>;
}

// ─── Initial state factory ───────────────────────────────────────────────────

export function createInitialEpistemicState(
  sessionId: string,
  agentId: string,
  domainContext?: Record<string, unknown>,
): EpistemicState {
  return {
    sessionId,
    agentId,
    timestamp: new Date().toISOString(),
    cynefinDomain: "DISORDER",
    domainConfidence: 0,
    activeGraphFragmentId: null,
    proposedEdges: [],
    activeAssumptions: [],
    evidenceCitations: [],
    overallConfidence: 0,
    uncertaintySources: [],
    hallucinationRiskScore: 0,
    guardianVerdict: "PENDING",
    policyRulesCited: [],
    requiresHumanReview: false,
    flags: [],
    reasoningSteps: [],
    toolCallLog: [],
    cycleCount: 0,
    recommendations: [],
    simulationResults: [],
    draftExplanation: "",
    domainContext: domainContext ?? {},
  };
}

// ─── Guards ──────────────────────────────────────────────────────────────────

/** True when the state should trigger circuit breaker (max cycles exceeded) */
export function shouldEscalate(state: EpistemicState): boolean {
  return (
    state.cycleCount >= 5 ||
    state.hallucinationRiskScore > 0.7 ||
    state.cynefinDomain === "CHAOTIC"
  );
}

/** True when Guardian has given final verdict (not pending) */
export function hasGuardianVerdict(state: EpistemicState): boolean {
  return state.guardianVerdict !== "PENDING";
}

/** True when safe to write to semantic layer */
export function isApprovedForWrite(state: EpistemicState): boolean {
  return state.guardianVerdict === "APPROVED" && !state.requiresHumanReview;
}

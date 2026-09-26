/**
 * Connector interface contract and domain event types.
 * All connectors must implement CausalConnector.
 * All mapToSemanticObjects outputs are DRAFT status — no connector produces APPROVED objects.
 *
 * Defined in DATA_LAYER.md § 4.
 */

import type { ISO8601, Cursor } from "./primitives.js";
import type { CausalSemanticDraft } from "./entities.js";

// ─── Domain events ────────────────────────────────────────────────────────────

export interface DomainEvent {
  eventId: string;
  domain: string;
  entityType: string;
  eventType: string;
  payload: Record<string, unknown>;
  source: string;
  timestamp: ISO8601;
  version: string;
}

// ─── Well-known event types ──────────────────────────────────────────────────

export interface IncidentCreatedEvent extends DomainEvent {
  domain: "devops";
  eventType: "incident.created";
  payload: {
    incidentId: string;
    title: string;
    severity: "P0" | "P1" | "P2" | "P3";
    affectedServices: string[];
    metricsSnapshot: Record<string, number>;
    alertTimestamps: ISO8601[];
  };
}

export interface DeploymentCompletedEvent extends DomainEvent {
  domain: "devops";
  eventType: "deployment.completed";
  payload: {
    deploymentId: string;
    service: string;
    version: string;
    environment: string;
    completedAt: ISO8601;
    status: "SUCCESS" | "FAILED" | "PARTIAL";
    changesetRef: string;
  };
}

export interface GraphEdgeProposedEvent extends DomainEvent {
  domain: "platform";
  eventType: "graph.edge_proposed";
  payload: {
    proposalId: string;
    sourceId: string;
    targetId: string;
    agentId: string;
    confidence: number;
  };
}

export interface GovernanceVerdictIssuedEvent extends DomainEvent {
  domain: "platform";
  eventType: "governance.verdict_issued";
  payload: {
    verdictId: string;
    verdict: "APPROVED" | "REJECTED" | "REQUIRES_ESCALATION";
    subjectId: string;
    policyRules: string[];
  };
}

// ─── Connector health ────────────────────────────────────────────────────────

export interface ConnectorHealthResult {
  connectorId: string;
  healthy: boolean;
  lastSuccessfulSync: ISO8601 | null;
  errorMessage: string | null;
  fieldsMissing: string[];
  checkedAt: ISO8601;
}

// ─── Connector interface ──────────────────────────────────────────────────────

/**
 * All connectors implement this interface.
 * fetchEvents + mapToSemanticObjects is the ETL pipeline for the causal layer.
 */
export interface CausalConnector {
  readonly id: string;
  readonly domain: string;
  readonly version: string;

  /** Fetch latest events since cursor. Cursor is null for full sync. */
  fetchEvents(cursor: Cursor): Promise<DomainEvent[]>;

  /**
   * Map raw events to semantic layer draft objects.
   * Outputs are always DRAFT — never APPROVED.
   * Must strip PII and pseudonymize before emitting.
   */
  mapToSemanticObjects(events: DomainEvent[]): CausalSemanticDraft[];

  /** Check connector health and mapping coverage */
  validate(): ConnectorHealthResult;
}


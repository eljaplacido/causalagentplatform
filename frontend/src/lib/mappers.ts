/**
 * Backend response → UI view-model mappers.
 *
 * Each mapper takes the shape the backend actually serialises (see the
 * response types in @/lib/api) so that tsc, not a runtime blank screen,
 * reports a key the backend does not send. Pinned by
 * src/lib/__tests__/mappers.test.ts with real-shaped JSON.
 */

import type { AuditEntry } from "@causalplatform/semantic-types";
import type { GraphFragmentResponse, InterventionDecisionRecord } from "@/lib/api";
import type { AuditLogRow, Incident } from "@/lib/types";
import type { FragmentCardData } from "@/components/graph/FragmentCard";

/** One backend AuditEntry (camelCase) → one row of AuditLogPanel. */
export function toAuditLogRow(entry: AuditEntry): AuditLogRow {
  return {
    id: entry.id,
    timestamp: entry.createdAt,
    agent: entry.agentId,
    action: entry.actionType,
    verdict: entry.guardianVerdict,
    policyRulesCited: entry.policyRulesCited ?? [],
    payloadHash: entry.payloadHash,
    actorId: entry.sessionId ?? undefined,
    subjectId: entry.subjectId ?? undefined,
  };
}

/**
 * One backend GraphFragment (camelCase) → FragmentCard data.
 *
 * The backend sends no verdict for a fragment, only guardianVerdictRef. A ref
 * on an APPROVED fragment was checked on promotion to point at an APPROVED
 * Guardian verdict, so there it means APPROVED. A ref on any other fragment
 * says nothing the UI can read: drafts written by the registry have it
 * cleared (registry._force_draft_status), and a seeded draft's ref points at a
 * REQUIRES_ESCALATION verdict -- which the card used to show as a green
 * "Approved" chip. So a fragment that is not APPROVED shows no Guardian
 * verdict (null); its DRAFT / DEPRECATED status says where it stands.
 */
export function toFragmentCardData(fragment: GraphFragmentResponse): FragmentCardData {
  return {
    id: fragment.id,
    name: fragment.name,
    domain: fragment.domain,
    description: fragment.description,
    edgeCount: fragment.edges.length,
    usageCount: fragment.usageCount,
    version: fragment.version,
    approvalStatus: fragment.approvalStatus,
    guardianVerdict:
      fragment.approvalStatus === "APPROVED" && fragment.guardianVerdictRef ? "APPROVED" : null,
    tags: fragment.tags,
    createdAt: fragment.createdAt,
  };
}

/**
 * Put each recorded human decision (GET /api/v1/interventions/decisions) on the
 * intervention it decided, matched by exact intervention id. The Guardian's
 * verdict is left as it is: the two are different facts.
 */
export function applyInterventionDecisions(
  incident: Incident,
  decisions: readonly InterventionDecisionRecord[]
): Incident {
  const byId = new Map(decisions.map((d) => [d.intervention_id, d]));
  return {
    ...incident,
    proposedInterventions: incident.proposedInterventions.map((int) => {
      const d = byId.get(int.id);
      return d
        ? { ...int, humanDecision: d.decision, humanDecisionBy: d.decided_by, humanDecisionAt: d.recorded_at }
        : int;
    }),
  };
}

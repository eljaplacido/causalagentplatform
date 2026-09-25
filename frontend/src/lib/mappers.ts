/**
 * Backend response → UI view-model mappers.
 *
 * Each mapper takes the shape the backend actually serialises (see the
 * response types in @/lib/api) so that tsc, not a runtime blank screen,
 * reports a key the backend does not send. Pinned by
 * src/lib/__tests__/mappers.test.ts with real-shaped JSON.
 */

import type { AuditEntry } from "@causalplatform/semantic-types";
import type { GraphFragmentResponse } from "@/lib/api";
import type { AuditLogRow } from "@/lib/types";
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
 * The backend sends no verdict for a fragment, only guardianVerdictRef, and
 * sets that ref solely on promotion, after checking the referenced audit row
 * is an APPROVED verdict (registry._force_draft_status clears it on every
 * draft). So a ref means APPROVED and its absence means no verdict yet.
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
    guardianVerdict: fragment.guardianVerdictRef ? "APPROVED" : "PENDING",
    tags: fragment.tags,
    createdAt: fragment.createdAt,
  };
}

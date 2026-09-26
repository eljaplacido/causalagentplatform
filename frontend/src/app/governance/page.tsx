"use client";

/**
 * Governance Review Page — GOVERNANCE persona (US-27, US-28).
 *
 * Shows:
 *   - ReviewQueue (all draft objects awaiting approval, oldest first)
 *   - ApprovalPanel (for the selected item)
 *   - AuditLogPanel (progressive disclosure Level 3)
 *
 * Data: loads from GET /api/governance/review-queue + GET /api/governance/audit-log
 * via React Query.
 *
 * Decisions (fail-closed):
 *   - Approve: POST /api/governance/check (Guardian verdict) → POST
 *     /api/governance/approve/{id} with the returned audit_entry_id as verdict_id.
 *   - Reject:  POST /api/governance/check (APPROVED verdict for GOVERNANCE_REJECT)
 *     → POST /api/governance/reject/{id}. Both checks carry the reviewer's
 *     Cynefin domain: Guardian approves nothing in DISORDER (PR-006).
 *   - Success is shown only after the backend confirms. Nothing is patched
 *     locally: the review-queue and audit-log queries are invalidated and
 *     refetched, so the UI only ever shows backend state and backend audit rows.
 *
 * Failure mode:
 *   - If the review queue is unreachable and NEXT_PUBLIC_ENABLE_MOCK_FALLBACK is
 *     set, mock data is shown with an explicit badge and approve/reject are
 *     disabled (a mock item cannot be approved). The audit log failing alone
 *     never switches the page to mock mode.
 *   - A 401/403 is shown as "not authorised", never as an outage or mock data.
 *   - Otherwise, a degraded-state message is shown (no silent mock substitution).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, RefreshCw, AlertTriangle } from "lucide-react";
import { AuditLogPanel } from "@/components/shared/AuditLogPanel";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { ReviewQueue } from "@/components/governance/ReviewQueue";
import { ApprovalPanel } from "@/components/governance/ApprovalPanel";
import {
  governanceApi,
  isNotAuthorised,
  type ReviewQueueItemResponse,
} from "@/lib/api";
import { toAuditLogRow } from "@/lib/mappers";
import type { ReviewQueueItem, AuditLogRow, CynefinDomain } from "@/lib/types";

// ─── Mock fallback data (dev-only, gated by env flag) ───────────────────────
// NEVER silently shown in production. Must be explicitly opted-in via:
//   NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true
// Mock items are display-only: approve/reject are disabled in mock mode.

function isMockFallbackEnabled(): boolean {
  return process.env["NEXT_PUBLIC_ENABLE_MOCK_FALLBACK"] === "true";
}

const MOCK_DECISIONS_DISABLED_REASON =
  "Showing mock data because the governance backend is unavailable. " +
  "Mock items cannot be approved or rejected.";

const REVIEW_QUEUE_QUERY_KEY = ["governance", "review-queue"] as const;
const AUDIT_LOG_QUERY_KEY = ["governance", "audit-log"] as const;

const MOCK_REVIEW_ITEMS: ReviewQueueItem[] = [
  {
    id: "rev-001",
    objectName: "payment_latency → checkout_conversion",
    objectType: "CausalEdge",
    businessDomain: "payments",
    cynefinDomain: "COMPLICATED",
    riskTier: "HIGH",
    submittedBy: "ryo.tanaka",
    submittedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    status: "PENDING",
    evidenceCitations: [
      {
        id: "ev-g1",
        type: "STATISTICAL_TEST",
        source: "A/B experiment 2024-Q3",
        snippet:
          "Pearson r = -0.72 between p99 payment latency and same-session checkout conversion rate (n=14,200, p<0.001)",
        relevanceScore: 0.91,
      },
      {
        id: "ev-g2",
        type: "DOCUMENT",
        source: "Q3 Post-Mortem: Checkout Degradation",
        snippet:
          "During the August outage, checkout conversion dropped 34% in direct correlation with payment API degradation.",
        relevanceScore: 0.84,
      },
    ],
    sensitivityResults: [
      {
        testName: "Rosenbaum E-value",
        statistic: 3.2,
        pValue: null,
        eValue: 3.2,
        significant: true,
        interpretation:
          "An unmeasured confounder would need to increase the odds of the outcome by 3.2× to fully explain this association.",
      },
    ],
    assumptions: [
      {
        id: "ass-g1",
        statement:
          "There are no unmeasured third variables that simultaneously cause payment latency increases and checkout abandonment",
        strength: "MODERATE",
        reviewStatus: "CURRENT",
        lastReviewedAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
        reviewedBy: "olu.adeyemi",
        scope: "payment-checkout domain",
        linkedEdgeIds: ["edge-pc-1"],
      },
    ],
  },
  {
    id: "rev-002",
    objectName: "db_connection_pool_size → api_response_time",
    objectType: "CausalEdge",
    businessDomain: "devops",
    cynefinDomain: "CLEAR",
    riskTier: "LOW",
    submittedBy: "mihail.popescu",
    submittedAt: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    status: "PENDING",
    evidenceCitations: [
      {
        id: "ev-g3",
        type: "EXPERT_KNOWLEDGE",
        source: "Platform Engineering runbook v4.2",
        snippet:
          "Connection pool saturation is a known cause of API latency spikes across all services using the shared PostgreSQL cluster.",
        relevanceScore: 0.95,
      },
    ],
    sensitivityResults: [],
    assumptions: [
      {
        id: "ass-g2",
        statement:
          "The connection pool is the binding constraint on API throughput (not CPU or memory)",
        strength: "STRONG",
        reviewStatus: "CURRENT",
        lastReviewedAt: new Date(Date.now() - 14 * 24 * 60 * 60 * 1000).toISOString(),
        reviewedBy: "mihail.popescu",
        scope: "database layer",
        linkedEdgeIds: ["edge-db-1"],
      },
    ],
  },
  {
    id: "rev-003",
    objectName: "marketing_spend_increase → customer_churn_reduction",
    objectType: "Intervention",
    businessDomain: "marketing",
    cynefinDomain: "COMPLEX",
    riskTier: "CRITICAL",
    submittedBy: "priya.sharma",
    submittedAt: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    status: "UNDER_REVIEW",
    evidenceCitations: [
      {
        id: "ev-g4",
        type: "STATISTICAL_TEST",
        source: "Causal forest estimate — Q4 cohort",
        snippet:
          "CATE estimate: −0.12 churn probability per $1000 additional marketing spend (95% CI: −0.21, −0.03)",
        relevanceScore: 0.77,
      },
    ],
    sensitivityResults: [
      {
        testName: "Rosenbaum E-value",
        statistic: 1.8,
        pValue: null,
        eValue: 1.8,
        significant: false,
        interpretation:
          "A confounder of only 1.8× strength would explain away the observed effect. High sensitivity — treat with caution.",
      },
    ],
    assumptions: [
      {
        id: "ass-g3",
        statement:
          "Marketing spend is the primary driver of reactivation — not concurrent product improvements",
        strength: "WEAK",
        reviewStatus: "OVERDUE",
        lastReviewedAt: new Date(Date.now() - 120 * 24 * 60 * 60 * 1000).toISOString(),
        reviewedBy: "ryo.tanaka",
        scope: "marketing domain",
        linkedEdgeIds: ["edge-mkt-1"],
      },
      {
        id: "ass-g4",
        statement: "The causal structure is stable across the customer cohort",
        strength: "SPECULATIVE",
        reviewStatus: "DUE_REVIEW",
        lastReviewedAt: new Date(Date.now() - 80 * 24 * 60 * 60 * 1000).toISOString(),
        reviewedBy: null,
        scope: "marketing domain",
        linkedEdgeIds: ["edge-mkt-1"],
      },
    ],
  },
];

const MOCK_AUDIT_ENTRIES: AuditLogRow[] = [
  {
    id: "audit-001",
    timestamp: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    agent: "GovernanceAgent",
    action: "guardian_check — causal edge proposal",
    verdict: "APPROVED",
    policyRulesCited: ["POL-001", "POL-003"],
    payloadHash: "sha256:abc123",
    actorId: "system",
  },
  {
    id: "audit-002",
    timestamp: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    agent: "CausalDiscoveryAgent",
    action: "causal_propose_edge — payment_latency → checkout_conversion",
    verdict: "PENDING",
    policyRulesCited: ["POL-002"],
    payloadHash: "sha256:def456",
    actorId: "ryo.tanaka",
  },
];

// ─── Backend response → frontend type mapper ──────────────────────────────────

const EDGE_NAME_NOTE =
  "Relation type only: this edge's endpoints are not in the review queue.";

/**
 * governance.py ReviewQueueItem → the row the page shows. Only what the backend
 * sends is carried: it sends no risk tier and no submitter, so those are null
 * (rendered as a dash), and a missing created_at stays null rather than
 * becoming "now". `domain` is the object's business domain ("devops",
 * "marketing"), kept as text: it is not a Cynefin classification.
 */
function mapBackendItem(item: ReviewQueueItemResponse): ReviewQueueItem {
  // An edge's `name` is its relation type (causal_edges.edge_type) and the item
  // carries no endpoints: every edge row read "CAUSES". Say what the name is.
  const edgeTypeOnly = item.object_type === "CausalEdge" && !item.name.includes("→");
  return {
    id: item.id,
    objectName: edgeTypeOnly ? `${item.name} edge` : item.name,
    ...(edgeTypeOnly ? { objectNameNote: EDGE_NAME_NOTE } : {}),
    objectType: item.object_type,
    // The backend's `domain` is the business domain (causal_edges.domain, e.g.
    // "devops"), not a Cynefin domain; the endpoint carries no Cynefin
    // classification, so none is invented here.
    businessDomain: item.domain ?? "",
    riskTier: item.risk_tier,
    riskReasons: item.risk_reasons,
    submittedBy: item.submitted_by,
    submittedAt: item.created_at ?? null,
    status: item.approval_status === "DRAFT"
      ? "PENDING"
      : item.approval_status === "UNDER_REVIEW"
      ? "UNDER_REVIEW"
      : (item.approval_status as ReviewQueueItem["status"]),
    evidenceCitations: [],
    sensitivityResults: [],
    assumptions: [],
  };
}

// ─── Decision helpers ──────────────────────────────────────────────────────────

/**
 * Backend object types accepted by /approve and /reject
 * (SemanticLayerRegistry.promote_to_approved / reject_object table_map).
 */
const BACKEND_OBJECT_TYPES = ["CausalEdge", "GraphFragment", "CausalEntity"] as const;
type BackendObjectType = (typeof BACKEND_OBJECT_TYPES)[number];

/**
 * Resolve the backend object_type for a queue item. Items carry the backend
 * value verbatim (see mapBackendItem); returns null for types the governance
 * API cannot approve/reject (the mock set's Intervention).
 */
function toBackendObjectType(objectType: string): BackendObjectType | null {
  if ((BACKEND_OBJECT_TYPES as readonly string[]).includes(objectType)) {
    return objectType as BackendObjectType;
  }
  return null;
}

/** Mirrors RejectRequest.reason min_length in backend/src/api/governance.py. */
const MIN_REJECTION_REASON_LENGTH = 10;

/** An approval and a rejection both carry the reviewer's classification. */
interface DecisionVariables {
  item: ReviewQueueItem;
  reason: string;
  cynefinDomain: CynefinDomain;
}

// ─── Page component ────────────────────────────────────────────────────────────

export default function GovernancePage() {
  const queryClient = useQueryClient();
  const enableMock = isMockFallbackEnabled();
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  // ── React Query: review queue ───────────────────────────────────────────────
  const {
    data: backendItems,
    isLoading: queueLoading,
    isError: queueError,
    error: queueErrorValue,
    refetch: refetchQueue,
  } = useQuery<ReviewQueueItem[]>({
    queryKey: REVIEW_QUEUE_QUERY_KEY,
    queryFn: async () => {
      const raw = await governanceApi.reviewQueue();
      return raw.map(mapBackendItem);
    },
    retry: 1,
    staleTime: 30_000,
  });

  // ── React Query: audit log ──────────────────────────────────────────────────
  const {
    data: backendAuditLog,
    isError: auditError,
    error: auditErrorValue,
  } = useQuery<{ rows: AuditLogRow[]; total: number }>({
    queryKey: AUDIT_LOG_QUERY_KEY,
    queryFn: async () => {
      // The latest 50 rows; `total` is how many the log holds.
      const page = await governanceApi.auditLog({ page_size: 50 });
      return { rows: page.entries.map(toAuditLogRow), total: page.total };
    },
    retry: 1,
    staleTime: 30_000,
  });

  // A 401/403 is the backend answering "no", not an outage: it is said as
  // such, and never papered over with mock data.
  const queueNotAuthorised = queueError && isNotAuthorised(queueErrorValue);
  const auditNotAuthorised = auditError && isNotAuthorised(auditErrorValue);

  // Mock fallback only when explicitly enabled AND the review queue could not
  // be reached. The audit log failing on its own does not switch the page to
  // mock mode: the live queue stays live (and decidable), and the audit panel
  // says it could not be loaded.
  const queueIsMock =
    enableMock && queueError && !backendItems && !queueNotAuthorised;
  const auditIsMock = queueIsMock && auditError && !backendAuditLog;
  const isMockMode = queueIsMock;

  // Fail-closed: no silent mock substitution in production, and no locally
  // patched statuses — the queue is exactly what the backend returned.
  const reviewItems: ReviewQueueItem[] = queueIsMock
    ? MOCK_REVIEW_ITEMS
    : (backendItems ?? []);

  // Audit rows come only from GET /api/governance/audit-log (or the labelled
  // mock set). Nothing is synthesized client-side.
  const auditEntries: AuditLogRow[] = auditIsMock
    ? MOCK_AUDIT_ENTRIES
    : (backendAuditLog?.rows ?? []);
  const auditUnavailable = auditError && !auditIsMock && !backendAuditLog;

  const dataSource = isMockMode
    ? "mock"
    : backendItems
    ? "live"
    : queueNotAuthorised
    ? "unauthorised"
    : "error";

  const selectedItem =
    reviewItems.find((item) => item.id === selectedItemId) ?? null;

  // ── Approve / Reject ────────────────────────────────────────────────────────
  // Both go to the backend; the returned promise resolves only on success.
  // On settle (success OR failure) the queue + audit log are refetched so the
  // UI reflects whatever the backend actually recorded — nothing is patched
  // locally.

  function invalidateGovernanceQueries() {
    void queryClient.invalidateQueries({ queryKey: REVIEW_QUEUE_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: AUDIT_LOG_QUERY_KEY });
  }

  function assertDecidable(item: ReviewQueueItem): BackendObjectType {
    if (isMockMode) {
      throw new Error(MOCK_DECISIONS_DISABLED_REASON);
    }
    const objectType = toBackendObjectType(item.objectType);
    if (!objectType) {
      throw new Error(
        `The governance API cannot approve or reject ${item.objectType} objects`
      );
    }
    return objectType;
  }

  const approveMutation = useMutation({
    mutationFn: async ({ item, reason, cynefinDomain }: DecisionVariables) => {
      const objectType = assertDecidable(item);
      // The backend requires a Guardian APPROVED verdict (audit_log id) to
      // authorise the promotion — obtain it from /check first. The verdict must
      // name the object it authorises (subject_id: /approve refuses one that
      // names nothing), and carry the approver's classification of the
      // situation: without one Guardian treats it as DISORDER and PR-006
      // rejects, which is why this button could never succeed before.
      const check = await governanceApi.check({
        action_type: "GOVERNANCE_APPROVE",
        payload: { object_id: item.id, object_type: objectType, reason },
        subject_id: item.id,
        cynefin_domain: cynefinDomain,
      });
      if (check.verdict !== "APPROVED") {
        throw new Error(`Guardian verdict was ${check.verdict}: ${check.reason}`);
      }
      await governanceApi.approve(
        item.id,
        objectType,
        check.audit_entry_id,
        reason || undefined
      );
    },
    onSettled: invalidateGovernanceQueries,
  });

  const rejectMutation = useMutation({
    mutationFn: async ({ item, reason, cynefinDomain }: DecisionVariables) => {
      const objectType = assertDecidable(item);
      if (reason.trim().length < MIN_REJECTION_REASON_LENGTH) {
        throw new Error(
          `A rejection reason of at least ${MIN_REJECTION_REASON_LENGTH} characters is required`
        );
      }
      // /reject requires an APPROVED Guardian verdict for GOVERNANCE_REJECT on this
      // object (septa-53), obtained exactly as an approval's is: bound to the
      // object and carrying the reviewer's classification. It used to need a
      // REJECTED verdict, which only an unclassified check produced.
      const check = await governanceApi.check({
        action_type: "GOVERNANCE_REJECT",
        payload: { object_id: item.id, object_type: objectType, reason },
        subject_id: item.id,
        cynefin_domain: cynefinDomain,
      });
      if (check.verdict !== "APPROVED") {
        throw new Error(`Guardian verdict was ${check.verdict}: ${check.reason}`);
      }
      await governanceApi.reject(item.id, objectType, reason, check.audit_entry_id);
    },
    onSettled: invalidateGovernanceQueries,
  });

  function findQueueItem(id: string): ReviewQueueItem {
    const item = reviewItems.find((i) => i.id === id);
    if (!item) {
      throw new Error("This item is no longer in the review queue");
    }
    return item;
  }

  async function handleApprove(
    id: string,
    reason: string,
    cynefinDomain: CynefinDomain
  ): Promise<void> {
    await approveMutation.mutateAsync({ item: findQueueItem(id), reason, cynefinDomain });
  }

  async function handleReject(
    id: string,
    reason: string,
    cynefinDomain: CynefinDomain
  ): Promise<void> {
    await rejectMutation.mutateAsync({ item: findQueueItem(id), reason, cynefinDomain });
  }

  const pendingCount = reviewItems.filter(
    (i) => i.status === "PENDING" || i.status === "UNDER_REVIEW"
  ).length;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Top nav */}
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-sm font-bold text-foreground">Causal Platform</h1>
        <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="text-sm text-muted-foreground">Governance</span>
        {pendingCount > 0 && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-700 dark:bg-amber-950 dark:text-amber-300">
            {pendingCount} pending
          </span>
        )}
        {dataSource === "mock" && (
          <span
            data-testid="mock-data-badge"
            title={MOCK_DECISIONS_DISABLED_REASON}
            className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
          >
            mock data — decisions disabled
          </span>
        )}
        {dataSource === "unauthorised" && (
          <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700 dark:bg-red-950 dark:text-red-300 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            not authorised
          </span>
        )}
        {dataSource === "error" && (
          <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700 dark:bg-red-950 dark:text-red-300 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            backend unavailable
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {!queueLoading && (
            <button
              type="button"
              onClick={() => void refetchQueue()}
              aria-label="Refresh review queue"
              className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <RefreshCw className="h-3 w-3" aria-hidden />
              Refresh
            </button>
          )}
          <PersonaSelector layout="dropdown" className="w-48" />
        </div>
      </header>

      <div className="flex flex-1 gap-0 overflow-hidden">
        {/* Review queue + audit log */}
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 lg:p-6">
          <div>
            <h2 className="mb-1 text-lg font-bold text-foreground">
              Review Queue
            </h2>
            <p className="mb-3 text-sm text-muted-foreground">
              All draft graph changes awaiting approval, oldest first.
              Click a row to review.
            </p>

            {/* Degraded state when backend is unreachable and mock is not enabled */}
            {dataSource === "error" && !queueLoading && (
              <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
                <div className="flex items-center gap-2 font-semibold mb-1">
                  <AlertTriangle className="h-4 w-4" aria-hidden />
                  Backend Unavailable
                </div>
                <p className="mb-2">
                  The governance service could not be reached. Review queue and
                  audit log data cannot be loaded.
                </p>
                <p className="text-xs text-red-600 dark:text-red-400">
                  To enable mock data for local development, set{" "}
                  <code className="rounded bg-red-100 px-1 py-0.5 dark:bg-red-900">
                    NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true
                  </code>
                </p>
              </div>
            )}

            {dataSource === "unauthorised" && !queueLoading && (
              <div
                role="alert"
                data-testid="governance-not-authorised"
                className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
              >
                <div className="flex items-center gap-2 font-semibold mb-1">
                  <AlertTriangle className="h-4 w-4" aria-hidden />
                  Not authorised
                </div>
                <p>
                  You are not authorised to view the review queue
                  {queueErrorValue instanceof Error && queueErrorValue.message
                    ? `: ${queueErrorValue.message}`
                    : "."}
                </p>
              </div>
            )}

            {queueLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
                <RefreshCw className="h-4 w-4 animate-spin" aria-hidden />
                Loading review queue…
              </div>
            ) : (
              (dataSource === "live" || dataSource === "mock") && (
                <ReviewQueue
                  items={reviewItems}
                  selectedItemId={selectedItem?.id}
                  onSelectItem={(item) => setSelectedItemId(item.id)}
                />
              )
            )}
          </div>

          {/* Audit log — progressive disclosure Level 3 (backend rows only) */}
          {(dataSource === "live" || dataSource === "mock") &&
            (auditUnavailable ? (
              <div
                role="alert"
                data-testid="audit-log-unavailable"
                className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
              >
                <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                {auditNotAuthorised
                  ? "You are not authorised to read the audit log."
                  : "The audit log could not be loaded from the governance backend."}
              </div>
            ) : (
              <AuditLogPanel
                entries={auditEntries}
                total={auditIsMock ? undefined : backendAuditLog?.total}
                defaultOpen={false}
              />
            ))}
        </div>

        {/* Approval panel */}
        <aside className="hidden w-96 shrink-0 overflow-y-auto border-l border-border p-4 lg:block lg:p-6">
          <h2 className="mb-4 text-base font-bold text-foreground">
            Review Detail
          </h2>
          <ApprovalPanel
            item={selectedItem}
            onApprove={handleApprove}
            onReject={handleReject}
            decisionsDisabledReason={
              isMockMode ? MOCK_DECISIONS_DISABLED_REASON : null
            }
          />
        </aside>
      </div>
    </div>
  );
}

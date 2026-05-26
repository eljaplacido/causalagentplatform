"use client";

/**
 * Governance Review Page — GOVERNANCE persona (US-27, US-28).
 *
 * Shows:
 *   - ReviewQueue (all draft objects awaiting approval, ordered by risk tier)
 *   - ApprovalPanel (for the selected item)
 *   - AuditLogPanel (progressive disclosure Level 3)
 *
 * Data: loads from GET /api/governance/review-queue + GET /api/governance/audit-log
 * via React Query.
 *
 * Failure mode:
 *   - If the backend is unreachable and NEXT_PUBLIC_ENABLE_MOCK_FALLBACK is set,
 *     mock data is shown with an explicit badge.
 *   - Otherwise, a degraded-state message is shown (no silent mock substitution).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, RefreshCw, AlertTriangle } from "lucide-react";
import { AuditLogPanel } from "@/components/shared/AuditLogPanel";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { ReviewQueue } from "@/components/governance/ReviewQueue";
import { ApprovalPanel } from "@/components/governance/ApprovalPanel";
import { governanceApi } from "@/lib/api";
import type { ReviewQueueItem, AuditEntry } from "@/lib/types";

// ─── Mock fallback data (dev-only, gated by env flag) ───────────────────────
// NEVER silently shown in production. Must be explicitly opted-in via:
//   NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true

const ENABLE_MOCK =
  process.env["NEXT_PUBLIC_ENABLE_MOCK_FALLBACK"] === "true";

const MOCK_REVIEW_ITEMS: ReviewQueueItem[] = [
  {
    id: "rev-001",
    objectName: "payment_latency → checkout_conversion",
    objectType: "CAUSAL_EDGE",
    domain: "COMPLICATED",
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
        type: "DOCUMENT_CITATION",
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
    objectType: "CAUSAL_EDGE",
    domain: "CLEAR",
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
    objectType: "INTERVENTION",
    domain: "COMPLEX",
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

const MOCK_AUDIT_ENTRIES: AuditEntry[] = [
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

interface BackendQueueItem {
  id: string;
  object_type: string;
  domain: string;
  name: string;
  approval_status: string;
  created_at: string | null;
}

function mapBackendItem(item: BackendQueueItem): ReviewQueueItem {
  return {
    id: item.id,
    objectName: item.name,
    objectType: item.object_type as ReviewQueueItem["objectType"],
    domain: (item.domain?.toUpperCase() ?? "DISORDER") as ReviewQueueItem["domain"],
    riskTier: "MEDIUM",
    submittedBy: "system",
    submittedAt: item.created_at ?? new Date().toISOString(),
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

interface BackendAuditEntry {
  id: string;
  agent_id: string;
  action_type: string;
  payload_hash: string;
  guardian_verdict: string;
  policy_rules_cited: string[];
  session_id: string | null;
  created_at: string;
}

function mapBackendAuditEntry(entry: BackendAuditEntry): AuditEntry {
  return {
    id: entry.id,
    timestamp: entry.created_at,
    agent: entry.agent_id,
    action: entry.action_type,
    verdict: entry.guardian_verdict as AuditEntry["verdict"],
    policyRulesCited: entry.policy_rules_cited ?? [],
    payloadHash: entry.payload_hash,
    actorId: entry.session_id ?? "system",
  };
}

// ─── Page component ────────────────────────────────────────────────────────────

export default function GovernancePage() {
  const [selectedItem, setSelectedItem] = useState<ReviewQueueItem | null>(null);
  const [localOverrides, setLocalOverrides] = useState<
    Record<string, ReviewQueueItem["status"]>
  >({});
  const [localAuditEntries, setLocalAuditEntries] = useState<AuditEntry[]>([]);

  // ── React Query: review queue ───────────────────────────────────────────────
  const {
    data: backendItems,
    isLoading: queueLoading,
    isError: queueError,
    refetch: refetchQueue,
  } = useQuery<ReviewQueueItem[]>({
    queryKey: ["governance", "review-queue"],
    queryFn: async () => {
      const raw = await governanceApi.reviewQueue() as BackendQueueItem[];
      return raw.map(mapBackendItem);
    },
    retry: 1,
    staleTime: 30_000,
  });

  // ── React Query: audit log ──────────────────────────────────────────────────
  const { data: backendAuditLog, isError: auditError } = useQuery<AuditEntry[]>({
    queryKey: ["governance", "audit-log"],
    queryFn: async () => {
      const raw = await governanceApi.auditLog({ limit: 50 }) as { entries: BackendAuditEntry[] };
      return raw.entries.map(mapBackendAuditEntry);
    },
    retry: 1,
    staleTime: 30_000,
  });

  // Determine data source and whether we should use mock fallback
  const hasBackendError = queueError || auditError;
  const useMock = ENABLE_MOCK && hasBackendError;

  // Fail-closed: no silent mock substitution in production
  const reviewItems: ReviewQueueItem[] = (
    useMock && !backendItems ? MOCK_REVIEW_ITEMS : (backendItems ?? [])
  ).map((item) =>
    localOverrides[item.id]
      ? { ...item, status: localOverrides[item.id]! }
      : item
  );

  const auditEntries: AuditEntry[] = [
    ...localAuditEntries,
    ...(useMock && !backendAuditLog ? MOCK_AUDIT_ENTRIES : (backendAuditLog ?? [])),
  ];

  const dataSource = useMock ? "mock" : backendItems ? "live" : "error";

  // ── Approve / Reject ────────────────────────────────────────────────────────

  async function handleApprove(id: string, reason: string) {
    setLocalOverrides((prev) => ({ ...prev, [id]: "APPROVED" }));

    const item = reviewItems.find((i) => i.id === id);
    if (item) {
      setLocalAuditEntries((prev) => [
        {
          id: `local-${Date.now()}`,
          timestamp: new Date().toISOString(),
          agent: "GovernanceAgent",
          action: `approve — ${item.objectName} (reason: "${reason}")`,
          verdict: "APPROVED",
          policyRulesCited: ["POL-001"],
          payloadHash: `sha256:${Math.random().toString(36).slice(2)}`,
          actorId: "dev-user-001",
        },
        ...prev,
      ]);
    }
  }

  async function handleReject(id: string, reason: string) {
    setLocalOverrides((prev) => ({ ...prev, [id]: "REJECTED" }));

    const item = reviewItems.find((i) => i.id === id);
    if (item) {
      setLocalAuditEntries((prev) => [
        {
          id: `local-${Date.now()}`,
          timestamp: new Date().toISOString(),
          agent: "GovernanceAgent",
          action: `reject — ${item.objectName} (reason: "${reason}")`,
          verdict: "REJECTED",
          policyRulesCited: ["POL-002"],
          payloadHash: `sha256:${Math.random().toString(36).slice(2)}`,
          actorId: "dev-user-001",
        },
        ...prev,
      ]);
    }
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
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            mock data
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
              All draft graph changes awaiting approval, ordered by risk tier.
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

            {queueLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
                <RefreshCw className="h-4 w-4 animate-spin" aria-hidden />
                Loading review queue…
              </div>
            ) : (
              dataSource !== "error" && (
                <ReviewQueue
                  items={reviewItems}
                  selectedItemId={selectedItem?.id}
                  onSelectItem={setSelectedItem}
                />
              )
            )}
          </div>

          {/* Audit log — progressive disclosure Level 3 */}
          {dataSource !== "error" && <AuditLogPanel entries={auditEntries} defaultOpen={false} />}
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
          />
        </aside>
      </div>
    </div>
  );
}

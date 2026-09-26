"use client";

/**
 * ApprovalPanel — Side panel for reviewing and approving/rejecting a draft object.
 *
 * Shows:
 *   - Evidence citations (data-testid="evidence-citations")
 *   - Sensitivity results (data-testid="sensitivity-result")
 *   - Assumption list
 *   - Causal chain (if EpistemicState available)
 *   - Approve / reject buttons (data-testid="approve-button" / "reject-button")
 *   - Reason textarea + confirm button (data-testid="confirm-approval" / "confirm-rejection")
 *   - Success state (data-testid="approval-success")
 *   - Error state (data-testid="approval-error")
 *
 * Fail-closed contract: the success state is rendered ONLY after the
 * onApprove/onReject promise resolves (the backend confirmed the decision).
 * If it rejects, the error is shown and the item stays as it was. When
 * `decisionsDisabledReason` is set (e.g. mock data), approve/reject are disabled.
 */

import { useState } from "react";
import {
  CheckCircle2,
  XCircle,
  BookOpen,
  FlaskConical,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { cn, formatConfidence } from "@/lib/utils";
import { objectTypeLabel } from "@/components/governance/ReviewQueue";
import { CynefinBadge } from "@/components/shared/CynefinBadge";
import type {
  ReviewQueueItem,
  Evidence,
  StatisticalTestResult,
  Assumption,
  CynefinDomain,
} from "@/lib/types";

/**
 * The situations a reviewer may declare, approving or rejecting. DISORDER is not
 * offered: it means "not classified", and Guardian rule PR-006 refuses to approve
 * anything in it -- a rejection included, since rejecting needs an APPROVED
 * verdict for GOVERNANCE_REJECT (septa-53).
 */
export const APPROVAL_DOMAINS: readonly Exclude<CynefinDomain, "DISORDER">[] = [
  "CLEAR",
  "COMPLICATED",
  "COMPLEX",
  "CHAOTIC",
];

export interface ApprovalPanelProps {
  item: ReviewQueueItem | null;
  /**
   * Must resolve ONLY once the backend has confirmed the approval and reject
   * otherwise. The success state is shown only after this resolves.
   */
  onApprove: (id: string, reason: string, cynefinDomain: CynefinDomain) => Promise<void>;
  /** Same contract as onApprove, for rejections, and it carries the domain too. */
  onReject: (id: string, reason: string, cynefinDomain: CynefinDomain) => Promise<void>;
  /**
   * When set, approve/reject are disabled and this explanation is shown
   * (e.g. the page is displaying mock fallback data).
   */
  decisionsDisabledReason?: string | null;
  className?: string;
}

interface ConfirmedDecision {
  itemId: string;
  objectName: string;
  verdict: "approved" | "rejected";
}

function formatDecisionError(kind: "approve" | "reject", err: unknown): string {
  const raw =
    err instanceof Error ? err.message : typeof err === "string" ? err : "";
  const detail = raw.trim().replace(/[.\s]+$/, "") || "Unknown error";
  return kind === "approve"
    ? `Approval failed: ${detail}. The item was NOT approved and remains in its previous state.`
    : `Rejection failed: ${detail}. The item was NOT rejected and remains in its previous state.`;
}

function EvidenceCitationList({ citations }: { citations: Evidence[] }) {
  return (
    <div data-testid="evidence-citations" className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
        Evidence Citations ({citations.length})
      </h4>
      {citations.length === 0 ? (
        <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          No evidence citations provided.
        </p>
      ) : (
        <ul className="space-y-2">
          {citations.map((cite) => (
            <li
              key={cite.id}
              className="rounded-md border border-border bg-muted/30 p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-semibold text-foreground">
                  {cite.source}
                </span>
                <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                  {cite.type.replace("_", " ")}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{cite.snippet}</p>
              {cite.url && (
                <a
                  href={cite.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 block text-xs text-blue-600 underline dark:text-blue-400"
                >
                  View source →
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SensitivityResultList({
  results,
}: {
  results: StatisticalTestResult[];
}) {
  return (
    <div data-testid="sensitivity-result" className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
        Sensitivity Results ({results.length})
      </h4>
      {results.length === 0 ? (
        <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          No sensitivity tests run yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {results.map((result, i) => (
            <li
              key={i}
              className={cn(
                "rounded-md border p-3",
                result.significant
                  ? "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950"
                  : "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950"
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-foreground">
                  {result.testName}
                </span>
                <span
                  className={cn(
                    "text-xs font-bold",
                    result.significant
                      ? "text-green-700 dark:text-green-400"
                      : "text-amber-700 dark:text-amber-400"
                  )}
                >
                  {result.significant ? "Significant" : "Not significant"}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span>
                  Statistic:{" "}
                  <strong className="text-foreground">
                    {result.statistic.toFixed(3)}
                  </strong>
                </span>
                {result.pValue !== null && (
                  <span>
                    p-value:{" "}
                    <strong className="text-foreground">
                      {result.pValue.toFixed(4)}
                    </strong>
                  </span>
                )}
                {result.eValue !== null && (
                  <span>
                    E-value:{" "}
                    <strong className="text-foreground">
                      {result.eValue.toFixed(3)}
                    </strong>
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {result.interpretation}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AssumptionList({ assumptions }: { assumptions: Assumption[] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? assumptions : assumptions.slice(0, 3);

  if (assumptions.length === 0) return null;

  return (
    <div className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
        Assumptions ({assumptions.length})
      </h4>
      <ul className="space-y-1.5">
        {visible.map((assumption) => (
          <li
            key={assumption.id}
            className="rounded-md border border-border bg-muted/20 p-2.5 text-xs"
          >
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  "rounded px-1 py-0.5 text-xs font-semibold",
                  assumption.strength === "STRONG"
                    ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400"
                    : assumption.strength === "MODERATE"
                    ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400"
                    : "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400"
                )}
              >
                {assumption.strength}
              </span>
              <span
                className={cn(
                  "rounded px-1 py-0.5 text-xs",
                  assumption.reviewStatus === "OVERDUE"
                    ? "bg-red-100 text-red-600 dark:bg-red-950 dark:text-red-400"
                    : assumption.reviewStatus === "DUE_REVIEW"
                    ? "bg-amber-100 text-amber-600 dark:bg-amber-950 dark:text-amber-400"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {assumption.reviewStatus}
              </span>
            </div>
            <p className="mt-1 text-foreground">{assumption.statement}</p>
          </li>
        ))}
      </ul>
      {assumptions.length > 3 && (
        <button
          onClick={() => setExpanded((p) => !p)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {expanded ? (
            <>
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
              Show less
            </>
          ) : (
            <>
              <ChevronRight className="h-3 w-3" aria-hidden="true" />
              Show {assumptions.length - 3} more
            </>
          )}
        </button>
      )}
    </div>
  );
}

export function ApprovalPanel({
  item,
  onApprove,
  onReject,
  decisionsDisabledReason = null,
  className,
}: ApprovalPanelProps) {
  const [mode, setMode] = useState<"idle" | "approve" | "reject">("idle");
  const [reason, setReason] = useState("");
  // The reviewer's classification of the situation. Guardian reads it (PR-003,
  // PR-006) for an approval and a rejection alike; nothing pre-selects it, so a
  // decision always carries a human's judgement rather than a default that
  // happens to pass.
  const [cynefinDomain, setCynefinDomain] = useState<CynefinDomain | "">("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Set ONLY after onApprove/onReject resolved, i.e. the backend confirmed the
  // decision. Keyed by item so a confirmation never bleeds onto another item.
  const [confirmed, setConfirmed] = useState<ConfirmedDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reset the in-progress decision when a different item is selected.
  const itemId = item?.id ?? null;
  const [trackedItemId, setTrackedItemId] = useState<string | null>(itemId);
  if (itemId !== trackedItemId) {
    setTrackedItemId(itemId);
    setMode("idle");
    setReason("");
    setCynefinDomain("");
    setError(null);
    // itemId === null happens when the decided item drops out of the refetched
    // queue — keep the confirmation visible in that case.
    if (itemId !== null && confirmed?.itemId !== itemId) {
      setConfirmed(null);
    }
  }

  const decisionsDisabled = Boolean(decisionsDisabledReason);

  async function submitDecision(kind: "approve" | "reject") {
    if (!item || decisionsDisabled) return;
    const target = item;
    setIsSubmitting(true);
    setError(null);
    try {
      if (!cynefinDomain) return;
      if (kind === "approve") {
        await onApprove(target.id, reason, cynefinDomain);
      } else {
        await onReject(target.id, reason, cynefinDomain);
      }
      setConfirmed({
        itemId: target.id,
        objectName: target.objectName,
        verdict: kind === "approve" ? "approved" : "rejected",
      });
      setMode("idle");
      setReason("");
      setCynefinDomain("");
    } catch (err) {
      // Fail closed: keep the item and the typed reason as they were, show why.
      setError(formatDecisionError(kind, err));
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleReset() {
    setMode("idle");
    setReason("");
    setCynefinDomain("");
    setError(null);
    setConfirmed(null);
  }

  // Success state — only reachable after the backend confirmed the decision.
  if (confirmed && (item === null || item.id === confirmed.itemId)) {
    return (
      <div
        data-testid="approval-success"
        className={cn(
          "flex flex-col items-center gap-4 rounded-xl border p-10 text-center",
          confirmed.verdict === "approved"
            ? "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950"
            : "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950",
          className
        )}
      >
        {confirmed.verdict === "approved" ? (
          <CheckCircle2 className="h-12 w-12 text-green-600 dark:text-green-400" aria-hidden="true" />
        ) : (
          <XCircle className="h-12 w-12 text-red-600 dark:text-red-400" aria-hidden="true" />
        )}
        <div>
          <p className="text-lg font-bold text-foreground">
            {confirmed.verdict === "approved"
              ? "Approved successfully"
              : "Rejected successfully"}
          </p>
          <p className="mt-1 text-sm font-medium text-foreground">
            {confirmed.objectName}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            The decision has been recorded in the immutable audit trail.
          </p>
        </div>
        <button
          onClick={handleReset}
          className="rounded-md border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-muted transition-colors"
        >
          Review another item
        </button>
      </div>
    );
  }

  if (!item) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-xl border border-dashed border-border p-12",
          className
        )}
      >
        <p className="text-sm text-muted-foreground">
          Select a review queue item to begin review.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="approval-panel"
      className={cn(
        "rounded-xl border border-border bg-card overflow-hidden",
        className
      )}
    >
      {/* Panel header */}
      <div className="border-b border-border bg-muted/30 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-base font-bold text-foreground">{item.objectName}</h3>
            <p className="text-xs text-muted-foreground">
              {objectTypeLabel(item.objectType)} ·{" "}
              <span data-testid="business-domain">{item.businessDomain || "—"}</span>{" "}
              · Submitted by {item.submittedBy ?? "not recorded"}
            </p>
          </div>
          {item.cynefinDomain && (
            <CynefinBadge
              domain={item.cynefinDomain}
              confidence={0}
              size="sm"
            />
          )}
        </div>
      </div>

      {/* Panel body */}
      <div className="space-y-5 p-5">
        {/* Evidence citations */}
        <EvidenceCitationList citations={item.evidenceCitations} />

        {/* Sensitivity results */}
        <SensitivityResultList results={item.sensitivityResults} />

        {/* Assumptions */}
        <AssumptionList assumptions={item.assumptions} />

        {/* Causal chain from EpistemicState */}
        {item.epistemicState && item.epistemicState.proposedEdges.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Proposed Causal Edges ({item.epistemicState.proposedEdges.length})
            </h4>
            <ul className="space-y-1">
              {item.epistemicState.proposedEdges.map((edge) => (
                <li
                  key={edge.id}
                  className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs"
                >
                  <span className="font-medium text-foreground">
                    {edge.sourceVariable}
                  </span>
                  <span className="text-muted-foreground">→</span>
                  <span className="font-medium text-foreground">
                    {edge.targetVariable}
                  </span>
                  <span className="ml-auto font-mono text-muted-foreground">
                    {formatConfidence(edge.confidence)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Reason input */}
        {mode !== "idle" && (
          <div className="space-y-2">
            <label
              htmlFor="approval-reason"
              className="text-xs font-semibold text-foreground"
            >
              {mode === "approve" ? "Approval reason" : "Rejection reason"}{" "}
              <span className="text-muted-foreground">(recorded in audit trail)</span>
            </label>
            {(mode === "approve" || mode === "reject") && (
              <>
                <label
                  htmlFor="approval-domain"
                  className="block text-xs font-semibold text-foreground"
                >
                  Situation (Cynefin domain){" "}
                  <span className="text-muted-foreground">
                    {mode === "approve"
                      ? "(required: Guardian does not approve in an unclassified situation)"
                      : "(required: Guardian does not authorise a rejection in an unclassified situation)"}
                  </span>
                </label>
                <select
                  id="approval-domain"
                  data-testid="approval-domain"
                  value={cynefinDomain}
                  onChange={(e) => setCynefinDomain(e.target.value as CynefinDomain | "")}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="">Choose…</option>
                  {APPROVAL_DOMAINS.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </>
            )}
            <textarea
              id="approval-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Provide a clear rationale for your decision..."
              className="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        )}

        {/* Decisions disabled (e.g. mock fallback data) */}
        {decisionsDisabledReason && (
          <p
            data-testid="decisions-disabled-notice"
            className="rounded-md border border-zinc-300 bg-zinc-50 p-3 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            {decisionsDisabledReason}
          </p>
        )}

        {/* Backend failure — the decision was not recorded */}
        {error && (
          <div
            role="alert"
            data-testid="approval-error"
            className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
          >
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}
      </div>

      {/* Action footer */}
      <div className="flex items-center gap-2 border-t border-border p-4">
        {mode === "idle" && (
          <>
            <button
              data-testid="approve-button"
              onClick={() => setMode("approve")}
              disabled={decisionsDisabled}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-md px-4 py-2.5 text-sm font-semibold",
                "bg-green-600 text-white hover:bg-green-700 active:bg-green-800",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500 focus-visible:ring-offset-1",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
              )}
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              Approve
            </button>
            <button
              data-testid="reject-button"
              onClick={() => setMode("reject")}
              disabled={decisionsDisabled}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-md border px-4 py-2.5 text-sm font-semibold",
                "border-red-300 bg-red-50 text-red-700 hover:bg-red-100",
                "dark:border-red-800 dark:bg-red-950 dark:text-red-300 dark:hover:bg-red-900",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-1",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
              )}
            >
              <XCircle className="h-4 w-4" aria-hidden="true" />
              Reject
            </button>
          </>
        )}

        {mode === "approve" && (
          <>
            <button
              data-testid="confirm-approval"
              onClick={() => void submitDecision("approve")}
              disabled={isSubmitting || decisionsDisabled || !cynefinDomain}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-md px-4 py-2.5 text-sm font-semibold",
                "bg-green-600 text-white hover:bg-green-700",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500"
              )}
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Confirming..." : "Confirm Approval"}
            </button>
            <button
              onClick={handleReset}
              disabled={isSubmitting}
              className="rounded-md px-3 py-2.5 text-sm text-muted-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
            >
              Cancel
            </button>
          </>
        )}

        {mode === "reject" && (
          <>
            <button
              data-testid="confirm-rejection"
              onClick={() => void submitDecision("reject")}
              disabled={isSubmitting || decisionsDisabled || !cynefinDomain}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-md border px-4 py-2.5 text-sm font-semibold",
                "border-red-300 bg-red-50 text-red-700 hover:bg-red-100",
                "dark:border-red-800 dark:bg-red-950 dark:text-red-300",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
              )}
            >
              <XCircle className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Confirming..." : "Confirm Rejection"}
            </button>
            <button
              onClick={handleReset}
              disabled={isSubmitting}
              className="rounded-md px-3 py-2.5 text-sm text-muted-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}

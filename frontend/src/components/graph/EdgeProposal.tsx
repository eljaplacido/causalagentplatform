"use client";

/**
 * EdgeProposal — review card for a single proposed causal edge.
 *
 * Displays:
 *   - Source → Target with edge type badge
 *   - Confidence meter (colour-coded)
 *   - Evidence citations list
 *   - Rationale text
 *   - Approve / Reject actions (governance_officer only)
 *   - Guardian verdict badge
 *
 * Guardian verdict takes precedence over action buttons:
 *   - APPROVED  → actions hidden, green badge shown
 *   - REJECTED  → actions hidden, red badge shown
 *   - PENDING   → actions visible
 */

import { ArrowRight, Check, X, Link2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import type { GuardianVerdict, Evidence } from "@/lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EdgeProposalData {
  id: string;
  sourceLabel: string;
  targetLabel: string;
  edgeType: string;
  confidence: number;
  rationale: string;
  evidenceCitations: Evidence[];
  proposedBy: string;
  proposedAt: string;
  guardianVerdict: GuardianVerdict;
  policyRulesCited?: string[];
}

export interface EdgeProposalProps {
  proposal: EdgeProposalData;
  /** Whether the current user has governance_officer role */
  canApprove?: boolean;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
  onViewDetails?: (id: string) => void;
  className?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const EDGE_TYPE_COLOURS: Record<string, string> = {
  CAUSES: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-300",
  CORRELATES: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  CONFOUNDS: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300",
  MEDIATES: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300",
};

function confidenceColour(c: number): string {
  if (c >= 0.7) return "bg-green-500";
  if (c >= 0.4) return "bg-amber-400";
  return "bg-red-400";
}

// ── Component ─────────────────────────────────────────────────────────────────

export function EdgeProposal({
  proposal,
  canApprove = false,
  onApprove,
  onReject,
  onViewDetails,
  className,
}: EdgeProposalProps) {
  const {
    id,
    sourceLabel,
    targetLabel,
    edgeType,
    confidence,
    rationale,
    evidenceCitations,
    proposedBy,
    proposedAt,
    guardianVerdict,
    policyRulesCited,
  } = proposal;

  const isSettled =
    guardianVerdict === "APPROVED" || guardianVerdict === "REJECTED";
  const isLowEvidence = evidenceCitations.length === 0;
  const formattedDate = new Date(proposedAt).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <article
      data-testid="edge-proposal"
      data-verdict={guardianVerdict}
      data-proposal-id={id}
      className={cn(
        "rounded-lg border bg-white dark:bg-zinc-900 p-4 space-y-3",
        guardianVerdict === "APPROVED" &&
          "border-green-300 dark:border-green-800",
        guardianVerdict === "REJECTED" &&
          "border-red-300 dark:border-red-800",
        guardianVerdict === "REQUIRES_ESCALATION" &&
          "border-amber-300 dark:border-amber-800",
        !isSettled &&
          guardianVerdict !== "REQUIRES_ESCALATION" &&
          "border-zinc-200 dark:border-zinc-700",
        className
      )}
    >
      {/* Header: Source → Target */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-sm text-zinc-800 dark:text-zinc-200">
          {sourceLabel}
        </span>
        <ArrowRight className="h-4 w-4 text-zinc-400 shrink-0" />
        <span className="font-semibold text-sm text-zinc-800 dark:text-zinc-200">
          {targetLabel}
        </span>
        <span
          className={cn(
            "ml-auto text-xs font-medium px-2 py-0.5 rounded-full",
            EDGE_TYPE_COLOURS[edgeType] ??
              "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
          )}
        >
          {edgeType}
        </span>
      </div>

      {/* Confidence meter */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            Confidence
          </span>
          <EpistemicConfidenceBadge score={confidence} size="sm" />
        </div>
        <div className="h-1.5 w-full rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all", confidenceColour(confidence))}
            style={{ width: `${(confidence * 100).toFixed(0)}%` }}
            role="progressbar"
            aria-valuenow={Math.round(confidence * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Confidence: ${Math.round(confidence * 100)}%`}
          />
        </div>
      </div>

      {/* Low evidence warning */}
      {isLowEvidence && (
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-md bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>No evidence citations. Requires expert review before approval.</span>
        </div>
      )}

      {/* Rationale */}
      <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">
        {rationale}
      </p>

      {/* Evidence citations */}
      {evidenceCitations.length > 0 && (
        <ul className="space-y-1">
          {evidenceCitations.slice(0, 3).map((ev) => (
            <li
              key={ev.id}
              className="flex items-start gap-1.5 text-xs text-zinc-500 dark:text-zinc-400"
            >
              <Link2 className="h-3 w-3 shrink-0 mt-0.5 text-indigo-400" />
              <span>
                <span className="font-medium">{ev.type.replace("_", " ")}</span>
                {" — "}
                {ev.source}: {ev.snippet}
              </span>
            </li>
          ))}
          {evidenceCitations.length > 3 && (
            <li className="text-xs text-zinc-400">
              +{evidenceCitations.length - 3} more citations
            </li>
          )}
        </ul>
      )}

      {/* Footer: verdict + actions */}
      <div className="flex items-center justify-between pt-1 border-t border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2">
          <GuardianVerdictBadge
            verdict={guardianVerdict}
            policyRulesCited={policyRulesCited}
            size="sm"
          />
          <span className="text-xs text-zinc-400">
            by {proposedBy} · {formattedDate}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {onViewDetails && (
            <button
              type="button"
              onClick={() => onViewDetails(id)}
              className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              Details
            </button>
          )}

          {/* Approval actions — shown when PENDING and user has role */}
          {canApprove && !isSettled && (
            <>
              <button
                type="button"
                data-testid="edge-proposal-reject"
                onClick={() => onReject?.(id)}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                aria-label={`Reject edge proposal ${id}`}
              >
                <X className="h-3 w-3" />
                Reject
              </button>
              <button
                type="button"
                data-testid="edge-proposal-approve"
                onClick={() => onApprove?.(id)}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium bg-green-600 text-white hover:bg-green-700 transition-colors"
                aria-label={`Approve edge proposal ${id}`}
              >
                <Check className="h-3 w-3" />
                Approve
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

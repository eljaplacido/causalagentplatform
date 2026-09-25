"use client";

/**
 * InterventionCard — Card for a proposed intervention.
 *
 * UX contract (US-11, US-12):
 *   ALWAYS shows approve + reject buttons regardless of Guardian verdict state.
 *   Human agency must always be one click away.
 *
 * Progressive disclosure:
 *   Level 0: recommendation + confidence + approve/reject
 *   Level 1: causal chain summary + top 3 assumptions (expand)
 */

import { useState } from "react";
import {
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  DollarSign,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import type { Intervention } from "@/lib/types";

export interface InterventionCardProps {
  intervention: Intervention;
  /**
   * Should resolve only once the backend recorded the decision and throw
   * otherwise. On throw the card keeps the reason input open and shows the error.
   */
  onApprove: (id: string, reason: string) => void | Promise<void>;
  onReject: (id: string, reason: string) => void | Promise<void>;
  /** Disable approve/reject while a submission is in flight */
  isSubmitting?: boolean;
  className?: string;
}

const RISK_TIER_CONFIG = {
  LOW: { label: "Low risk", class: "text-green-600 dark:text-green-400" },
  MEDIUM: { label: "Medium risk", class: "text-amber-600 dark:text-amber-400" },
  HIGH: { label: "High risk", class: "text-red-600 dark:text-red-400" },
  CRITICAL: { label: "Critical risk", class: "text-red-700 dark:text-red-300 font-bold" },
};

function EffectEstimateBar({ intervention }: { intervention: Intervention }) {
  const { estimatedEffect } = intervention;
  const isPositive = estimatedEffect.direction === "POSITIVE";
  const isUncertain = estimatedEffect.direction === "UNCERTAIN";

  return (
    <div className="space-y-1.5" data-testid="effect-estimate-bar">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Estimated Effect
        </span>
        <span
          className={cn(
            "text-sm font-bold",
            isUncertain
              ? "text-zinc-500"
              : isPositive
              ? "text-green-600 dark:text-green-400"
              : "text-red-600 dark:text-red-400"
          )}
        >
          {isPositive ? "+" : isUncertain ? "±" : ""}
          {estimatedEffect.pointEstimate.toFixed(2)} {estimatedEffect.unit}
        </span>
      </div>

      {/* Confidence interval visualization */}
      <div
        className="relative h-2 rounded-full bg-muted"
        title={`95% CI: [${estimatedEffect.lowerBound.toFixed(2)}, ${estimatedEffect.upperBound.toFixed(2)}] ${estimatedEffect.unit}`}
        aria-label={`Effect estimate: ${estimatedEffect.pointEstimate.toFixed(2)} ${estimatedEffect.unit}, 95% CI [${estimatedEffect.lowerBound.toFixed(2)}, ${estimatedEffect.upperBound.toFixed(2)}]`}
      >
        <div
          className={cn(
            "absolute h-full rounded-full",
            isUncertain ? "bg-zinc-400" : isPositive ? "bg-green-500" : "bg-red-500"
          )}
          style={{
            width: `${Math.min(100, Math.abs(estimatedEffect.pointEstimate) * 10)}%`,
          }}
        />
        {/* Point estimate marker */}
        <div
          className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full bg-foreground"
          style={{
            left: `${Math.min(99, Math.abs(estimatedEffect.pointEstimate) * 10)}%`,
          }}
          aria-hidden="true"
        />
      </div>

      <p className="text-xs text-muted-foreground">
        95% CI: [{estimatedEffect.lowerBound.toFixed(2)},{" "}
        {estimatedEffect.upperBound.toFixed(2)}] {estimatedEffect.unit}
      </p>
    </div>
  );
}

export function InterventionCard({
  intervention,
  onApprove,
  onReject,
  isSubmitting = false,
  className,
}: InterventionCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [showReasonInput, setShowReasonInput] = useState<
    "approve" | "reject" | null
  >(null);
  const [reason, setReason] = useState("");
  const [isActionInFlight, setIsActionInFlight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const riskConfig = RISK_TIER_CONFIG[intervention.riskTier];
  const displayedAssumptions = intervention.assumptions.slice(0, 3);

  async function submitDecision(kind: "approve" | "reject") {
    setIsActionInFlight(true);
    setActionError(null);
    try {
      if (kind === "approve") {
        await onApprove(intervention.id, reason);
      } else {
        await onReject(intervention.id, reason);
      }
      setShowReasonInput(null);
      setReason("");
    } catch (err) {
      // Fail closed: the decision was not recorded — keep the reason input
      // open so the user can retry, and say why.
      const detail =
        (err instanceof Error ? err.message : String(err)).trim() ||
        "Unknown error";
      setActionError(
        `${kind === "approve" ? "Approval" : "Rejection"} failed: ${detail}`
      );
    } finally {
      setIsActionInFlight(false);
    }
  }

  async function handleApprove() {
    if (showReasonInput !== "approve") {
      setShowReasonInput("approve");
      setActionError(null);
      return;
    }
    await submitDecision("approve");
  }

  async function handleReject() {
    if (showReasonInput !== "reject") {
      setShowReasonInput("reject");
      setActionError(null);
      return;
    }
    await submitDecision("reject");
  }

  const actionDisabled = isSubmitting || isActionInFlight;

  return (
    <article
      data-testid="intervention-card"
      data-intervention-id={intervention.id}
      className={cn(
        "rounded-xl border border-border bg-card shadow-sm",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3 border-b border-border p-4">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-foreground">
            {intervention.title}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <GuardianVerdictBadge
              verdict={intervention.guardianVerdict}
              size="sm"
            />
            <span className={cn("text-xs", riskConfig.class)}>
              {riskConfig.label}
            </span>
            {intervention.cost && (
              <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
                <DollarSign className="h-3 w-3" aria-hidden="true" />
                {intervention.cost}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="space-y-4 p-4">
        <p className="text-sm text-foreground">{intervention.description}</p>

        {/* Effect estimate */}
        <EffectEstimateBar intervention={intervention} />

        {/* Confidence */}
        <EpistemicConfidenceBadge
          overallConfidence={intervention.confidence}
          showMeter={true}
        />

        {/* Assumptions count */}
        <button
          onClick={() => setExpanded((p) => !p)}
          aria-expanded={expanded}
          className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="flex items-center gap-1.5">
            <AlertCircle className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="text-muted-foreground">
              {intervention.assumptions.length} assumption
              {intervention.assumptions.length !== 1 ? "s" : ""}
            </span>
          </span>
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          )}
        </button>

        {/* Level 1 progressive disclosure — assumptions */}
        {expanded && displayedAssumptions.length > 0 && (
          <ul className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
            {displayedAssumptions.map((assumption) => (
              <li key={assumption.id} className="text-xs text-foreground">
                <span
                  className={cn(
                    "mr-1.5 rounded px-1 py-0.5 text-xs font-semibold",
                    assumption.strength === "STRONG"
                      ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400"
                      : assumption.strength === "MODERATE"
                      ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400"
                      : "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400"
                  )}
                >
                  {assumption.strength}
                </span>
                {assumption.statement}
              </li>
            ))}
            {intervention.assumptions.length > 3 && (
              <li className="text-xs text-muted-foreground">
                +{intervention.assumptions.length - 3} more assumptions
              </li>
            )}
          </ul>
        )}

        {/* Reason input — shown after first click of approve/reject */}
        {showReasonInput && (
          <div className="space-y-2">
            <label
              htmlFor={`reason-${intervention.id}`}
              className="text-xs font-semibold text-foreground"
            >
              {showReasonInput === "approve"
                ? "Approval reason (recorded in audit trail)"
                : "Rejection reason (recorded in audit trail)"}
            </label>
            <textarea
              id={`reason-${intervention.id}`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="Explain your decision..."
              className="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        )}

        {/* Backend failure — the decision was not recorded */}
        {actionError && (
          <p
            role="alert"
            data-testid="intervention-action-error"
            className="rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
          >
            {actionError}
          </p>
        )}
      </div>

      {/* Action footer — ALWAYS visible per UX contract */}
      <div className="flex items-center gap-2 border-t border-border p-4">
        {/* APPROVE button */}
        <button
          data-testid="approve-button"
          onClick={handleApprove}
          disabled={actionDisabled}
          aria-label={`Approve intervention: ${intervention.title}`}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-semibold",
            "bg-green-600 text-white hover:bg-green-700 active:bg-green-800",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500 focus-visible:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
          )}
        >
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          {showReasonInput === "approve" ? "Confirm Approval" : "Approve"}
        </button>

        {/* REJECT button */}
        <button
          data-testid="reject-button"
          onClick={handleReject}
          disabled={actionDisabled}
          aria-label={`Reject intervention: ${intervention.title}`}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-semibold",
            "border border-red-300 bg-red-50 text-red-700 hover:bg-red-100",
            "dark:border-red-800 dark:bg-red-950 dark:text-red-300 dark:hover:bg-red-900",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
          )}
        >
          <XCircle className="h-4 w-4" aria-hidden="true" />
          {showReasonInput === "reject" ? "Confirm Rejection" : "Reject"}
        </button>

        {/* Cancel reason input */}
        {showReasonInput && (
          <button
            onClick={() => {
              setShowReasonInput(null);
              setReason("");
              setActionError(null);
            }}
            className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Cancel
          </button>
        )}
      </div>
    </article>
  );
}

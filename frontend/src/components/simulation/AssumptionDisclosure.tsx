"use client";

/**
 * AssumptionDisclosure — Lists assumptions an estimate rests on in plain language (US-25).
 *
 * Expandable. Each assumption shows statement, strength, review status.
 * Designed for Business Analyst persona — no statistical jargon in this component.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle2, XCircle, Clock } from "lucide-react";
import { cn, formatTimestamp } from "@/lib/utils";
import type { Assumption, AssumptionStrength, AssumptionReviewStatus } from "@/lib/types";

export interface AssumptionDisclosureProps {
  assumptions: Assumption[];
  /** Title for the disclosure section */
  title?: string;
  /** Whether the list starts expanded */
  defaultExpanded?: boolean;
  className?: string;
}

const STRENGTH_CONFIG: Record<
  AssumptionStrength,
  { label: string; icon: typeof CheckCircle2; iconClass: string; badgeClass: string }
> = {
  STRONG: {
    label: "Strong",
    icon: CheckCircle2,
    iconClass: "text-green-600 dark:text-green-400",
    badgeClass: "bg-green-100 text-green-700 border-green-200 dark:bg-green-950 dark:text-green-300 dark:border-green-800",
  },
  MODERATE: {
    label: "Moderate",
    icon: AlertCircle,
    iconClass: "text-amber-600 dark:text-amber-400",
    badgeClass: "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800",
  },
  WEAK: {
    label: "Weak",
    icon: AlertCircle,
    iconClass: "text-red-500 dark:text-red-400",
    badgeClass: "bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-800",
  },
  SPECULATIVE: {
    label: "Speculative",
    icon: XCircle,
    iconClass: "text-red-600 dark:text-red-400",
    badgeClass: "bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-200 dark:border-red-700",
  },
};

const REVIEW_STATUS_CONFIG: Record<
  AssumptionReviewStatus,
  { label: string; class: string }
> = {
  CURRENT: { label: "Current", class: "text-green-600 dark:text-green-400" },
  DUE_REVIEW: { label: "Due for review", class: "text-amber-600 dark:text-amber-400" },
  OVERDUE: { label: "Overdue review", class: "text-red-600 dark:text-red-400" },
  DRAFT: { label: "Draft", class: "text-zinc-500 dark:text-zinc-400" },
};

function AssumptionItem({ assumption }: { assumption: Assumption }) {
  const [expanded, setExpanded] = useState(false);
  const strengthConfig = STRENGTH_CONFIG[assumption.strength];
  const reviewConfig = REVIEW_STATUS_CONFIG[assumption.reviewStatus];
  const Icon = strengthConfig.icon;

  return (
    <li className="rounded-md border border-border bg-card">
      <button
        onClick={() => setExpanded((p) => !p)}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-start gap-3 p-3 text-left",
          "hover:bg-muted/40 transition-colors rounded-md",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        )}
      >
        <Icon
          className={cn("mt-0.5 h-4 w-4 shrink-0", strengthConfig.iconClass)}
          aria-hidden="true"
        />

        <div className="min-w-0 flex-1">
          <p className="text-sm text-foreground">{assumption.statement}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "rounded border px-1.5 py-0.5 text-xs font-semibold",
                strengthConfig.badgeClass
              )}
            >
              {strengthConfig.label}
            </span>
            <span
              className={cn("flex items-center gap-1 text-xs", reviewConfig.class)}
            >
              <Clock className="h-3 w-3" aria-hidden="true" />
              {reviewConfig.label}
            </span>
          </div>
        </div>

        {expanded ? (
          <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-border px-3 pb-3 pt-2">
          <dl className="space-y-1 text-xs text-muted-foreground">
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 font-semibold">Scope:</dt>
              <dd>{assumption.scope}</dd>
            </div>
            {assumption.reviewedBy && (
              <div className="flex gap-2">
                <dt className="w-24 shrink-0 font-semibold">Reviewed by:</dt>
                <dd>{assumption.reviewedBy}</dd>
              </div>
            )}
            {assumption.lastReviewedAt && (
              <div className="flex gap-2">
                <dt className="w-24 shrink-0 font-semibold">Last reviewed:</dt>
                <dd>{formatTimestamp(assumption.lastReviewedAt)}</dd>
              </div>
            )}
            {assumption.linkedEdgeIds.length > 0 && (
              <div className="flex gap-2">
                <dt className="w-24 shrink-0 font-semibold">Used by:</dt>
                <dd>{assumption.linkedEdgeIds.length} causal edge(s)</dd>
              </div>
            )}
          </dl>
        </div>
      )}
    </li>
  );
}

export function AssumptionDisclosure({
  assumptions,
  title = "Assumptions this estimate rests on",
  defaultExpanded = false,
  className,
}: AssumptionDisclosureProps) {
  const [isOpen, setIsOpen] = useState(defaultExpanded);

  const overdueCount = assumptions.filter(
    (a) => a.reviewStatus === "OVERDUE"
  ).length;
  const weakOrSpeculativeCount = assumptions.filter(
    (a) => a.strength === "WEAK" || a.strength === "SPECULATIVE"
  ).length;

  return (
    <div
      data-testid="assumption-disclosure"
      className={cn("rounded-xl border border-border", className)}
    >
      {/* Header */}
      <button
        onClick={() => setIsOpen((p) => !p)}
        aria-expanded={isOpen}
        aria-controls="assumption-list-content"
        className={cn(
          "flex w-full items-center gap-2 px-4 py-3 text-left",
          "hover:bg-muted/50 transition-colors",
          isOpen ? "border-b border-border" : ""
        )}
      >
        <AlertCircle
          className="h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <span className="flex-1 text-sm font-semibold text-foreground">
          {title}
        </span>

        {/* Warning indicators */}
        {overdueCount > 0 && (
          <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-700 dark:bg-red-950 dark:text-red-300">
            {overdueCount} overdue
          </span>
        )}
        {weakOrSpeculativeCount > 0 && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-950 dark:text-amber-300">
            {weakOrSpeculativeCount} weak
          </span>
        )}

        <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
          {assumptions.length}
        </span>

        {isOpen ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {/* Assumption list */}
      {isOpen && (
        <div id="assumption-list-content" className="p-4">
          {assumptions.length === 0 ? (
            <p className="text-center text-sm text-muted-foreground">
              No assumptions disclosed for this estimate.
            </p>
          ) : (
            <ul className="space-y-2">
              {assumptions.map((assumption) => (
                <AssumptionItem key={assumption.id} assumption={assumption} />
              ))}
            </ul>
          )}

          {/* Plain-language summary for non-technical users */}
          {assumptions.length > 0 && (
            <div className="mt-4 rounded-md border border-border bg-muted/30 p-3">
              <p className="text-xs text-muted-foreground">
                <strong className="text-foreground">What this means:</strong>{" "}
                This estimate is built on {assumptions.length} assumption
                {assumptions.length !== 1 ? "s" : ""}
                {weakOrSpeculativeCount > 0
                  ? `, of which ${weakOrSpeculativeCount} ${weakOrSpeculativeCount === 1 ? "is" : "are"} weak or speculative`
                  : ""}
                . If these assumptions do not hold in practice, the actual outcome may differ
                from the estimate.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

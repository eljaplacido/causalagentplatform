"use client";

/**
 * ReviewQueue — Structured review queue for Governance Officer (US-27).
 *
 * Ordered by risk tier (CRITICAL → HIGH → MEDIUM → LOW), keeping the given
 * order within a tier (the backend sends oldest first). The tier and its reasons
 * come from the backend (backend/src/semantic_layer/review_risk.py); an item
 * whose submitter was never recorded says so.
 * Clicking a row calls onSelectItem for the ApprovalPanel.
 */

import { cn, formatTimestamp } from "@/lib/utils";
import { CynefinBadge } from "@/components/shared/CynefinBadge";
import type { ReviewQueueItem, RiskTier } from "@/lib/types";

export interface ReviewQueueProps {
  items: ReviewQueueItem[];
  selectedItemId?: string;
  onSelectItem: (item: ReviewQueueItem) => void;
  className?: string;
}

const RISK_TIER_ORDER: Record<RiskTier, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
};

const RISK_TIER_CONFIG: Record<
  RiskTier,
  { label: string; bg: string; text: string; border: string }
> = {
  CRITICAL: {
    label: "Critical",
    bg: "bg-red-100 dark:bg-red-950",
    text: "text-red-800 dark:text-red-300",
    border: "border-red-300 dark:border-red-800",
  },
  HIGH: {
    label: "High",
    bg: "bg-orange-100 dark:bg-orange-950",
    text: "text-orange-800 dark:text-orange-300",
    border: "border-orange-300 dark:border-orange-800",
  },
  MEDIUM: {
    label: "Medium",
    bg: "bg-amber-100 dark:bg-amber-950",
    text: "text-amber-800 dark:text-amber-300",
    border: "border-amber-300 dark:border-amber-800",
  },
  LOW: {
    label: "Low",
    bg: "bg-green-100 dark:bg-green-950",
    text: "text-green-800 dark:text-green-300",
    border: "border-green-300 dark:border-green-800",
  },
};

const STATUS_CONFIG = {
  PENDING: { label: "Pending", class: "text-zinc-500 dark:text-zinc-400" },
  UNDER_REVIEW: { label: "Under Review", class: "text-blue-600 dark:text-blue-400" },
  APPROVED: { label: "Approved", class: "text-green-600 dark:text-green-400" },
  REJECTED: { label: "Rejected", class: "text-red-600 dark:text-red-400" },
  ESCALATED: { label: "Escalated", class: "text-amber-600 dark:text-amber-400" },
};

/** Keyed by the backend's object_type (registry.py _REVIEWABLE_TABLES). */
const OBJECT_TYPE_LABELS: Partial<Record<string, string>> = {
  CausalEdge: "Causal Edge",
  GraphFragment: "Graph Fragment",
  CausalEntity: "Causal Entity",
  Assumption: "Assumption",
  Intervention: "Intervention",
};

/** A label for an object type; an unknown one is shown as sent. */
export function objectTypeLabel(objectType: string): string {
  return OBJECT_TYPE_LABELS[objectType] ?? objectType;
}

const NONE = "—";

/** Where a row without a tier sorts: after every tier. */
const UNRANKED = 99;

export function ReviewQueue({
  items,
  selectedItemId,
  onSelectItem,
  className,
}: ReviewQueueProps) {
  const sorted = items
    .slice()
    .sort(
      (a, b) =>
        (RISK_TIER_ORDER[a.riskTier] ?? UNRANKED) - (RISK_TIER_ORDER[b.riskTier] ?? UNRANKED)
    );

  if (items.length === 0) {
    return (
      <div
        data-testid="review-queue"
        className={cn(
          "rounded-lg border border-dashed border-border p-10 text-center",
          className
        )}
      >
        <p className="text-sm text-muted-foreground">
          No items awaiting review. All governance checks are current.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="review-queue"
      className={cn("rounded-xl border border-border overflow-hidden", className)}
    >
      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Object
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Type
              </th>
              <th
                scope="col"
                className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground md:table-cell"
              >
                Domain
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Risk
              </th>
              <th
                scope="col"
                className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground sm:table-cell"
              >
                Submitted by
              </th>
              <th
                scope="col"
                className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground lg:table-cell"
              >
                Submitted at
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item, index) => {
              // Typed as always present, but a row from an older backend may carry none.
              const riskConfig = item.riskTier ? RISK_TIER_CONFIG[item.riskTier] : undefined;
              const statusConfig = STATUS_CONFIG[item.status] ?? {
                label: item.status,
                class: "text-muted-foreground",
              };
              const isSelected = item.id === selectedItemId;
              const isFirst = index === 0;

              return (
                <tr
                  key={item.id}
                  data-testid={isFirst ? "first-review-item" : "review-item"}
                  data-item-id={item.id}
                  onClick={() => onSelectItem(item)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectItem(item);
                    }
                  }}
                  aria-current={isSelected ? "true" : undefined}
                  className={cn(
                    "cursor-pointer border-b border-border last:border-0 transition-colors",
                    "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    isSelected ? "bg-primary/5" : ""
                  )}
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    {item.objectName}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {objectTypeLabel(item.objectType)}
                  </td>
                  <td
                    className="hidden px-4 py-3 md:table-cell"
                    data-testid="review-item-domain"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span
                        data-testid="business-domain"
                        className="text-muted-foreground"
                      >
                        {item.businessDomain || NONE}
                      </span>
                      {item.cynefinDomain && (
                        <CynefinBadge
                          domain={item.cynefinDomain}
                          confidence={0}
                          size="sm"
                        />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {riskConfig ? (
                      <span
                        title={item.riskReasons?.join("; ") || undefined}
                        className={cn(
                          "rounded-full border px-2 py-0.5 text-xs font-semibold",
                          riskConfig.bg,
                          riskConfig.text,
                          riskConfig.border
                        )}
                      >
                        {riskConfig.label}
                      </span>
                    ) : (
                      <span className="text-muted-foreground" title="No risk tier was sent">
                        {NONE}
                      </span>
                    )}
                  </td>
                  <td className="hidden px-4 py-3 text-muted-foreground sm:table-cell">
                    {item.submittedBy ?? "not recorded"}
                  </td>
                  <td
                    className="hidden px-4 py-3 text-muted-foreground lg:table-cell"
                    data-testid="review-item-submitted-at"
                  >
                    {item.submittedAt ? formatTimestamp(item.submittedAt) : NONE}
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn("text-xs font-medium", statusConfig.class)}>
                      {statusConfig.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

"use client";

/**
 * FragmentCard — compact card for a GraphFragment in the library browser.
 *
 * Shows:
 *   - Fragment name, domain badge, version
 *   - Approval status (colour-coded)
 *   - Edge count, usage count
 *   - Tags
 *   - Guardian verdict badge
 *   - Action: View, Use in Session, Request Review
 *
 * Used in the GraphFragment browser on the /analyst and /governance pages.
 *
 * UIX.md: Causal Analyst persona needs to browse and apply approved graph
 * fragments without re-discovering known causal structures.
 */

import { GitBranch, BarChart3, Tag, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import type { GuardianVerdict } from "@/lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

export type FragmentApprovalStatus =
  | "DRAFT"
  | "UNDER_REVIEW"
  | "APPROVED"
  | "DEPRECATED";

export interface FragmentCardData {
  id: string;
  name: string;
  domain: string;
  description: string;
  edgeCount: number;
  usageCount: number;
  version: string;
  approvalStatus: FragmentApprovalStatus;
  guardianVerdict: GuardianVerdict;
  policyRulesCited?: string[];
  tags: string[];
  createdAt: string; // ISO8601
  approvedAt?: string; // ISO8601
}

export interface FragmentCardProps {
  fragment: FragmentCardData;
  onView?: (id: string) => void;
  onUseInSession?: (id: string) => void;
  onRequestReview?: (id: string) => void;
  isActive?: boolean;
  className?: string;
}

// ── Status colour map ─────────────────────────────────────────────────────────

const STATUS_COLOURS: Record<
  FragmentApprovalStatus,
  { badge: string; dot: string }
> = {
  DRAFT: {
    badge: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    dot: "bg-zinc-400",
  },
  UNDER_REVIEW: {
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300",
    dot: "bg-amber-400",
  },
  APPROVED: {
    badge: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
    dot: "bg-green-500",
  },
  DEPRECATED: {
    badge: "bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-500",
    dot: "bg-zinc-300",
  },
};

// ── Component ─────────────────────────────────────────────────────────────────

export function FragmentCard({
  fragment,
  onView,
  onUseInSession,
  onRequestReview,
  isActive = false,
  className,
}: FragmentCardProps) {
  const {
    id,
    name,
    domain,
    description,
    edgeCount,
    usageCount,
    version,
    approvalStatus,
    guardianVerdict,
    policyRulesCited,
    tags,
    createdAt,
    approvedAt,
  } = fragment;

  const statusColours = STATUS_COLOURS[approvalStatus];
  const isApproved = approvalStatus === "APPROVED";
  const isDeprecated = approvalStatus === "DEPRECATED";

  const formattedDate = approvedAt
    ? new Date(approvedAt).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : new Date(createdAt).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      });

  return (
    <article
      data-testid="fragment-card"
      data-fragment-id={id}
      data-status={approvalStatus}
      className={cn(
        "group rounded-lg border bg-white dark:bg-zinc-900 p-4 space-y-3 transition-all",
        isActive
          ? "border-indigo-400 dark:border-indigo-600 ring-1 ring-indigo-200 dark:ring-indigo-900"
          : "border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600",
        isDeprecated && "opacity-60",
        className
      )}
      aria-current={isActive ? "true" : undefined}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span
              className={cn(
                "inline-flex items-center h-2 w-2 rounded-full shrink-0",
                statusColours.dot
              )}
              aria-hidden
            />
            <h3
              className={cn(
                "font-semibold text-sm text-zinc-900 dark:text-zinc-100 truncate",
                isDeprecated && "line-through text-zinc-500"
              )}
            >
              {name}
            </h3>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded">
              {domain}
            </span>
            <span className="text-xs text-zinc-400 dark:text-zinc-500">
              v{version}
            </span>
          </div>
        </div>
        <span
          className={cn(
            "shrink-0 text-xs font-medium px-2 py-0.5 rounded-full",
            statusColours.badge
          )}
        >
          {approvalStatus}
        </span>
      </div>

      {/* Description */}
      {description && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400 line-clamp-2 leading-relaxed">
          {description}
        </p>
      )}

      {/* Stats row */}
      <div className="flex items-center gap-3 text-xs text-zinc-500 dark:text-zinc-400">
        <span className="flex items-center gap-1">
          <GitBranch className="h-3.5 w-3.5" aria-hidden />
          <span>{edgeCount} edges</span>
        </span>
        <span className="flex items-center gap-1">
          <BarChart3 className="h-3.5 w-3.5" aria-hidden />
          <span>{usageCount} uses</span>
        </span>
        <span className="ml-auto">{formattedDate}</span>
      </div>

      {/* Tags */}
      {tags.length > 0 && (
        <div
          className="flex items-center gap-1 flex-wrap"
          aria-label="Fragment tags"
        >
          <Tag className="h-3 w-3 text-zinc-400 shrink-0" aria-hidden />
          {tags.slice(0, 4).map((tag) => (
            <span
              key={tag}
              className="text-xs bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 px-1.5 py-0.5 rounded-full"
            >
              {tag}
            </span>
          ))}
          {tags.length > 4 && (
            <span className="text-xs text-zinc-400">+{tags.length - 4}</span>
          )}
        </div>
      )}

      {/* Guardian verdict */}
      <div className="flex items-center justify-between pt-1 border-t border-zinc-100 dark:border-zinc-800">
        <GuardianVerdictBadge
          verdict={guardianVerdict}
          policyRulesCited={policyRulesCited}
          size="sm"
        />

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          {onView && (
            <button
              type="button"
              onClick={() => onView(id)}
              data-testid="fragment-card-view"
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:underline"
              aria-label={`View fragment ${name}`}
            >
              View
            </button>
          )}

          {!isApproved && !isDeprecated && onRequestReview && (
            <button
              type="button"
              onClick={() => onRequestReview(id)}
              data-testid="fragment-card-request-review"
              className="text-xs text-amber-600 dark:text-amber-400 hover:underline"
              aria-label={`Request review for fragment ${name}`}
            >
              Request review
            </button>
          )}

          {isApproved && onUseInSession && (
            <button
              type="button"
              onClick={() => onUseInSession(id)}
              data-testid="fragment-card-use"
              className="inline-flex items-center gap-1 text-xs font-medium text-indigo-700 dark:text-indigo-300 hover:underline"
              aria-label={`Use fragment ${name} in session`}
            >
              Use
              <ArrowUpRight className="h-3 w-3" aria-hidden />
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

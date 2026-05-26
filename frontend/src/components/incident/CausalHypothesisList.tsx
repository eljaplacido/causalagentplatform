"use client";

/**
 * CausalHypothesisList — Ranked list of causal hypotheses.
 *
 * Progressive disclosure: collapsed by default (Level 0), expands to show
 * the causal chain (Level 1) on click.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, ArrowRight } from "lucide-react";
import { cn, formatConfidence } from "@/lib/utils";
import type { CausalHypothesis, CausalChainLink } from "@/lib/types";

export interface CausalHypothesisListProps {
  hypotheses: CausalHypothesis[];
  className?: string;
}

function CausalChainDisplay({ chain }: { chain: CausalChainLink[] }) {
  return (
    <div
      className="flex flex-wrap items-center gap-1 pt-2"
      aria-label="Causal chain"
    >
      {chain.map((link, i) => (
        <span key={`${link.variable}-${i}`} className="flex items-center gap-1">
          <span
            className={cn(
              "rounded-md px-2 py-0.5 text-xs font-medium",
              link.role === "CAUSE"
                ? "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                : link.role === "MEDIATOR"
                ? "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300"
                : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
            )}
            title={`${link.role} — confidence: ${formatConfidence(link.confidence)}`}
          >
            {link.variable}
          </span>
          {i < chain.length - 1 && (
            <ArrowRight className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
          )}
        </span>
      ))}
    </div>
  );
}

function HypothesisItem({ hypothesis }: { hypothesis: CausalHypothesis }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <li
      data-testid="hypothesis-item"
      className="border-b border-border last:border-0"
    >
      <button
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-start gap-3 px-4 py-3 text-left",
          "hover:bg-muted/40 transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        )}
      >
        {/* Rank badge */}
        <span
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-bold text-muted-foreground"
          aria-label={`Rank ${hypothesis.rank}`}
        >
          {hypothesis.rank}
        </span>

        {/* Description */}
        <span className="flex-1 text-sm text-foreground">
          {hypothesis.description}
        </span>

        {/* Confidence score */}
        <span
          data-testid="confidence-score"
          className={cn(
            "shrink-0 font-mono text-xs font-bold",
            hypothesis.confidence > 0.7
              ? "text-green-600 dark:text-green-400"
              : hypothesis.confidence >= 0.4
              ? "text-amber-600 dark:text-amber-400"
              : "text-red-600 dark:text-red-400"
          )}
          aria-label={`Confidence: ${formatConfidence(hypothesis.confidence)}`}
        >
          {formatConfidence(hypothesis.confidence)}
        </span>

        {/* Expand icon */}
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {/* Causal chain — Level 1 progressive disclosure */}
      {expanded && (
        <div className="px-4 pb-4 pt-1">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Causal Chain
          </p>
          <CausalChainDisplay chain={hypothesis.causalChain} />

          {hypothesis.evidenceCitations.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Evidence ({hypothesis.evidenceCitations.length})
              </p>
              <ul className="space-y-1">
                {hypothesis.evidenceCitations.map((cite) => (
                  <li key={cite.id} className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{cite.source}:</span>{" "}
                    {cite.snippet}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function CausalHypothesisList({
  hypotheses,
  className,
}: CausalHypothesisListProps) {
  if (hypotheses.length === 0) {
    return (
      <div
        data-testid="causal-hypothesis-list"
        className={cn(
          "rounded-lg border border-border p-6 text-center text-sm text-muted-foreground",
          className
        )}
      >
        No causal hypotheses generated yet.
      </div>
    );
  }

  return (
    <ul
      data-testid="causal-hypothesis-list"
      aria-label="Causal hypotheses, ranked by confidence"
      className={cn("rounded-lg border border-border divide-y divide-border", className)}
    >
      {hypotheses
        .slice()
        .sort((a, b) => a.rank - b.rank)
        .map((hypothesis) => (
          <HypothesisItem key={hypothesis.id} hypothesis={hypothesis} />
        ))}
    </ul>
  );
}

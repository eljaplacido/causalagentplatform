"use client";

/**
 * InterventionComparison — Side-by-side comparison of two interventions (US-24).
 *
 * Shows estimated effects, cost, confidence intervals for each.
 * Designed for Business Analyst persona (Priya) — clarity over precision.
 */

import { cn } from "@/lib/utils";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import type { Intervention } from "@/lib/types";

export interface InterventionComparisonProps {
  interventionA: Intervention;
  interventionB: Intervention;
  className?: string;
}

function EffectEstimateBar({
  intervention,
  label,
}: {
  intervention: Intervention;
  label: "A" | "B";
}) {
  const { estimatedEffect } = intervention;
  const isPositive = estimatedEffect.direction === "POSITIVE";
  const isUncertain = estimatedEffect.direction === "UNCERTAIN";
  const rangeWidth =
    Math.abs(estimatedEffect.upperBound - estimatedEffect.lowerBound);

  return (
    <div className="space-y-2" data-testid={`effect-estimate-${label.toLowerCase()}`}>
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Estimated Effect
        </span>
        <span
          className={cn(
            "text-lg font-bold",
            isUncertain
              ? "text-zinc-500"
              : isPositive
              ? "text-green-600 dark:text-green-400"
              : "text-red-600 dark:text-red-400"
          )}
        >
          {isPositive ? "+" : isUncertain ? "±" : ""}
          {estimatedEffect.pointEstimate.toFixed(2)}{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {estimatedEffect.unit}
          </span>
        </span>
      </div>

      {/* Visual bar */}
      <div
        className="relative h-3 rounded-full bg-muted"
        role="img"
        aria-label={`Effect: ${estimatedEffect.pointEstimate.toFixed(2)} ${estimatedEffect.unit}, CI [${estimatedEffect.lowerBound.toFixed(2)}, ${estimatedEffect.upperBound.toFixed(2)}]`}
      >
        <div
          className={cn(
            "absolute h-full rounded-full opacity-90",
            isUncertain ? "bg-zinc-400" : isPositive ? "bg-green-500" : "bg-red-500"
          )}
          style={{
            width: `${Math.min(95, Math.abs(estimatedEffect.pointEstimate) * 8)}%`,
          }}
        />
      </div>

      {/* CI text */}
      <p className="text-xs text-muted-foreground">
        95% CI: [{estimatedEffect.lowerBound.toFixed(2)},{" "}
        {estimatedEffect.upperBound.toFixed(2)}] {estimatedEffect.unit}
        {rangeWidth > 0 && (
          <span className="ml-1 text-muted-foreground/70">
            (range: {rangeWidth.toFixed(2)})
          </span>
        )}
      </p>
    </div>
  );
}

function ComparisonColumn({
  intervention,
  label,
  isWinner,
}: {
  intervention: Intervention;
  label: "A" | "B";
  isWinner: boolean;
}) {
  return (
    <div
      data-testid={`intervention-column-${label.toLowerCase()}`}
      className={cn(
        "flex flex-col gap-4 rounded-xl border p-5",
        isWinner
          ? "border-green-300 bg-green-50/50 dark:border-green-700 dark:bg-green-950/30"
          : "border-border bg-card"
      )}
    >
      {/* Label + winner badge */}
      <div className="flex items-center justify-between gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-muted text-sm font-bold text-foreground">
          {label}
        </span>
        {isWinner && (
          <span className="rounded-full border border-green-300 bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-700 dark:border-green-700 dark:bg-green-950 dark:text-green-400">
            Higher confidence
          </span>
        )}
      </div>

      {/* Title + description */}
      <div>
        <h3 className="text-base font-bold text-foreground">{intervention.title}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {intervention.description}
        </p>
      </div>

      {/* Effect estimate */}
      <EffectEstimateBar intervention={intervention} label={label} />

      {/* Confidence */}
      <EpistemicConfidenceBadge
        overallConfidence={intervention.confidence}
        showMeter={true}
      />

      {/* Cost */}
      {intervention.cost && (
        <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
          <span className="text-xs font-semibold text-muted-foreground">
            Estimated Cost
          </span>
          <span className="text-sm font-bold text-foreground">
            {intervention.cost}
          </span>
        </div>
      )}

      {/* Risk tier */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Risk tier</span>
        <span
          className={cn(
            "text-xs font-semibold",
            intervention.riskTier === "CRITICAL"
              ? "text-red-700 dark:text-red-300"
              : intervention.riskTier === "HIGH"
              ? "text-red-600 dark:text-red-400"
              : intervention.riskTier === "MEDIUM"
              ? "text-amber-600 dark:text-amber-400"
              : "text-green-600 dark:text-green-400"
          )}
        >
          {intervention.riskTier}
        </span>
      </div>

      {/* Assumptions count */}
      <p className="text-xs text-muted-foreground">
        {intervention.assumptions.length} assumption
        {intervention.assumptions.length !== 1 ? "s" : ""} disclosed
      </p>
    </div>
  );
}

export function InterventionComparison({
  interventionA,
  interventionB,
  className,
}: InterventionComparisonProps) {
  // Determine which has higher confidence for the "winner" indicator
  const aIsWinner = interventionA.confidence > interventionB.confidence;
  const bIsWinner = interventionB.confidence > interventionA.confidence;

  const delta = Math.abs(
    interventionA.estimatedEffect.pointEstimate -
      interventionB.estimatedEffect.pointEstimate
  );

  return (
    <div
      data-testid="intervention-comparison"
      className={cn("space-y-4", className)}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-foreground">
          Intervention Comparison
        </h2>
        <span className="text-sm text-muted-foreground">
          Effect difference: {delta.toFixed(2)}{" "}
          {interventionA.estimatedEffect.unit}
        </span>
      </div>

      {/* Side-by-side columns */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ComparisonColumn
          intervention={interventionA}
          label="A"
          isWinner={aIsWinner}
        />
        <ComparisonColumn
          intervention={interventionB}
          label="B"
          isWinner={bIsWinner}
        />
      </div>

      {/* Tie state */}
      {!aIsWinner && !bIsWinner && (
        <p className="rounded-md border border-border bg-muted/30 px-4 py-2 text-center text-sm text-muted-foreground">
          Both interventions have equal confidence. Review assumptions to
          differentiate.
        </p>
      )}
    </div>
  );
}

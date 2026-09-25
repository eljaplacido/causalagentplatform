"use client";

/**
 * EpistemicConfidenceBadge — Displays overall EpistemicState confidence.
 *
 * HIGH (>0.7)     → green
 * MEDIUM (0.4–0.7)→ amber
 * LOW (<0.4)      → red
 *
 * Also shows hallucinationRiskScore with a warning icon when > 0.5.
 * Never conveys information by color alone — uses icons and text.
 */

import { AlertTriangle, CheckCircle2, AlertCircle, XCircle } from "lucide-react";
import { cn, formatConfidence, clamp } from "@/lib/utils";
import { toConfidenceLevel, type ConfidenceLevel } from "@/lib/types";

export interface EpistemicConfidenceBadgeProps {
  /** Overall confidence score 0.0 – 1.0 */
  overallConfidence?: number;
  /** Alias for overallConfidence (convenience prop) */
  score?: number;
  /** Grounding-risk score 0.0 – 1.0 (Guardian PR-002; computed by the platform) */
  hallucinationRiskScore?: number;
  /** Show the confidence meter bar */
  showMeter?: boolean;
  /** Badge size variant */
  size?: "sm" | "md" | "lg";
  className?: string;
}

const LEVEL_CONFIG: Record<
  ConfidenceLevel,
  {
    label: string;
    icon: typeof CheckCircle2;
    bg: string;
    text: string;
    border: string;
    meter: string;
  }
> = {
  HIGH: {
    label: "High Confidence",
    icon: CheckCircle2,
    bg: "bg-green-50 dark:bg-green-950",
    text: "text-green-800 dark:text-green-300",
    border: "border-green-200 dark:border-green-800",
    meter: "bg-green-500",
  },
  MEDIUM: {
    label: "Medium Confidence",
    icon: AlertCircle,
    bg: "bg-amber-50 dark:bg-amber-950",
    text: "text-amber-800 dark:text-amber-300",
    border: "border-amber-200 dark:border-amber-800",
    meter: "bg-amber-400",
  },
  LOW: {
    label: "Low Confidence",
    icon: XCircle,
    bg: "bg-red-50 dark:bg-red-950",
    text: "text-red-800 dark:text-red-300",
    border: "border-red-200 dark:border-red-800",
    meter: "bg-red-500",
  },
};

const SIZE_CLASSES = {
  sm: "flex-row gap-1.5 rounded-md border px-2 py-1",
  md: "flex-col gap-2 rounded-lg border p-3",
  lg: "flex-col gap-3 rounded-xl border p-4",
};

export function EpistemicConfidenceBadge({
  overallConfidence,
  score,
  hallucinationRiskScore = 0,
  showMeter = true,
  size = "md",
  className,
}: EpistemicConfidenceBadgeProps) {
  const confidenceValue = overallConfidence ?? score ?? 0;
  const clamped = clamp(confidenceValue, 0, 1);
  const level = toConfidenceLevel(clamped);
  const config = LEVEL_CONFIG[level];
  const Icon = config.icon;
  const showHallucinationWarning = hallucinationRiskScore > 0.5;
  const isSmall = size === "sm";

  return (
    <div
      data-testid="epistemic-confidence-badge"
      className={cn(
        "flex items-center border",
        isSmall ? SIZE_CLASSES.sm : SIZE_CLASSES[size],
        config.bg,
        config.border,
        className
      )}
    >
      {/* Main badge row */}
      <div className={cn("flex items-center gap-2", isSmall && "w-full")}>
        <Icon
          className={cn("shrink-0", isSmall ? "h-3 w-3" : "h-4 w-4", config.text)}
          aria-hidden="true"
        />
        {!isSmall && (
          <span
            className={cn("text-sm font-semibold", config.text)}
            data-testid="confidence-level-label"
          >
            {config.label}
          </span>
        )}
        <span
          className={cn(
            "font-mono font-bold",
            isSmall ? "ml-auto text-xs" : "ml-auto text-sm",
            config.text
          )}
          data-testid="confidence-score-value"
        >
          {formatConfidence(clamped)}
        </span>
      </div>

      {/* Confidence meter bar — hidden in sm mode */}
      {showMeter && !isSmall && (
        <div
          role="progressbar"
          aria-valuenow={Math.round(clamped * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Confidence: ${formatConfidence(clamped)}`}
          className="h-1.5 w-full rounded-full bg-black/10 dark:bg-white/10"
        >
          <div
            className={cn(
              "h-full rounded-full transition-all duration-300",
              config.meter
            )}
            style={{ width: `${Math.round(clamped * 100)}%` }}
          />
        </div>
      )}

      {/* Hallucination risk warning — hidden in sm mode */}
      {showHallucinationWarning && !isSmall && (
        <div
          data-testid="hallucination-risk-warning"
          className="flex items-center gap-1.5 rounded-md bg-amber-100/80 px-2 py-1 dark:bg-amber-900/40"
        >
          <AlertTriangle
            className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
            aria-hidden="true"
          />
          <span className="text-xs text-amber-700 dark:text-amber-300">
            Hallucination risk:{" "}
            <strong>{formatConfidence(hallucinationRiskScore)}</strong> — review
            evidence citations before approving
          </span>
        </div>
      )}
    </div>
  );
}

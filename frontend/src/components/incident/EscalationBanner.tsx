"use client";

/**
 * EscalationBanner — Full-width red banner shown when Cynefin = CHAOTIC.
 *
 * UX contract (US-14, AGENTS.md §5.1):
 *   CHAOTIC domain → route immediately to human escalation.
 *   Never show AI recommendations alongside this banner.
 */

import { AlertTriangle, Phone } from "lucide-react";
import { cn } from "@/lib/utils";

export interface EscalationBannerProps {
  /** Incident or session ID to include in escalation context */
  incidentId?: string;
  onEscalate?: (incidentId: string | undefined) => void;
  className?: string;
}

export function EscalationBanner({
  incidentId,
  onEscalate,
  className,
}: EscalationBannerProps) {
  function handleEscalate() {
    onEscalate?.(incidentId);
    // In production this opens the escalation pager / PagerDuty integration
  }

  return (
    <div
      data-testid="escalation-banner"
      role="alert"
      aria-live="assertive"
      className={cn(
        "flex w-full flex-col gap-3 rounded-lg border-2 border-red-500 bg-red-50 p-4 dark:bg-red-950",
        "sm:flex-row sm:items-center sm:gap-4",
        className
      )}
    >
      {/* Icon + text */}
      <div className="flex items-start gap-3 sm:flex-1">
        <AlertTriangle
          className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400"
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-bold text-red-800 dark:text-red-200">
            Immediate human escalation required
          </p>
          <p className="mt-0.5 text-sm text-red-700 dark:text-red-300">
            This incident is classified as{" "}
            <strong>Chaotic domain</strong> — AI recommendations are{" "}
            <strong>not reliable</strong> in this context. A human Incident
            Commander must take direct control immediately.
          </p>
          {incidentId && (
            <p className="mt-1 font-mono text-xs text-red-600 dark:text-red-400">
              Incident: {incidentId}
            </p>
          )}
        </div>
      </div>

      {/* Escalation CTA */}
      <button
        onClick={handleEscalate}
        data-testid="escalate-button"
        className={cn(
          "flex shrink-0 items-center gap-2 rounded-md px-4 py-2.5 text-sm font-semibold",
          "bg-red-600 text-white hover:bg-red-700 active:bg-red-800",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2",
          "transition-colors"
        )}
      >
        <Phone className="h-4 w-4" aria-hidden="true" />
        Escalate to Incident Commander
      </button>
    </div>
  );
}

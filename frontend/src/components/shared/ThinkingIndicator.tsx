"use client";

/**
 * ThinkingIndicator — Critical UX Contract Component
 *
 * MUST appear within 150ms of any agent action submission.
 * Rendered optimistically before the API response arrives.
 *
 * Progressive disclosure: shows domain badge as soon as Cynefin domain
 * is classified (passed via cynefinDomain prop).
 */

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn, formatElapsed } from "@/lib/utils";
import { CynefinBadge } from "./CynefinBadge";
import type { CynefinDomain } from "@/lib/types";

export interface ThinkingIndicatorProps {
  /** Current reasoning stage description */
  stage: string;
  /** Elapsed milliseconds since agent action started (defaults to 0) */
  elapsed?: number;
  /** Cynefin domain — shown as badge once classified */
  cynefinDomain?: CynefinDomain;
  /** Confidence of the domain classification (0–1) */
  cynefinConfidence?: number;
  /** Additional CSS classes */
  className?: string;
}

export function ThinkingIndicator({
  stage,
  elapsed = 0,
  cynefinDomain,
  cynefinConfidence,
  className,
}: ThinkingIndicatorProps) {
  // Tick elapsed time internally if consumer doesn't update it continuously
  const [localElapsed, setLocalElapsed] = useState(elapsed);

  useEffect(() => {
    setLocalElapsed(elapsed);
  }, [elapsed]);

  useEffect(() => {
    const interval = setInterval(() => {
      setLocalElapsed((prev) => prev + 100);
    }, 100);
    return () => clearInterval(interval);
  }, []);

  return (
    <div
      data-testid="thinking-indicator"
      role="status"
      aria-live="polite"
      aria-label={`AI thinking: ${stage}`}
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border bg-muted/50 px-4 py-3",
        "animate-fade-in",
        className
      )}
    >
      {/* Animated spinner */}
      <Loader2
        className="h-5 w-5 shrink-0 animate-thinking-spin text-primary"
        aria-hidden="true"
      />

      {/* Stage text + elapsed */}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm font-medium text-foreground">{stage}</span>
        <span className="text-xs text-muted-foreground">
          {formatElapsed(localElapsed)}
        </span>
      </div>

      {/* Cynefin domain badge — appears as soon as domain is classified */}
      {cynefinDomain && (
        <div className="shrink-0" data-testid="thinking-cynefin-badge">
          <CynefinBadge
            domain={cynefinDomain}
            confidence={cynefinConfidence ?? 0}
            size="sm"
          />
        </div>
      )}
    </div>
  );
}

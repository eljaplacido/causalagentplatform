"use client";

/**
 * CynefinBadge — Cynefin domain classification badge.
 *
 * Color-coded per domain:
 *   CLEAR       → green
 *   COMPLICATED → blue
 *   COMPLEX     → purple
 *   CHAOTIC     → red
 *   DISORDER    → gray
 *
 * Never conveys information by color alone — domain name is always visible.
 */

import { cn, formatConfidence } from "@/lib/utils";
import type { CynefinDomain } from "@/lib/types";

export interface CynefinBadgeProps {
  domain: CynefinDomain;
  /** Confidence score 0.0 – 1.0 */
  confidence?: number;
  /** Badge size — "sm" for inline use, "md" for cards, "lg" for headers */
  size?: "sm" | "md" | "lg";
  className?: string;
}

const DOMAIN_CONFIG: Record<
  CynefinDomain,
  {
    label: string;
    icon: string;
    bg: string;
    text: string;
    border: string;
    description: string;
  }
> = {
  CLEAR: {
    label: "Clear",
    icon: "✓",
    bg: "bg-green-100 dark:bg-green-950",
    text: "text-green-800 dark:text-green-300",
    border: "border-green-300 dark:border-green-800",
    description: "Cause and effect are known. Apply best practice.",
  },
  COMPLICATED: {
    label: "Complicated",
    icon: "⚙",
    bg: "bg-blue-100 dark:bg-blue-950",
    text: "text-blue-800 dark:text-blue-300",
    border: "border-blue-300 dark:border-blue-800",
    description: "Multiple right answers. Expert analysis required.",
  },
  COMPLEX: {
    label: "Complex",
    icon: "◈",
    bg: "bg-purple-100 dark:bg-purple-950",
    text: "text-purple-800 dark:text-purple-300",
    border: "border-purple-300 dark:border-purple-800",
    description: "Cause and effect only visible in hindsight. Probe first.",
  },
  CHAOTIC: {
    label: "Chaotic",
    icon: "!",
    bg: "bg-red-100 dark:bg-red-950",
    text: "text-red-800 dark:text-red-300",
    border: "border-red-300 dark:border-red-800",
    description: "No cause-effect relationships visible. Act to stabilize.",
  },
  DISORDER: {
    label: "Disorder",
    icon: "?",
    bg: "bg-zinc-100 dark:bg-zinc-800",
    text: "text-zinc-700 dark:text-zinc-300",
    border: "border-zinc-300 dark:border-zinc-600",
    description: "Domain is unclear. Gather more information.",
  },
};

const SIZE_CLASSES = {
  sm: {
    badge: "px-1.5 py-0.5 text-xs gap-1",
    icon: "text-xs",
    confidence: "text-xs",
  },
  md: {
    badge: "px-2.5 py-1 text-sm gap-1.5",
    icon: "text-sm",
    confidence: "text-xs",
  },
  lg: {
    badge: "px-3 py-1.5 text-base gap-2",
    icon: "text-base",
    confidence: "text-sm",
  },
};

export function CynefinBadge({
  domain,
  confidence = 0,
  size = "md",
  className,
}: CynefinBadgeProps) {
  const config = DOMAIN_CONFIG[domain];
  const sizes = SIZE_CLASSES[size];

  return (
    <span
      data-testid="cynefin-badge"
      data-domain={domain}
      title={config.description}
      role="img"
      aria-label={`Cynefin domain: ${config.label}, confidence: ${formatConfidence(confidence)}`}
      className={cn(
        "inline-flex items-center rounded-full border font-medium",
        config.bg,
        config.text,
        config.border,
        sizes.badge,
        className
      )}
    >
      <span className={cn("font-bold", sizes.icon)} aria-hidden="true">
        {config.icon}
      </span>
      <span>{config.label}</span>
      {confidence > 0 && (
        <span
          className={cn("opacity-75", sizes.confidence)}
          data-testid="cynefin-confidence"
        >
          {formatConfidence(confidence)}
        </span>
      )}
    </span>
  );
}

"use client";

/**
 * GuardianVerdictBadge — Shows the Guardian policy engine verdict.
 *
 * APPROVED           → green check
 * REJECTED           → red x
 * REQUIRES_ESCALATION→ amber warning
 * PENDING            → gray spinner
 */

import { CheckCircle2, XCircle, AlertTriangle, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GuardianVerdict } from "@/lib/types";

export interface GuardianVerdictBadgeProps {
  verdict: GuardianVerdict;
  /** Optional policy rules cited by the Guardian */
  policyRulesCited?: string[];
  size?: "sm" | "md" | "lg";
  className?: string;
}

const VERDICT_CONFIG: Record<
  GuardianVerdict,
  {
    label: string;
    icon: typeof CheckCircle2;
    iconClass: string;
    bg: string;
    text: string;
    border: string;
  }
> = {
  APPROVED: {
    label: "Approved",
    icon: CheckCircle2,
    iconClass: "text-green-600 dark:text-green-400",
    bg: "bg-green-50 dark:bg-green-950",
    text: "text-green-800 dark:text-green-300",
    border: "border-green-200 dark:border-green-800",
  },
  REJECTED: {
    label: "Rejected",
    icon: XCircle,
    iconClass: "text-red-600 dark:text-red-400",
    bg: "bg-red-50 dark:bg-red-950",
    text: "text-red-800 dark:text-red-300",
    border: "border-red-200 dark:border-red-800",
  },
  REQUIRES_ESCALATION: {
    label: "Requires Escalation",
    icon: AlertTriangle,
    iconClass: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-50 dark:bg-amber-950",
    text: "text-amber-800 dark:text-amber-300",
    border: "border-amber-200 dark:border-amber-800",
  },
  PENDING: {
    label: "Pending",
    icon: Clock,
    iconClass: "text-zinc-500 dark:text-zinc-400",
    bg: "bg-zinc-50 dark:bg-zinc-900",
    text: "text-zinc-700 dark:text-zinc-300",
    border: "border-zinc-200 dark:border-zinc-700",
  },
};

const SIZE_CLASSES = {
  sm: { wrapper: "px-2 py-0.5 text-xs gap-1", icon: "h-3 w-3" },
  md: { wrapper: "px-2.5 py-1 text-sm gap-1.5", icon: "h-4 w-4" },
  lg: { wrapper: "px-3 py-1.5 text-base gap-2", icon: "h-5 w-5" },
};

export function GuardianVerdictBadge({
  verdict,
  policyRulesCited,
  size = "md",
  className,
}: GuardianVerdictBadgeProps) {
  const config = VERDICT_CONFIG[verdict];
  const sizeClass = SIZE_CLASSES[size];
  const Icon = config.icon;

  const title =
    policyRulesCited && policyRulesCited.length > 0
      ? `Policy rules: ${policyRulesCited.join(", ")}`
      : `Guardian verdict: ${config.label}`;

  return (
    <span
      data-testid="guardian-verdict-badge"
      data-verdict={verdict}
      title={title}
      role="img"
      aria-label={`Guardian verdict: ${config.label}`}
      className={cn(
        "inline-flex items-center rounded-full border font-medium",
        config.bg,
        config.text,
        config.border,
        sizeClass.wrapper,
        className
      )}
    >
      <Icon
        className={cn("shrink-0", config.iconClass, sizeClass.icon)}
        aria-hidden="true"
      />
      <span>{config.label}</span>
    </span>
  );
}

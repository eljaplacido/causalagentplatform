/**
 * VerdictBadge — the decision layer's verdict, as causal_decision returned it.
 *
 * The four verdicts (act, act_with_approval, experiment, refuse) are the
 * policy's, not the UI's; the badge shows the value verbatim with an icon, so
 * the verdict is not conveyed by colour alone.
 */

import { CheckCircle2, FlaskConical, ShieldAlert, UserCheck } from "lucide-react";
import type { DecisionVerdict } from "@/lib/api";
import { cn } from "@/lib/utils";

const STYLE: Record<DecisionVerdict, { icon: typeof CheckCircle2; className: string }> = {
  act: {
    icon: CheckCircle2,
    className: "border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-300",
  },
  act_with_approval: {
    icon: UserCheck,
    className: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300",
  },
  experiment: {
    icon: FlaskConical,
    className: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  refuse: {
    icon: ShieldAlert,
    className: "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300",
  },
};

export function VerdictBadge({ verdict }: { verdict: DecisionVerdict }) {
  const style = STYLE[verdict];
  const Icon = style?.icon ?? ShieldAlert;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] font-semibold",
        style?.className ?? "border-border bg-muted text-foreground"
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {verdict}
    </span>
  );
}

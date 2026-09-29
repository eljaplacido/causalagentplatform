"use client";

/**
 * Term — a word of causal jargon with its meaning one hover or focus away.
 *
 * The glossary is here so every page explains a term the same way. The
 * definition is also the element's accessible description.
 */

import { useId } from "react";
import { cn } from "@/lib/utils";

export const GLOSSARY = {
  treatment:
    "The thing you did or could do (the cause you're asking about), e.g. sending an email.",
  outcome: "The result you care about, e.g. spend or churn.",
  confounder:
    "Something that influences both the treatment and the outcome. If ignored, it makes the raw comparison misleading.",
  graph:
    "Your assumptions about what causes what, written as arrows: A -> B means A influences B. The analysis is only as good as these arrows.",
  identified:
    "The graph says the effect can be computed from the data you have. Not identified means no amount of this data can answer it, and the platform says what would.",
  adjustment:
    "The variables the analysis compares like-with-like on, chosen from your graph so every non-causal path between treatment and outcome is blocked.",
  ate: "Average treatment effect: the average change in the outcome if everyone were treated vs. no one.",
  att: "Average effect on the treated: the average change for the units that actually got the treatment.",
  interval:
    "The range of effects compatible with the data at 95% confidence. If it's wide, the data can't tell the answer precisely.",
  evalue:
    "How strong an unmeasured confounder would have to be (as a risk ratio, with both treatment and outcome) to explain the effect away. Bigger is more robust.",
  refutation:
    "Tests that try to break the result, e.g. replacing the treatment with a random placebo should give zero.",
  status:
    "supported: identified, estimators agree, no falsification test failed. provisional: as supported, but some graph edges aren't reviewed. contested: a check failed; the numbers are reported without causal language.",
  mwe: "Minimum worthwhile effect: the smallest effect that would make the action worth taking.",
  verdict:
    "act: the evidence supports it. act_with_approval: supported, but a person must sign off. experiment: not settled; run the experiment described. refuse: the evidence doesn't support acting.",
  randomized: "Units were assigned to treatment by chance (an A/B test), so no confounder can explain the difference.",
  observational:
    "Treatment happened for reasons of its own; the analysis relies on the graph naming every common cause.",
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;

export function Term({
  k,
  children,
  className,
}: {
  k: GlossaryKey;
  children: React.ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <span className={cn("group relative inline-block", className)}>
      <span
        tabIndex={0}
        aria-describedby={id}
        className="cursor-help underline decoration-dotted decoration-muted-foreground/60 underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
      >
        {children}
      </span>
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none invisible absolute left-0 top-full z-50 mt-1 w-72 rounded-md border border-border bg-popover p-2.5 text-xs font-normal normal-case leading-relaxed tracking-normal text-popover-foreground shadow-md opacity-0 transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {GLOSSARY[k]}
      </span>
    </span>
  );
}

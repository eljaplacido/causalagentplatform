"use client";

/**
 * ViewNav — links between the analysis views that read the kernel's records:
 * the DAG Workbench, the decision ledger and the assumption registry.
 * The current view is marked with aria-current="page".
 */

import Link from "next/link";
import { cn } from "@/lib/utils";

export type ViewNavKey = "workbench" | "decisions" | "registry";

const VIEWS: ReadonlyArray<{ key: ViewNavKey; href: string; label: string }> = [
  { key: "workbench", href: "/workbench", label: "DAG Workbench" },
  { key: "decisions", href: "/decisions", label: "Decision ledger" },
  { key: "registry", href: "/registry", label: "Assumption registry" },
];

export function ViewNav({ current, className }: { current: ViewNavKey; className?: string }) {
  return (
    <nav aria-label="Analysis views" className={cn("flex items-center gap-1", className)}>
      {VIEWS.map((v) => (
        <Link
          key={v.key}
          href={v.href}
          aria-current={v.key === current ? "page" : undefined}
          className={cn(
            "rounded-md px-2 py-1 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            v.key === current
              ? "bg-muted font-semibold text-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground"
          )}
        >
          {v.label}
        </Link>
      ))}
    </nav>
  );
}

"use client";

/**
 * AppNav — the one navigation bar, on every page (rendered by the root layout).
 *
 * Links to every page (src/lib/navigation.ts), the current one marked with
 * aria-current="page". The persona control on the right sets only the default
 * landing page -- where the root route sends you -- and is labelled so; the
 * bar never names a persona as the current view. It renders after mount
 * because the persona is read from browser storage, which the server does not
 * have (rendering it on the server would disagree with the client's first
 * render).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_GROUPS, activeHref, PERSONA_LANDING } from "@/lib/navigation";
import { usePersonaStore } from "@/lib/store";
import type { Persona } from "@/lib/types";
import { cn } from "@/lib/utils";

const PERSONA_LABELS: Record<Persona, string> = {
  OPERATOR: "Operator / SRE",
  ANALYST: "Causal Analyst",
  EXECUTIVE: "Executive",
  GOVERNANCE: "Governance Officer",
  ENGINEER: "Platform Engineer",
};

function LandingPicker() {
  const { currentPersona, setPersona } = usePersonaStore();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <span>Default landing page</span>
      <select
        value={currentPersona}
        onChange={(e) => setPersona(e.target.value as Persona)}
        aria-label="Default landing page"
        data-testid="landing-picker"
        className="rounded-md border border-input bg-background px-1.5 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {(Object.keys(PERSONA_LANDING) as Persona[]).map((p) => (
          <option key={p} value={p}>
            {PERSONA_LABELS[p]} ({PERSONA_LANDING[p]})
          </option>
        ))}
      </select>
    </label>
  );
}

export function AppNav() {
  const pathname = usePathname();
  const active = activeHref(pathname);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-muted/40 px-4 py-2" data-testid="app-nav">
      <Link href="/" className="text-sm font-bold text-foreground">
        Causal Platform
      </Link>
      <nav aria-label="Main" className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {NAV_GROUPS.map((g) => (
          <div key={g.label} role="group" aria-label={g.label} className="flex flex-wrap items-center gap-1">
            <span className="mr-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {g.label}
            </span>
            {g.items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={item.href === active ? "page" : undefined}
                className={cn(
                  "rounded-md px-2 py-1 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  item.href === active
                    ? "bg-background font-semibold text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-background hover:text-foreground"
                )}
              >
                {item.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="ml-auto">
        <LandingPicker />
      </div>
    </div>
  );
}

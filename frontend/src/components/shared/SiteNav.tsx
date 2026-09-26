"use client";

/**
 * SiteNav — the one navigation bar, rendered by the root layout above every
 * page (the 404 page included).
 *
 * The brand links home; then the five persona pages and the three analysis
 * views that read the kernel's records. The current page is marked with
 * aria-current="page". Persona pages and analysis views used to be two
 * unconnected nav systems (a persona dropdown on one set, ViewNav on the
 * other), and /decisions and /registry had no way home.
 *
 * At phone width the link row scrolls within itself, so the bar never widens
 * the page.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { PERSONA_ROUTES } from "./PersonaSelector";

const PERSONA_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: PERSONA_ROUTES.OPERATOR, label: "Incidents" },
  { href: PERSONA_ROUTES.ANALYST, label: "Analyst" },
  { href: PERSONA_ROUTES.GOVERNANCE, label: "Governance" },
  { href: PERSONA_ROUTES.ENGINEER, label: "Engineer" },
  { href: PERSONA_ROUTES.EXECUTIVE, label: "Executive" },
];

const VIEW_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "/workbench", label: "DAG Workbench" },
  { href: "/decisions", label: "Decision ledger" },
  { href: "/registry", label: "Assumption registry" },
];

function NavLink({ href, label, current }: { href: string; label: string; current: boolean }) {
  return (
    <Link
      href={href}
      aria-current={current ? "page" : undefined}
      className={cn(
        "shrink-0 whitespace-nowrap rounded-md px-2 py-1 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        current
          ? "bg-muted font-semibold text-foreground"
          : "text-muted-foreground hover:bg-muted hover:text-foreground"
      )}
    >
      {label}
    </Link>
  );
}

export function SiteNav({ className }: { className?: string }) {
  const pathname = usePathname() ?? "";
  const isCurrent = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <nav
      aria-label="Site"
      className={cn(
        "flex w-full min-w-0 items-center gap-2 border-b border-border bg-background px-4 py-1.5",
        className
      )}
    >
      <Link
        href="/"
        className="shrink-0 whitespace-nowrap text-sm font-bold text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Causal Platform
      </Link>
      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
        {PERSONA_LINKS.map((l) => (
          <NavLink key={l.href} {...l} current={isCurrent(l.href)} />
        ))}
        <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-border" />
        {VIEW_LINKS.map((l) => (
          <NavLink key={l.href} {...l} current={isCurrent(l.href)} />
        ))}
      </div>
    </nav>
  );
}

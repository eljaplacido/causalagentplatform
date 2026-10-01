/**
 * The app's one navigation: every page, and which one a persona lands on.
 *
 * A persona chooses the default landing page (the root route redirects there)
 * and nothing else: every page is reachable from the navigation whatever the
 * persona, and no page claims to be "the Operator's" view.
 */

import type { Persona } from "@/lib/types";

export interface NavItem {
  href: string;
  label: string;
}

export interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: "Operations",
    items: [
      { href: "/incidents", label: "Incidents" },
      { href: "/analyst", label: "Analyst" },
      { href: "/governance", label: "Governance" },
      { href: "/executive", label: "Executive" },
      { href: "/engineer", label: "Platform health" },
    ],
  },
  {
    label: "Causal analysis",
    items: [
      { href: "/decisions/new", label: "New decision" },
      { href: "/decisions", label: "Decision ledger" },
      { href: "/registry", label: "Assumption registry" },
      { href: "/workbench", label: "DAG workbench" },
    ],
  },
];

/** Where each persona lands from the root route. */
export const PERSONA_LANDING: Record<Persona, string> = {
  OPERATOR: "/incidents",
  ANALYST: "/analyst",
  EXECUTIVE: "/executive",
  GOVERNANCE: "/governance",
  ENGINEER: "/engineer",
};

/**
 * The nav item a path belongs to: the longest href that is the path or a
 * prefix of it at a segment boundary, so /decisions/new is "New decision" and
 * not also "Decision ledger".
 */
export function activeHref(pathname: string | null): string | null {
  if (!pathname) return null;
  let best: string | null = null;
  for (const g of NAV_GROUPS) {
    for (const item of g.items) {
      const match = pathname === item.href || pathname.startsWith(`${item.href}/`);
      if (match && (best === null || item.href.length > best.length)) best = item.href;
    }
  }
  return best;
}

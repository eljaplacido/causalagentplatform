/**
 * Reading a SessionStateResponse (POST /api/v1/session/run) without
 * contradicting it.
 *
 * `cynefin_domain` is the session's classification. A run can also be handed to
 * a person without being CHAOTIC: the lookahead planner finding no safe action
 * (flag `no_safe_action_found`) or an unresolved DISORDER routes a COMPLICATED
 * or COMPLEX run to the escalation node too (orchestration/graph.py
 * route_after_plan_ahead, route_after_disorder), and that node's text has said
 * "CHAOTIC domain" whatever the classification was. So the page takes the
 * domain from `cynefin_domain`, the escalation from the flags, and says so
 * when the backend's prose names a different domain -- rather than showing
 * "Complicated" in one corner and "CHAOTIC" in another.
 */

import type { SessionStateResponse } from "@/lib/api";

const DOMAINS = ["CLEAR", "COMPLICATED", "COMPLEX", "CHAOTIC", "DISORDER"] as const;

export interface Escalation {
  escalated: boolean;
  /** Why, from the flags; null when not escalated. */
  reason: string | null;
}

const REASONS: Record<string, string> = {
  chaotic_domain: "the situation is classified CHAOTIC",
  no_safe_action_found: "planning found no safe action",
  planning_found_no_safe_action: "planning found no safe action",
  disorder_unresolved: "the situation could not be classified",
};

/** Whether the run was handed to a person, and why, as its flags and domain say. */
export function escalationOf(state: Pick<SessionStateResponse, "flags" | "cynefin_domain">): Escalation {
  const flags = state.flags ?? [];
  const escalated =
    flags.includes("human_escalation_required") || state.cynefin_domain === "CHAOTIC";
  if (!escalated) return { escalated: false, reason: null };
  const tagged = flags.find((f) => f.startsWith("escalation:"));
  if (tagged) {
    const key = tagged.slice("escalation:".length);
    return { escalated, reason: REASONS[key] ?? key.replace(/_/g, " ") };
  }
  if (flags.includes("no_safe_action_found")) return { escalated, reason: REASONS["no_safe_action_found"]! };
  if (state.cynefin_domain === "CHAOTIC") return { escalated, reason: REASONS["chaotic_domain"]! };
  if (state.cynefin_domain === "DISORDER") return { escalated, reason: REASONS["disorder_unresolved"]! };
  return { escalated, reason: "no reason was given" };
}

/**
 * The Cynefin domains `texts` name that are not the session's own, e.g.
 * ["CHAOTIC"] for a COMPLICATED session whose escalation text says "CHAOTIC
 * domain". Empty when the prose agrees with the classification.
 */
export function otherDomainsNamed(domain: string, texts: readonly string[]): string[] {
  const own = domain.toUpperCase();
  const found = new Set<string>();
  for (const t of texts) {
    for (const d of DOMAINS) {
      if (d !== own && new RegExp(`\\b${d}\\b`, "i").test(t)) found.add(d);
    }
  }
  return [...found];
}

/** A discovery agent's "Propose edge: <source> → <target> (<TYPE>)", as its parts; null otherwise. */
export function parseProposedEdge(action: string): { source: string; target: string; edgeType: string } | null {
  const m = /^Propose edge:\s*(\S+)\s*→\s*(\S+)\s*\(([A-Z_]+)\)\s*$/.exec(action);
  return m ? { source: m[1]!, target: m[2]!, edgeType: m[3]! } : null;
}

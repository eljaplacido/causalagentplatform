/**
 * Claim markers in a decision's rationale, and the display-only rounding the
 * decision views put beside a raw number.
 *
 * causal_decision writes its rationale with claim markers (⟦claim:effect⟧,
 * ASCII fallback [[claim:effect]]) and causal_core.claims.render fills each one
 * from the outcome's own `claims`: the claim whose `id` is the marker's name,
 * rendered as its `text` plus "%" when its unit is "%". Claim ids are unique
 * within an outcome (render keys them by id), so the mapping is unambiguous
 * exactly when the outcome is a result carrying that id. Anything else -- a
 * refusal, which carries no claims, or an id the outcome lacks -- stays a
 * marker; the UI never guesses a number for it.
 */

import type { KernelClaim, KernelOutcome } from "@/lib/api";

/** The kernel's token grammar (causal_core.claims.TOKEN), with the global flag. */
const TOKEN = /⟦claim:([\w.-]+)⟧|\[\[claim:([\w.-]+)\]\]/g;

export type RationalePart =
  | { kind: "text"; text: string }
  | { kind: "claim"; id: string; claim: KernelClaim | null };

/** The claims an outcome carries, by id; empty for a refusal or failure. */
export function claimsOf(outcome: KernelOutcome | null | undefined): Map<string, KernelClaim> {
  if (!outcome || outcome.kind !== "result") return new Map();
  return new Map(outcome.claims.map((c) => [c.id, c]));
}

/** Split a rationale into text and claim markers, each marker resolved if it can be. */
export function splitRationale(text: string, claims: Map<string, KernelClaim> = new Map()): RationalePart[] {
  const parts: RationalePart[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    const id = (m[1] ?? m[2])!;
    parts.push({ kind: "claim", id, claim: claims.get(id) ?? null });
    last = at + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

/** A claim as causal_core.claims.render writes it: its text, plus "%" for a percentage. */
export function renderClaim(claim: KernelClaim): string {
  return claim.text + (claim.unit === "%" ? "%" : "");
}

/**
 * A number rounded to four significant digits, for reading only. It is shown
 * beside the raw value, never instead of it, and never sent anywhere.
 */
export function roundedForDisplay(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toPrecision(4)));
}

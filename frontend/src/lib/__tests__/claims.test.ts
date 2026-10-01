/**
 * Filling a decision rationale's claim markers from the outcome (src/lib/claims.ts).
 *
 * Preconditions: a rationale and outcome as POST /api/v1/decisions returned them
 * (kernel-produced fixtures, src/test-fixtures/decide.ts).
 * Postconditions:
 *   - each ⟦claim:id⟧ is filled from the outcome's claim of that id exactly as
 *     causal_core.claims.render fills it -- so the filled rationale is the text
 *     causal_decision.explain returned, after its "ACT: " head;
 *   - a marker whose id the outcome lacks (every marker of a refusal) stays a marker;
 *   - the rounded display is display-only and never replaces the raw value.
 * Undefined: markup.
 */

import { describe, expect, it } from "vitest";

import { claimsOf, renderClaim, roundedForDisplay, splitRationale } from "@/lib/claims";
import { DECIDE_ACT, DECIDE_REFUSED } from "@/test-fixtures/decide";

function fill(text: string, claims: ReturnType<typeof claimsOf>): string {
  return splitRationale(text, claims)
    .map((p) => (p.kind === "text" ? p.text : p.claim ? renderClaim(p.claim) : `⟦claim:${p.id}⟧`))
    .join("");
}

describe("claim markers", () => {
  it("fills the rationale to exactly what causal_decision.explain rendered", () => {
    const rationale = DECIDE_ACT.decision.rationale;
    expect(rationale).toContain("⟦claim:effect⟧");
    const filled = fill(rationale, claimsOf(DECIDE_ACT.outcome));
    expect(filled).not.toContain("⟦claim:");
    expect(`ACT: ${filled}`).toBe(DECIDE_ACT.explanation.split("\n")[0]);
  });

  it("resolves each marker to the claim of its id, the number the analysis produced", () => {
    const parts = splitRationale(DECIDE_ACT.decision.rationale, claimsOf(DECIDE_ACT.outcome));
    const effect = parts.find((p) => p.kind === "claim" && p.id === "effect");
    expect(effect?.kind === "claim" && effect.claim?.value).toBe(
      DECIDE_ACT.outcome.kind === "result" ? DECIDE_ACT.outcome.primary.point : NaN
    );
    const level = parts.find((p) => p.kind === "claim" && p.id === "ci.level");
    expect(level?.kind === "claim" && level.claim && renderClaim(level.claim)).toBe("95%");
  });

  it("leaves a marker the outcome has no claim for (a refusal has none)", () => {
    expect(claimsOf(DECIDE_REFUSED.outcome).size).toBe(0);
    const parts = splitRationale("n = ⟦claim:n⟧ and [[claim:effect]]", claimsOf(DECIDE_REFUSED.outcome));
    expect(parts.filter((p) => p.kind === "claim").map((p) => p.kind === "claim" && p.claim)).toEqual([null, null]);
    expect(parts.filter((p) => p.kind === "claim").map((p) => p.kind === "claim" && p.id)).toEqual(["n", "effect"]);
  });

  it("reads the ASCII fallback marker too, as the kernel's token grammar does", () => {
    const filled = fill("effect [[claim:effect]]", claimsOf(DECIDE_ACT.outcome));
    expect(filled).toBe("effect -9.009");
  });
});

describe("roundedForDisplay", () => {
  it("rounds to four significant digits and leaves integers alone", () => {
    expect(roundedForDisplay(-9.00921617223674)).toBe("-9.009");
    expect(roundedForDisplay(0.6071126065745684)).toBe("0.6071");
    expect(roundedForDisplay(400)).toBe("400");
    expect(roundedForDisplay(Number.NaN)).toBe("NaN");
  });
});

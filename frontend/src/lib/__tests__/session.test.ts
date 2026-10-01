/**
 * Reading a session without contradicting it (src/lib/session.ts).
 *
 * Postconditions: the domain is cynefin_domain; the escalation and its reason
 * come from the flags; prose naming another domain is detected; an edge
 * proposal's ids are read out of the agent's action text.
 */

import { describe, expect, it } from "vitest";

import { escalationOf, otherDomainsNamed, parseProposedEdge } from "@/lib/session";

describe("escalationOf", () => {
  it("reads a planner escalation of a COMPLICATED session from its flags", () => {
    // The flags POST /api/v1/session/run returned for such a run.
    const flags = ["intake_complete", "plan_ahead_escalation", "no_safe_action_found", "human_escalation_required"];
    expect(escalationOf({ flags, cynefin_domain: "COMPLICATED" })).toEqual({
      escalated: true,
      reason: "planning found no safe action",
    });
  });

  it("prefers an explicit escalation:<reason> flag, and knows CHAOTIC escalates", () => {
    expect(escalationOf({ flags: ["human_escalation_required", "escalation:disorder_unresolved"], cynefin_domain: "DISORDER" }).reason).toBe(
      "the situation could not be classified"
    );
    expect(escalationOf({ flags: [], cynefin_domain: "CHAOTIC" }).reason).toBe("the situation is classified CHAOTIC");
    expect(escalationOf({ flags: ["intake_complete"], cynefin_domain: "COMPLEX" })).toEqual({ escalated: false, reason: null });
  });
});

describe("otherDomainsNamed", () => {
  it("finds the CHAOTIC in a COMPLICATED session's escalation text", () => {
    expect(
      otherDomainsNamed("COMPLICATED", [
        "CHAOTIC domain: automated analysis suspended.",
        "Statistical association detected in COMPLICATED domain context.",
      ])
    ).toEqual(["CHAOTIC"]);
    expect(otherDomainsNamed("CHAOTIC", ["CHAOTIC domain detected."])).toEqual([]);
  });
});

describe("parseProposedEdge", () => {
  it("reads the discovery agent's action", () => {
    expect(
      parseProposedEdge("Propose edge: 01c9d937-0281-4579-9f40-3b1df56d13e8 → 5559caf1-91c6-4203-9501-1d506331a6c9 (CAUSES)")
    ).toEqual({
      source: "01c9d937-0281-4579-9f40-3b1df56d13e8",
      target: "5559caf1-91c6-4203-9501-1d506331a6c9",
      edgeType: "CAUSES",
    });
    expect(parseProposedEdge("ESCALATE: Route to human decision-maker immediately.")).toBeNull();
  });
});

/**
 * The frontend narrows on the canonical vocabulary.
 *
 * tsc cannot catch a union redeclared in a second file -- it is a second type,
 * not a conflict -- so the frontend used to narrow on ReviewStatus and
 * EvidenceType values no endpoint sends. This pins the fix from both sides:
 * at the type level (tsc fails if the frontend's EvidenceType stops being the
 * canonical one) and at the source level (types.ts may not redeclare either
 * name, and scripts/contract-parity.json lists no divergence).
 *
 * Leaves-by test for conformance entry `frontend-vocabulary-divergence`.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, expectTypeOf, it } from "vitest";
import type * as Canonical from "@causalplatform/semantic-types";
import type { Evidence, EvidenceType, ReviewQueueItem } from "@/lib/types";
import type { DAGEdge } from "@/components/graph/DAGCanvas";
import type { FragmentCardData } from "@/components/graph/FragmentCard";

const REPO = resolve(__dirname, "../../../..");
const typesSource = readFileSync(resolve(REPO, "frontend/src/lib/types.ts"), "utf8");

describe("frontend vocabulary", () => {
  it("EvidenceType is the canonical union", () => {
    expectTypeOf<EvidenceType>().toEqualTypeOf<Canonical.EvidenceType>();
    expectTypeOf<Evidence["type"]>().toEqualTypeOf<Canonical.EvidenceType>();
  });

  it("edge and fragment statuses are the canonical ones", () => {
    expectTypeOf<DAGEdge["approvalStatus"]>().toEqualTypeOf<Canonical.ApprovalStatus>();
    expectTypeOf<FragmentCardData["approvalStatus"]>().toEqualTypeOf<Canonical.FragmentApprovalStatus>();
  });

  it("the review-queue status is a view model and does not borrow ReviewStatus's name", () => {
    expectTypeOf<ReviewQueueItem["status"]>().not.toEqualTypeOf<Canonical.ReviewStatus>();
  });

  it("types.ts does not redeclare a name the canonical package owns", () => {
    for (const name of ["ReviewStatus", "EvidenceType", "CausalEdge", "AuditEntry", "ApprovalStatus", "EdgeType"]) {
      expect(typesSource, name).not.toMatch(new RegExp(`export (type|interface) ${name}\\b`));
    }
  });

  it("scripts/contract-parity.json lists no known divergence", () => {
    const parity = JSON.parse(
      readFileSync(resolve(REPO, "scripts/contract-parity.json"), "utf8")
    ) as { known_divergences: Record<string, unknown> };
    expect(Object.keys(parity.known_divergences)).toEqual([]);
  });
});

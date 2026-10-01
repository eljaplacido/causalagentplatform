/**
 * Entity ids shown as names (src/lib/entityNames.ts).
 *
 * Postconditions: an id the semantic layer has an entity for is shown as the
 * entity's name; any other id is shown short (8 characters and "…"), never as a
 * made-up label; a queued edge is labelled by its endpoints and type.
 */

import { describe, expect, it } from "vitest";

import {
  entityNameMap,
  nameOrShortId,
  reviewQueueLabel,
  shortId,
  withEntityNames,
} from "@/lib/entityNames";

const ORDER = "02a668fc-854f-462b-8ba2-64fbae7ece2b";
const UNKNOWN = "5559caf1-91c6-4203-9501-1d506331a6c9";
const names = entityNameMap([{ id: ORDER, name: "order-service" }]);

describe("entity names", () => {
  it("names a known id and shortens an unknown one", () => {
    expect(nameOrShortId(names, ORDER)).toBe("order-service");
    expect(nameOrShortId(names, ORDER.toUpperCase())).toBe("order-service");
    expect(nameOrShortId(names, UNKNOWN)).toBe("5559caf1…");
    expect(shortId("abc")).toBe("abc");
  });

  it("rewrites every id in a recommendation", () => {
    expect(withEntityNames(names, `Propose edge: ${ORDER} → ${UNKNOWN} (CAUSES)`)).toBe(
      "Propose edge: order-service → 5559caf1… (CAUSES)"
    );
    expect(withEntityNames(names, "no ids here")).toBe("no ids here");
  });

  it("labels a queued edge by what it connects", () => {
    const base = {
      id: "e1",
      object_type: "CausalEdge",
      domain: "devops",
      name: "CAUSES",
      approval_status: "DRAFT" as const,
      created_at: null,
    };
    expect(
      reviewQueueLabel({
        ...base,
        source: { id: ORDER, object_type: "CausalEntity", name: "order-service" },
        target: { id: UNKNOWN, object_type: "Outcome", name: null },
      })
    ).toBe("order-service → Outcome 5559caf1… (CAUSES)");
    // A backend without endpoints, or a row that is not an edge: its name.
    expect(reviewQueueLabel(base)).toBe("CAUSES");
    expect(reviewQueueLabel({ ...base, object_type: "GraphFragment", name: "f", source: null, target: null })).toBe("f");
  });
});

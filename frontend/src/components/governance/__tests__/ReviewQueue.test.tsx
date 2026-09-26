/**
 * ReviewQueue tests (septa-36).
 *
 * Contract: rows are ordered by risk tier (CRITICAL, HIGH, MEDIUM, LOW), keeping
 * the given order within a tier (the backend sends oldest first); each row shows
 * its tier, the reasons the tier was given (as the badge's title), and who
 * submitted the item -- "not recorded" when the backend has no submitter, never a
 * made-up name.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ReviewQueue } from "../ReviewQueue";
import type { ReviewQueueItem } from "@/lib/types";

function makeItem(overrides: Partial<ReviewQueueItem>): ReviewQueueItem {
  return {
    id: "item",
    objectName: "object",
    objectType: "CAUSAL_EDGE",
    businessDomain: "devops",
    riskTier: "MEDIUM",
    riskReasons: [],
    submittedBy: "someone",
    submittedAt: "2026-09-01T10:00:00Z",
    status: "PENDING",
    evidenceCitations: [],
    sensitivityResults: [],
    assumptions: [],
    ...overrides,
  };
}

describe("ReviewQueue", () => {
  it("orders by tier and keeps the given order within a tier", () => {
    render(
      <ReviewQueue
        items={[
          makeItem({ id: "low", riskTier: "LOW" }),
          makeItem({ id: "high-older", riskTier: "HIGH" }),
          makeItem({ id: "critical", riskTier: "CRITICAL" }),
          makeItem({ id: "high-newer", riskTier: "HIGH" }),
        ]}
        onSelectItem={vi.fn()}
      />
    );
    const ids = [
      screen.getByTestId("first-review-item"),
      ...screen.getAllByTestId("review-item"),
    ].map((row) => row.getAttribute("data-item-id"));
    expect(ids).toEqual(["critical", "high-older", "high-newer", "low"]);
  });

  it("shows the tier's reasons and an unrecorded submitter as such", () => {
    render(
      <ReviewQueue
        items={[
          makeItem({
            id: "edge",
            riskTier: "HIGH",
            riskReasons: ["no stored sensitivity result says it is robust"],
            submittedBy: null,
          }),
        ]}
        onSelectItem={vi.fn()}
      />
    );
    const row = screen.getByTestId("first-review-item");
    expect(within(row).getByText("High")).toHaveAttribute(
      "title",
      "no stored sensitivity result says it is robust"
    );
    expect(within(row).getByText("not recorded")).toBeInTheDocument();
  });
});

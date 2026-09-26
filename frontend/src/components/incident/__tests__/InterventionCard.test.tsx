/**
 * InterventionCard tests
 *
 * UX contract:
 *   - Approve and reject buttons ALWAYS visible regardless of Guardian verdict
 *   - Renders estimated effect
 *   - Approve flow records reason in audit trail (reason textarea visible after first click)
 *   - Reject flow works identically
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InterventionCard } from "../InterventionCard";
import type { Intervention } from "@/lib/types";

// ─── Test fixtures ─────────────────────────────────────────────────────────────

function makeIntervention(
  overrides: Partial<Intervention> = {}
): Intervention {
  return {
    id: "int-test-1",
    title: "Roll back payment-api to v2.3.0",
    description: "Revert the deployment to restore stable connection pool behavior.",
    estimatedEffect: {
      pointEstimate: -87,
      lowerBound: -95,
      upperBound: -72,
      unit: "% latency reduction",
      direction: "POSITIVE",
    },
    confidence: 0.76,
    assumptions: [
      {
        id: "ass-1",
        statement: "v2.3.0 does not contain the connection pool regression",
        strength: "STRONG",
        reviewStatus: "CURRENT",
        lastReviewedAt: new Date().toISOString(),
        reviewedBy: "Mihail",
        scope: "payment-api",
        linkedEdgeIds: [],
      },
    ],
    cost: "~15 min",
    riskTier: "MEDIUM",
    guardianVerdict: "APPROVED",
    ...overrides,
  };
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("InterventionCard — UX contract: approve/reject always visible", () => {
  it("shows approve button when Guardian verdict is APPROVED", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "APPROVED" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("approve-button")).toBeDefined();
  });

  it("shows reject button when Guardian verdict is APPROVED", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "APPROVED" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("reject-button")).toBeDefined();
  });

  it("shows approve button when Guardian verdict is REJECTED", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "REJECTED" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    // UX contract: ALWAYS visible, even if Guardian already rejected it
    expect(screen.getByTestId("approve-button")).toBeDefined();
  });

  it("shows reject button when Guardian verdict is REJECTED", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "REJECTED" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("reject-button")).toBeDefined();
  });

  it("shows approve button when Guardian verdict is PENDING", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "PENDING" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("approve-button")).toBeDefined();
  });

  it("shows approve button when Guardian verdict is REQUIRES_ESCALATION", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({
          guardianVerdict: "REQUIRES_ESCALATION",
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("approve-button")).toBeDefined();
    expect(screen.getByTestId("reject-button")).toBeDefined();
  });
});

describe("InterventionCard — estimated effect rendering", () => {
  it("renders the effect estimate bar", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByTestId("effect-estimate-bar")).toBeDefined();
  });

  it("shows the point estimate value and unit", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    // "-87.00 % latency reduction" or similar
    expect(screen.getByText(/-87\.00/)).toBeDefined();
  });

  it("shows confidence intervals", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    // CI text includes bounds
    expect(screen.getByText(/95% CI/)).toBeDefined();
    expect(screen.getByText(/-95\.00/)).toBeDefined();
    expect(screen.getByText(/-72\.00/)).toBeDefined();
  });
});

describe("InterventionCard — approve flow", () => {
  it("clicking approve shows reason textarea", async () => {
    const user = userEvent.setup();

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("approve-button"));

    // After first click, reason textarea should appear
    expect(
      screen.getByLabelText(/approval reason/i)
    ).toBeDefined();
  });

  it("clicking approve twice (confirm) calls onApprove with the reason", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={onApprove}
        onReject={vi.fn()}
      />
    );

    // First click → show reason input
    await user.click(screen.getByTestId("approve-button"));

    // Fill in reason
    const textarea = screen.getByLabelText(/approval reason/i);
    await user.type(textarea, "Rollback is safe based on post-mortem evidence.");

    // Second click → confirm
    await user.click(screen.getByTestId("approve-button"));

    await waitFor(() => {
      expect(onApprove).toHaveBeenCalledWith(
        "int-test-1",
        "Rollback is safe based on post-mortem evidence."
      );
    });
  });

  it("cancel button hides reason input", async () => {
    const user = userEvent.setup();

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("approve-button"));
    expect(screen.getByLabelText(/approval reason/i)).toBeDefined();

    await user.click(screen.getByText("Cancel"));
    expect(screen.queryByLabelText(/approval reason/i)).toBeNull();
  });
});

describe("InterventionCard — reject flow", () => {
  it("clicking reject shows reason textarea", async () => {
    const user = userEvent.setup();

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("reject-button"));
    expect(screen.getByLabelText(/rejection reason/i)).toBeDefined();
  });

  it("confirm rejection calls onReject with reason", async () => {
    const user = userEvent.setup();
    const onReject = vi.fn().mockResolvedValue(undefined);

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={onReject}
      />
    );

    await user.click(screen.getByTestId("reject-button"));
    const textarea = screen.getByLabelText(/rejection reason/i);
    await user.type(textarea, "Risk is too high without further validation.");
    await user.click(screen.getByTestId("reject-button"));

    await waitFor(() => {
      expect(onReject).toHaveBeenCalledWith(
        "int-test-1",
        "Risk is too high without further validation."
      );
    });
  });
});

describe("InterventionCard — backend failure (fail-closed)", () => {
  it("approve rejection shows the error and keeps the reason input open", async () => {
    const user = userEvent.setup();
    const onApprove = vi
      .fn()
      .mockRejectedValue(new Error("Network request failed. The decision was NOT recorded."));

    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "PENDING" })}
        onApprove={onApprove}
        onReject={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.type(screen.getByLabelText(/approval reason/i), "Safe rollback.");
    await user.click(screen.getByTestId("approve-button"));

    const alert = await screen.findByTestId("intervention-action-error");
    expect(alert).toHaveTextContent(/Approval failed: Network request failed/);
    expect(alert).toHaveTextContent(/NOT recorded/);
    // Prior state kept — the user can retry
    expect(screen.getByLabelText(/approval reason/i)).toHaveValue("Safe rollback.");
    expect(screen.getByTestId("approve-button")).toHaveTextContent("Confirm Approval");
    expect(screen.getByTestId("approve-button")).toBeEnabled();
  });

  it("reject rejection shows the error and keeps the reason input open", async () => {
    const user = userEvent.setup();
    const onReject = vi.fn().mockRejectedValue(new Error("Request failed with status 500"));

    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "PENDING" })}
        onApprove={vi.fn()}
        onReject={onReject}
      />
    );

    await user.click(screen.getByTestId("reject-button"));
    await user.type(screen.getByLabelText(/rejection reason/i), "Too risky.");
    await user.click(screen.getByTestId("reject-button"));

    const alert = await screen.findByTestId("intervention-action-error");
    expect(alert).toHaveTextContent(/Rejection failed: Request failed with status 500/);
    expect(screen.getByLabelText(/rejection reason/i)).toHaveValue("Too risky.");
  });

  it("successful decision closes the reason input without an error", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={onApprove}
        onReject={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.click(screen.getByTestId("approve-button"));

    await waitFor(() => {
      expect(screen.queryByLabelText(/approval reason/i)).toBeNull();
    });
    expect(screen.queryByTestId("intervention-action-error")).toBeNull();
  });
});

describe("InterventionCard — intervention title and description", () => {
  it("renders the intervention title", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(
      screen.getByText("Roll back payment-api to v2.3.0")
    ).toBeDefined();
  });

  it("renders the intervention description", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(
      screen.getByText(
        "Revert the deployment to restore stable connection pool behavior."
      )
    ).toBeDefined();
  });
});

describe("InterventionCard — assumptions progressive disclosure", () => {
  it("shows assumption count", () => {
    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    expect(screen.getByText(/1 assumption/)).toBeDefined();
  });

  it("expands assumptions on click", async () => {
    const user = userEvent.setup();

    render(
      <InterventionCard
        intervention={makeIntervention()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );

    const expandButton = screen.getByText(/1 assumption/);
    await user.click(expandButton);

    expect(
      screen.getByText(
        "v2.3.0 does not contain the connection pool regression"
      )
    ).toBeDefined();
  });
});

// Preconditions: an Intervention whose estimatedEffect is absent -- which is
// every incident intervention today, since neither the triage endpoint nor the
// incidents API returns an estimate.
// Postconditions: the card says there is no estimate and renders no interval
// or point estimate.
describe("InterventionCard — no estimate is not a zero estimate", () => {
  it("renders 'No effect estimate' and no interval when estimatedEffect is absent", () => {
    const withoutEffect: Intervention = makeIntervention();
    delete withoutEffect.estimatedEffect;
    render(
      <InterventionCard intervention={withoutEffect} onApprove={vi.fn()} onReject={vi.fn()} />
    );
    expect(screen.getByTestId("no-effect-estimate")).toHaveTextContent("No effect estimate");
    expect(screen.queryByTestId("effect-estimate-bar")).toBeNull();
    expect(document.body.textContent).not.toMatch(/95% CI/);
  });

  it("shows a human decision beside the Guardian verdict, not instead of it", () => {
    render(
      <InterventionCard
        intervention={makeIntervention({ guardianVerdict: "REQUIRES_ESCALATION", humanDecision: "APPROVED" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />
    );
    expect(screen.getByTestId("guardian-verdict-badge").getAttribute("data-verdict")).toBe(
      "REQUIRES_ESCALATION"
    );
    expect(screen.getByTestId("human-decision").getAttribute("data-decision")).toBe("APPROVED");
  });
});

/**
 * ApprovalPanel tests
 *
 * Fail-closed contract (roadmap P0.6):
 *   - The success state ("recorded in the immutable audit trail") appears ONLY
 *     after the onApprove/onReject promise resolves (backend confirmed).
 *   - If the promise rejects: no success state, the error is shown, and the
 *     item stays in its prior state (reason input still open, reason kept).
 *   - When decisionsDisabledReason is set (mock data), approve/reject are disabled.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalPanel } from "../ApprovalPanel";
import type { ReviewQueueItem } from "@/lib/types";

// ─── Test fixtures ─────────────────────────────────────────────────────────────

function makeItem(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    objectName: "payment_latency → checkout_conversion",
    objectType: "CAUSAL_EDGE",
    businessDomain: "payments",
    riskTier: "HIGH",
    submittedBy: "ryo.tanaka",
    submittedAt: new Date().toISOString(),
    status: "PENDING",
    evidenceCitations: [],
    sensitivityResults: [],
    assumptions: [],
    ...overrides,
  };
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const REASON = "Evidence and sensitivity analysis support this edge.";

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("ApprovalPanel — success only after backend confirmation", () => {
  it("shows the approval success state only after onApprove resolves", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    const onApprove = vi.fn(() => pending.promise);

    render(
      <ApprovalPanel item={makeItem()} onApprove={onApprove} onReject={vi.fn()} />
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.type(screen.getByLabelText(/approval reason/i), REASON);
    await user.click(screen.getByTestId("confirm-approval"));

    expect(onApprove).toHaveBeenCalledWith(makeItem().id, REASON, "COMPLICATED");
    // Request in flight: no success claim yet
    expect(screen.queryByTestId("approval-success")).toBeNull();
    expect(screen.queryByText(/recorded in the immutable audit trail/i)).toBeNull();
    expect(screen.getByTestId("confirm-approval")).toBeDisabled();

    pending.resolve();

    await waitFor(() => {
      expect(screen.getByTestId("approval-success")).toBeInTheDocument();
    });
    expect(screen.getByText("Approved successfully")).toBeInTheDocument();
    expect(
      screen.getByText(/recorded in the immutable audit trail/i)
    ).toBeInTheDocument();
  });

  it("shows the rejection success state only after onReject resolves", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    const onReject = vi.fn(() => pending.promise);

    render(
      <ApprovalPanel item={makeItem()} onApprove={vi.fn()} onReject={onReject} />
    );

    await user.click(screen.getByTestId("reject-button"));
    await user.type(screen.getByLabelText(/rejection reason/i), REASON);
    await user.click(screen.getByTestId("confirm-rejection"));

    expect(onReject).toHaveBeenCalledWith(makeItem().id, REASON);
    expect(screen.queryByTestId("approval-success")).toBeNull();

    pending.resolve();

    await waitFor(() => {
      expect(screen.getByText("Rejected successfully")).toBeInTheDocument();
    });
  });
});

describe("ApprovalPanel — backend failure is shown, never marked approved", () => {
  it("on approve rejection: no success state, error shown, reason kept", async () => {
    const user = userEvent.setup();
    const onApprove = vi
      .fn()
      .mockRejectedValue(
        new Error("Guardian verdict was REJECTED: DISORDER domain")
      );

    render(
      <ApprovalPanel item={makeItem()} onApprove={onApprove} onReject={vi.fn()} />
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.type(screen.getByLabelText(/approval reason/i), REASON);
    await user.click(screen.getByTestId("confirm-approval"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Approval failed/);
    expect(alert).toHaveTextContent(/Guardian verdict was REJECTED/);
    expect(alert).toHaveTextContent(/NOT approved/);

    expect(screen.queryByTestId("approval-success")).toBeNull();
    expect(screen.queryByText("Approved successfully")).toBeNull();
    // Prior state kept: still in approve mode with the typed reason, can retry
    expect(screen.getByLabelText(/approval reason/i)).toHaveValue(REASON);
    expect(screen.getByTestId("confirm-approval")).toBeEnabled();
  });

  it("on reject rejection: no success state, error shown", async () => {
    const user = userEvent.setup();
    const onReject = vi
      .fn()
      .mockRejectedValue(new Error("Request failed with status 500"));

    render(
      <ApprovalPanel item={makeItem()} onApprove={vi.fn()} onReject={onReject} />
    );

    await user.click(screen.getByTestId("reject-button"));
    await user.type(screen.getByLabelText(/rejection reason/i), REASON);
    await user.click(screen.getByTestId("confirm-rejection"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Rejection failed/);
    expect(alert).toHaveTextContent(/status 500/);
    expect(screen.queryByTestId("approval-success")).toBeNull();
    expect(screen.getByLabelText(/rejection reason/i)).toHaveValue(REASON);
  });

  it("does not carry a confirmation over to a different item", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    const first = makeItem();
    const second = makeItem({
      id: "9b2c6d1e-0000-4000-8000-000000000002",
      objectName: "db_connection_pool_size → api_response_time",
    });

    const { rerender } = render(
      <ApprovalPanel item={first} onApprove={onApprove} onReject={vi.fn()} />
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.click(screen.getByTestId("confirm-approval"));
    await screen.findByTestId("approval-success");

    rerender(
      <ApprovalPanel item={second} onApprove={onApprove} onReject={vi.fn()} />
    );

    expect(screen.queryByTestId("approval-success")).toBeNull();
    expect(screen.getByText(second.objectName)).toBeInTheDocument();
    expect(screen.getByTestId("approve-button")).toBeEnabled();
  });
});

describe("ApprovalPanel — decisions disabled (mock data)", () => {
  it("disables approve and reject and explains why", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    const onReject = vi.fn().mockResolvedValue(undefined);

    render(
      <ApprovalPanel
        item={makeItem()}
        onApprove={onApprove}
        onReject={onReject}
        decisionsDisabledReason="Mock items cannot be approved or rejected."
      />
    );

    expect(screen.getByTestId("approve-button")).toBeDisabled();
    expect(screen.getByTestId("reject-button")).toBeDisabled();
    expect(screen.getByTestId("decisions-disabled-notice")).toHaveTextContent(
      /cannot be approved or rejected/
    );

    await user.click(screen.getByTestId("approve-button"));
    await user.click(screen.getByTestId("reject-button"));

    expect(screen.queryByLabelText(/approval reason/i)).toBeNull();
    expect(screen.queryByLabelText(/rejection reason/i)).toBeNull();
    expect(onApprove).not.toHaveBeenCalled();
    expect(onReject).not.toHaveBeenCalled();
    expect(screen.queryByTestId("approval-success")).toBeNull();
  });
});

describe("ApprovalPanel — an approval carries the approver's classification", () => {
  it("cannot be confirmed until a Cynefin domain is chosen, and DISORDER is not offered", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn(() => Promise.resolve());
    render(<ApprovalPanel item={makeItem()} onApprove={onApprove} onReject={vi.fn()} />);

    await user.click(screen.getByTestId("approve-button"));
    await user.type(screen.getByLabelText(/approval reason/i), REASON);
    // Nothing is pre-selected: a default that happens to pass Guardian would be
    // the UI classifying the situation instead of the person approving it.
    expect(screen.getByTestId("approval-domain")).toHaveValue("");
    expect(screen.getByTestId("confirm-approval")).toBeDisabled();
    const offered = Array.from(
      screen.getByTestId("approval-domain").querySelectorAll("option")
    ).map((o) => o.getAttribute("value"));
    expect(offered).not.toContain("DISORDER");

    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLEX");
    expect(screen.getByTestId("confirm-approval")).toBeEnabled();
    await user.click(screen.getByTestId("confirm-approval"));
    expect(onApprove).toHaveBeenCalledWith(makeItem().id, REASON, "COMPLEX");
  });
});

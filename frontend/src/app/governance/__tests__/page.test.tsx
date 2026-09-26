/**
 * Governance page tests — roadmap P0.6 "Stop the UI from faking approvals".
 *
 * Contract:
 *   - Approve/reject go to the real governance API (check → approve/reject).
 *   - Success UI appears only after the API call succeeds; on failure the
 *     error is shown and the item keeps its prior status.
 *   - The audit panel renders ONLY rows returned by GET /api/governance/audit-log.
 *   - After a decision, the review-queue and audit-log queries are refetched.
 *   - With NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true and the backend down, mock data
 *     is badged and approve/reject are disabled.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { governanceApiMock } = vi.hoisted(() => ({
  governanceApiMock: {
    reviewQueue: vi.fn(),
    auditLog: vi.fn(),
    check: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
  },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, governanceApi: governanceApiMock };
});

import GovernancePage from "../page";
import { CausalApiError, type AuditLogResponse } from "@/lib/api";
import type { AuditEntry, GuardianVerdict } from "@causalplatform/semantic-types";

// ─── Backend-shaped fixtures ────────────────────────────────────────────────────

const ITEM_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6";
const ITEM_NAME = "payment_latency → checkout_conversion";
const REASON = "Evidence and sensitivity analysis support this edge.";

// Shaped as SemanticLayerRegistry.list_pending_review_queue returns it: `domain`
// is the object's business domain (the seed writes "devops" and "marketing"),
// not a Cynefin classification.
const backendQueueItem = {
  id: ITEM_ID,
  object_type: "CausalEdge",
  domain: "devops",
  name: ITEM_NAME,
  approval_status: "DRAFT",
  created_at: "2026-09-18T10:00:00Z",
};

// AuditEntry serialises camelCase (alias_generator=_camel); the AuditLogResponse
// envelope around it is snake_case. Both exactly as the backend sends them.
function auditRow(
  id: string,
  actionType: string,
  verdict: GuardianVerdict = "APPROVED"
): AuditEntry {
  return {
    id,
    agentId: "governance-agent-api",
    actionType,
    payloadHash: `sha256-${id}`,
    guardianVerdict: verdict,
    policyRulesCited: [],
    sessionId: "api-check-dev-user-001",
    createdAt: "2026-09-18T10:05:00Z",
    subjectId: null,
    notes: null,
  };
}

function auditResponse(entries: AuditEntry[]): AuditLogResponse {
  return { entries, total: entries.length, page: 1, page_size: 100 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// ─── Helpers ────────────────────────────────────────────────────────────────────

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      // The page sets retry: 1 per query; retryDelay 0 keeps the retry instant.
      queries: { retry: false, retryDelay: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <GovernancePage />
    </QueryClientProvider>
  );
}

async function selectFirstItem(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId("first-review-item"));
}

async function openAuditPanel(user: ReturnType<typeof userEvent.setup>) {
  const panel = screen.getByTestId("audit-log-panel");
  await user.click(within(panel).getByRole("button", { name: /audit trail/i }));
  return panel;
}

function auditBodyRows(panel: HTMLElement) {
  return within(panel).queryAllByTestId("audit-entry");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_ENABLE_MOCK_FALLBACK", "false");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

// ─── Live backend ────────────────────────────────────────────────────────────────

describe("GovernancePage — approve calls the backend and waits for it", () => {
  it("shows success only after check + approve succeed, then refetches queue and audit log", async () => {
    const user = userEvent.setup();
    const approveCall = deferred<Record<string, string>>();

    governanceApiMock.reviewQueue
      .mockResolvedValueOnce([backendQueueItem])
      .mockResolvedValue([]); // after approval the item has left the queue
    governanceApiMock.auditLog
      .mockResolvedValueOnce(auditResponse([auditRow("a-1", "GUARDIAN_CHECK")]))
      .mockResolvedValue(
        auditResponse([
          auditRow("a-2", "GOVERNANCE_APPROVE"),
          auditRow("a-1", "GUARDIAN_CHECK"),
        ])
      );
    governanceApiMock.check.mockResolvedValue({
      verdict: "APPROVED",
      reason: "All policy checks passed.",
      policy_rules_cited: [],
      audit_entry_id: "a-2",
      session_id: null,
    });
    governanceApiMock.approve.mockReturnValue(approveCall.promise);

    renderPage();
    await selectFirstItem(user);
    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.type(screen.getByLabelText(/approval reason/i), REASON);
    await user.click(screen.getByTestId("confirm-approval"));

    await waitFor(() => {
      expect(governanceApiMock.approve).toHaveBeenCalledWith(
        ITEM_ID,
        "CausalEdge",
        "a-2",
        REASON
      );
    });
    // The verdict names the object it authorises and carries the approver's
    // classification: without them the real backend rejects (PR-006) and
    // /approve refuses a verdict bound to nothing.
    expect(governanceApiMock.check).toHaveBeenCalledWith({
      action_type: "GOVERNANCE_APPROVE",
      payload: { object_id: ITEM_ID, object_type: "CausalEdge", reason: REASON },
      subject_id: ITEM_ID,
      cynefin_domain: "COMPLICATED",
    });

    // Approve request still in flight → nothing claims success yet
    expect(screen.queryByTestId("approval-success")).toBeNull();
    expect(screen.queryByText(/recorded in the immutable audit trail/i)).toBeNull();

    approveCall.resolve({ status: "approved", object_id: ITEM_ID });

    expect(await screen.findByTestId("approval-success")).toHaveTextContent(
      /Approved successfully/
    );

    // Queue + audit log are refetched from the backend (invalidated), not patched
    await waitFor(() => {
      expect(governanceApiMock.reviewQueue).toHaveBeenCalledTimes(2);
      expect(governanceApiMock.auditLog).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(
        screen.getByText(/No items awaiting review/i)
      ).toBeInTheDocument();
    });

    // Audit panel shows exactly the backend rows — no synthesized entry
    const panel = await openAuditPanel(user);
    await waitFor(() => {
      expect(auditBodyRows(panel)).toHaveLength(2);
    });
    expect(within(panel).getByText("GOVERNANCE_APPROVE")).toBeInTheDocument();
    expect(within(panel).getByText("GUARDIAN_CHECK")).toBeInTheDocument();
    expect(within(panel).queryByText(new RegExp(REASON))).toBeNull();
    expect(within(panel).queryByText(/approve —/)).toBeNull();
  });
});

describe("GovernancePage — backend failure never marks the item approved", () => {
  it("approve API rejection: error shown, no success, item still Pending, no audit row added", async () => {
    const user = userEvent.setup();

    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockResolvedValue(
      auditResponse([auditRow("a-1", "GUARDIAN_CHECK")])
    );
    governanceApiMock.check.mockResolvedValue({
      verdict: "APPROVED",
      reason: "All policy checks passed.",
      policy_rules_cited: [],
      audit_entry_id: "a-2",
      session_id: null,
    });
    governanceApiMock.approve.mockRejectedValue(
      new CausalApiError(403, "HTTP_ERROR", "Insufficient role for this action")
    );

    renderPage();
    await selectFirstItem(user);
    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.type(screen.getByLabelText(/approval reason/i), REASON);
    await user.click(screen.getByTestId("confirm-approval"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Approval failed: Insufficient role/);
    expect(screen.queryByTestId("approval-success")).toBeNull();

    // Queue row keeps its backend status
    const row = screen.getByTestId("first-review-item");
    expect(within(row).getByText("Pending")).toBeInTheDocument();
    expect(within(row).queryByText("Approved")).toBeNull();

    // Audit panel: still only the backend's single row
    const panel = await openAuditPanel(user);
    expect(auditBodyRows(panel)).toHaveLength(1);
    expect(within(panel).queryByText(/approve —/)).toBeNull();
  });

  it("Guardian verdict other than APPROVED: approve endpoint is not called and error is shown", async () => {
    const user = userEvent.setup();

    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));
    governanceApiMock.check.mockResolvedValue({
      verdict: "REJECTED",
      reason: "DISORDER domain: domain must be classified before any approval.",
      policy_rules_cited: ["PR-006"],
      audit_entry_id: "a-9",
      session_id: null,
    });

    renderPage();
    await selectFirstItem(user);
    await user.click(screen.getByTestId("approve-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.click(screen.getByTestId("confirm-approval"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Guardian verdict was REJECTED/);
    expect(governanceApiMock.approve).not.toHaveBeenCalled();
    expect(screen.queryByTestId("approval-success")).toBeNull();
  });

  it("Guardian verdict other than APPROVED on a reject: reject endpoint is not called", async () => {
    const user = userEvent.setup();

    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));
    governanceApiMock.check.mockResolvedValue({
      verdict: "REJECTED",
      reason: "Guardian refused the reject request.",
      policy_rules_cited: ["PR-003"],
      audit_entry_id: "a-8",
      session_id: null,
    });

    renderPage();
    await selectFirstItem(user);
    await user.click(screen.getByTestId("reject-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLEX");
    await user.type(screen.getByLabelText(/rejection reason/i), REASON);
    await user.click(screen.getByTestId("confirm-rejection"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Rejection failed: Guardian verdict was REJECTED/);
    // A REJECTED verdict no longer authorises a rejection, so it is not sent.
    expect(governanceApiMock.reject).not.toHaveBeenCalled();
    expect(screen.queryByTestId("approval-success")).toBeNull();
  });

  it("reject API rejection: error shown and no success state", async () => {
    const user = userEvent.setup();

    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));
    governanceApiMock.check.mockResolvedValue({
      verdict: "APPROVED",
      reason: "All policy checks passed.",
      policy_rules_cited: [],
      audit_entry_id: "a-3",
      session_id: null,
    });
    governanceApiMock.reject.mockRejectedValue(
      new CausalApiError(0, "NETWORK_ERROR", "Network request failed. Check your connection.")
    );

    renderPage();
    await selectFirstItem(user);
    await user.click(screen.getByTestId("reject-button"));
    await user.selectOptions(screen.getByTestId("approval-domain"), "COMPLICATED");
    await user.type(screen.getByLabelText(/rejection reason/i), REASON);
    await user.click(screen.getByTestId("confirm-rejection"));

    const alert = await screen.findByTestId("approval-error");
    expect(alert).toHaveTextContent(/Rejection failed: Network request failed/);
    // The same shape as an approval's check: the object it authorises, and the
    // reviewer's classification -- rejecting needs an APPROVED verdict for
    // GOVERNANCE_REJECT (septa-53), which Guardian never gives in DISORDER.
    expect(governanceApiMock.check).toHaveBeenCalledWith({
      action_type: "GOVERNANCE_REJECT",
      payload: { object_id: ITEM_ID, object_type: "CausalEdge", reason: REASON },
      subject_id: ITEM_ID,
      cynefin_domain: "COMPLICATED",
    });
    expect(governanceApiMock.reject).toHaveBeenCalledWith(
      ITEM_ID,
      "CausalEdge",
      REASON,
      "a-3"
    );
    expect(screen.queryByTestId("approval-success")).toBeNull();
    const row = screen.getByTestId("first-review-item");
    expect(within(row).getByText("Pending")).toBeInTheDocument();
  });

  it("audit-log failure (no mock fallback): shows unavailable notice, renders no rows", async () => {
    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockRejectedValue(
      new CausalApiError(0, "NETWORK_ERROR", "Network request failed.")
    );

    renderPage();

    expect(await screen.findByTestId("audit-log-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-log-panel")).toBeNull();
    expect(screen.queryByTestId("mock-data-badge")).toBeNull();
  });
});

// ─── Mock fallback ──────────────────────────────────────────────────────────────

describe("GovernancePage — mock fallback mode", () => {
  it("badges mock data and disables approve/reject", async () => {
    vi.stubEnv("NEXT_PUBLIC_ENABLE_MOCK_FALLBACK", "true");
    const user = userEvent.setup();

    const down = new CausalApiError(0, "NETWORK_ERROR", "Network request failed.");
    governanceApiMock.reviewQueue.mockRejectedValue(down);
    governanceApiMock.auditLog.mockRejectedValue(down);

    renderPage();

    expect(await screen.findByTestId("mock-data-badge")).toBeInTheDocument();
    await selectFirstItem(user);

    expect(screen.getByTestId("approve-button")).toBeDisabled();
    expect(screen.getByTestId("reject-button")).toBeDisabled();
    expect(screen.getByTestId("decisions-disabled-notice")).toBeInTheDocument();

    await user.click(screen.getByTestId("approve-button"));
    expect(screen.queryByTestId("confirm-approval")).toBeNull();
    expect(governanceApiMock.check).not.toHaveBeenCalled();
    expect(governanceApiMock.approve).not.toHaveBeenCalled();
    expect(governanceApiMock.reject).not.toHaveBeenCalled();
    expect(screen.queryByTestId("approval-success")).toBeNull();
  });

  it("without the env flag, a backend outage shows no mock data", async () => {
    const down = new CausalApiError(0, "NETWORK_ERROR", "Network request failed.");
    governanceApiMock.reviewQueue.mockRejectedValue(down);
    governanceApiMock.auditLog.mockRejectedValue(down);

    renderPage();

    expect(await screen.findByText("Backend Unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("mock-data-badge")).toBeNull();
    expect(screen.queryByTestId("review-queue")).toBeNull();
    expect(screen.queryByTestId("audit-log-panel")).toBeNull();
  });
});

// ─── The queue renders what the registry sends ─────────────────────────────────
//
// Preconditions: GET /api/governance/review-queue returns rows shaped as
// governance.py ReviewQueueItem (id, object_type, domain, name,
// approval_status, created_at), with the business domains and object types
// SemanticLayerRegistry writes ("devops", "marketing"; "CausalEdge",
// "GraphFragment", "CausalEntity"), and created_at possibly null.
// Postconditions: the page renders every row without throwing; the business
// domain is shown as the text it is, never as a Cynefin classification; the
// type is labelled; the submitted time is the backend's, or a dash when it sent
// none. The audit log failing alone does not put the page in mock mode, and a
// 401/403 reads as "not authorised", not as an outage.
// Undefined, not asserted: the timestamp's locale format.

describe("GovernancePage — renders registry rows as the registry sends them", () => {
  it("renders business-domain rows (devops, marketing) without a Cynefin badge", async () => {
    governanceApiMock.reviewQueue.mockResolvedValue([
      backendQueueItem,
      {
        id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        object_type: "GraphFragment",
        domain: "marketing",
        name: "customer_churn_model",
        approval_status: "UNDER_REVIEW",
        created_at: null,
      },
    ]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));

    renderPage();

    const queue = await screen.findByTestId("review-queue");
    expect(within(queue).getByText(ITEM_NAME)).toBeInTheDocument();
    expect(within(queue).getByText("customer_churn_model")).toBeInTheDocument();
    expect(within(queue).getByText("devops")).toBeInTheDocument();
    expect(within(queue).getByText("marketing")).toBeInTheDocument();
    expect(within(queue).queryByTestId("cynefin-badge")).toBeNull();
    expect(within(queue).getByText("Causal Edge")).toBeInTheDocument();
    expect(within(queue).getByText("Graph Fragment")).toBeInTheDocument();
  });

  it("shows the backend's created_at, and a dash when it sent none", async () => {
    governanceApiMock.reviewQueue.mockResolvedValue([
      { ...backendQueueItem, created_at: null },
    ]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));

    renderPage();

    const row = await screen.findByTestId("first-review-item");
    const submittedAt = within(row).getByTestId("review-item-submitted-at");
    expect(submittedAt).toHaveTextContent(/^—$/);
  });

  it("does not claim a submitter or a risk tier the registry does not send", async () => {
    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));

    renderPage();

    const row = await screen.findByTestId("first-review-item");
    expect(within(row).queryByText("system")).toBeNull();
    expect(within(row).queryByText("Medium")).toBeNull();
  });

  it("stays live when only the audit log fails, even with the mock fallback enabled", async () => {
    vi.stubEnv("NEXT_PUBLIC_ENABLE_MOCK_FALLBACK", "true");
    const user = userEvent.setup();
    governanceApiMock.reviewQueue.mockResolvedValue([backendQueueItem]);
    governanceApiMock.auditLog.mockRejectedValue(
      new CausalApiError(0, "NETWORK_ERROR", "Network request failed.")
    );

    renderPage();

    expect(await screen.findByTestId("audit-log-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("mock-data-badge")).toBeNull();
    await selectFirstItem(user);
    expect(screen.queryByTestId("decisions-disabled-notice")).toBeNull();
    expect(screen.getByTestId("approve-button")).toBeEnabled();
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
  ])("a %i from the review queue reads as not authorised, not as an outage", async (status, code) => {
    governanceApiMock.reviewQueue.mockRejectedValue(
      new CausalApiError(status, code, "the caller may not read the review queue")
    );
    governanceApiMock.auditLog.mockResolvedValue(auditResponse([]));

    renderPage();

    expect(await screen.findByTestId("governance-not-authorised")).toHaveTextContent(
      /not authorised/i
    );
    expect(screen.queryByText("Backend Unavailable")).toBeNull();
  });
});

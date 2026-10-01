/**
 * Incidents page — intervention decisions are fail-closed (roadmap P0.6).
 *
 *   - The intervention's verdict changes ONLY after the interventions API
 *     confirms the decision (no optimistic flip).
 *   - On API failure the verdict stays as it was and the error is shown.
 *   - A stored intervention with no verdict is PENDING, never implied APPROVED.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { incidentsApiMock, interventionApiMock, sessionApiMock } = vi.hoisted(() => ({
  incidentsApiMock: { list: vi.fn(), get: vi.fn() },
  interventionApiMock: { approve: vi.fn(), reject: vi.fn(), decisions: vi.fn() },
  sessionApiMock: { run: vi.fn(), incident: vi.fn(), getState: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    incidentsApi: incidentsApiMock,
    interventionApi: interventionApiMock,
    sessionApi: sessionApiMock,
  };
});

import IncidentsPage from "../page";
import { CausalApiError, type IncidentRecord } from "@/lib/api";

const INCIDENT_ID = "INC-001";
const INTERVENTION_ID = "int-1-session-abc";

function makeRecord(intervention: Record<string, unknown>): IncidentRecord {
  return {
    id: INCIDENT_ID,
    title: "Checkout latency spike in payment-api",
    severity: "SEV2",
    affected_services: ["payment-api"],
    status: "ACTIVE",
    session_id: "session-abc",
    cynefin_domain: "COMPLICATED",
    domain_confidence: 0.8,
    overall_confidence: 0.7,
    guardian_verdict: "PENDING",
    draft_explanation: "",
    causal_hypotheses: [],
    proposed_interventions: [
      {
        id: INTERVENTION_ID,
        title: "Roll back payment-api to v2.3.0",
        description: "Revert the deployment.",
        confidence: 0.76,
        riskTier: "MEDIUM",
        ...intervention,
      },
    ],
    submitted_by: "dev-user-001",
    created_at: "2026-09-18T10:00:00Z",
    updated_at: "2026-09-18T10:00:00Z",
  };
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

async function cardVerdict() {
  const card = await screen.findByTestId("intervention-card");
  return within(card).getByTestId("guardian-verdict-badge").getAttribute("data-verdict");
}

beforeEach(() => {
  vi.clearAllMocks();
  interventionApiMock.decisions.mockResolvedValue([]);
});

describe("IncidentsPage — intervention decisions", () => {
  it("does not flip the verdict until the backend confirms the approval", async () => {
    const user = userEvent.setup();
    const approveCall = deferred<{
      intervention_id: string;
      decision: "APPROVED" | "REJECTED";
      audit_log_id: string;
      recorded_at: string;
      already_decided: boolean;
    }>();
    incidentsApiMock.list.mockResolvedValue([
      makeRecord({ guardianVerdict: "PENDING" }),
    ]);
    interventionApiMock.approve.mockReturnValue(approveCall.promise);

    render(<IncidentsPage />);
    expect(await cardVerdict()).toBe("PENDING");

    await user.click(screen.getByTestId("approve-button"));
    await user.type(screen.getByLabelText(/approval reason/i), "Safe rollback.");
    await user.click(screen.getByTestId("approve-button"));

    await waitFor(() => {
      expect(interventionApiMock.approve).toHaveBeenCalledWith(INTERVENTION_ID, {
        reason: "Safe rollback.",
        incident_id: INCIDENT_ID,
        intervention_title: "Roll back payment-api to v2.3.0",
      });
    });
    // Request in flight — still PENDING
    expect(await cardVerdict()).toBe("PENDING");

    approveCall.resolve({
      intervention_id: INTERVENTION_ID,
      decision: "APPROVED",
      audit_log_id: "audit-123",
      recorded_at: "2026-09-18T10:10:00Z",
      already_decided: false,
    });

    await waitFor(async () => {
      expect(await cardVerdict()).toBe("APPROVED");
    });
    expect(screen.queryByTestId("intervention-action-error")).toBeNull();
  });

  it("keeps the prior verdict and shows the error when the backend fails", async () => {
    const user = userEvent.setup();
    incidentsApiMock.list.mockResolvedValue([
      makeRecord({ guardianVerdict: "PENDING" }),
    ]);
    interventionApiMock.approve.mockRejectedValue(
      new CausalApiError(0, "NETWORK_ERROR", "Network request failed. Check your connection.")
    );

    render(<IncidentsPage />);
    expect(await cardVerdict()).toBe("PENDING");

    await user.click(screen.getByTestId("approve-button"));
    await user.type(screen.getByLabelText(/approval reason/i), "Safe rollback.");
    await user.click(screen.getByTestId("approve-button"));

    const alert = await screen.findByTestId("intervention-action-error");
    expect(alert).toHaveTextContent(/Network request failed/);
    expect(alert).toHaveTextContent(/NOT recorded/);
    expect(await cardVerdict()).toBe("PENDING");
  });

  it("shows the backend's earlier decision when the attempt conflicts with it", async () => {
    const user = userEvent.setup();
    incidentsApiMock.list.mockResolvedValue([
      makeRecord({ guardianVerdict: "PENDING" }),
    ]);
    interventionApiMock.reject.mockResolvedValue({
      intervention_id: INTERVENTION_ID,
      decision: "APPROVED",
      audit_log_id: "audit-earlier",
      recorded_at: "2026-09-18T09:00:00Z",
      already_decided: true,
    });

    render(<IncidentsPage />);
    await user.click(await screen.findByTestId("reject-button"));
    await user.type(screen.getByLabelText(/rejection reason/i), "Too risky.");
    await user.click(screen.getByTestId("reject-button"));

    const alert = await screen.findByTestId("intervention-action-error");
    expect(alert).toHaveTextContent(/already APPROVED/);
    expect(alert).toHaveTextContent(/NOT recorded/);
    expect(await cardVerdict()).toBe("APPROVED");
  });

  it("treats a stored intervention without a verdict as PENDING, not APPROVED", async () => {
    incidentsApiMock.list.mockResolvedValue([makeRecord({})]);

    render(<IncidentsPage />);

    expect(await cardVerdict()).toBe("PENDING");
  });
});

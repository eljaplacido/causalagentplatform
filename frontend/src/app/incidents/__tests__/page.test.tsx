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
    // save_incident stores the severity the page sent: IncidentSessionRequest's
    // P0 | P1 | P2 | P3 (backend/src/api/session.py).
    severity: "P1",
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

async function humanDecision() {
  const card = await screen.findByTestId("intervention-card");
  return within(card).queryByTestId("human-decision")?.getAttribute("data-decision") ?? null;
}

beforeEach(() => {
  vi.clearAllMocks();
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

    // The human decision is shown as such; the Guardian's verdict is unchanged.
    await waitFor(async () => {
      expect(await humanDecision()).toBe("APPROVED");
    });
    expect(await cardVerdict()).toBe("PENDING");
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
    expect(await humanDecision()).toBeNull();
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
    expect(await humanDecision()).toBe("APPROVED");
    expect(await cardVerdict()).toBe("PENDING");
  });

  it("treats a stored intervention without a verdict as PENDING, not APPROVED", async () => {
    incidentsApiMock.list.mockResolvedValue([makeRecord({})]);

    render(<IncidentsPage />);

    expect(await cardVerdict()).toBe("PENDING");
  });
});

// ─── The page renders what the backend stores, and invents nothing ─────────────
//
// Preconditions: GET /api/v1/incidents/ returns IncidentRecord rows
// (backend/src/api/incidents.py). A row written by the triage endpoint carries
// the severity the page sent (P0-P3) and interventions shaped by
// session.py _persist_incident: id, title, description, confidence, riskTier,
// guardianVerdict, rank -- no effect estimate. Rows written by
// backend/scripts/seed_data.py carry SEV1-SEV4. After a human decision the
// list endpoint adds humanDecision beside the Guardian's guardianVerdict.
// POST /api/v1/session/incident returns SessionStateResponse, whose
// recommendations carry no effect estimate either.
// Postconditions: every such row renders; severity is shown in one vocabulary
// (P0-P3); an intervention without an estimate says so and shows no interval;
// the Guardian verdict and the human decision are shown separately; a new
// incident's id does not collide with a listed one; a failed list load is
// said, and a 401/403 reads as not authorised.
// Undefined, not asserted: the exact random part of a generated id.

function sessionState(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "sess-new-1",
    agent_id: "incident-triage-agent",
    cynefin_domain: "COMPLICATED",
    domain_confidence: 0.74,
    guardian_verdict: "REQUIRES_ESCALATION",
    policy_rules_cited: ["PR-004"],
    overall_confidence: 0.61,
    requires_human_review: true,
    flags: [],
    draft_explanation: "Probable root cause in the payment-api deploy.",
    proposed_edge_count: 1,
    recommendation_count: 1,
    recommendations: [
      { rank: 1, action: "Roll back payment-api", rationale: "Deploy correlates with onset.",
        confidence: 0.7, risk_tier: "MEDIUM" },
    ],
    cycle_count: 1,
    timestamp: "2026-09-18T10:00:00+00:00",
    ...overrides,
  };
}

async function submitIncident(user: ReturnType<typeof userEvent.setup>, title: string) {
  await user.click(screen.getByTestId("submit-incident"));
  await user.type(screen.getByLabelText("Title"), title);
  await user.type(screen.getByLabelText("Affected Services"), "payment-api");
  await user.click(screen.getByRole("button", { name: "Submit Incident" }));
}

describe("IncidentsPage — renders stored incidents as the backend stores them", () => {
  it("renders a stored incident whose severity is P1, as the triage endpoint stores it", async () => {
    incidentsApiMock.list.mockResolvedValue([makeRecord({ guardianVerdict: "APPROVED" })]);

    render(<IncidentsPage />);

    expect(await screen.findByTestId("incident-severity")).toHaveTextContent("P1");
  });

  it("renders a seeded incident whose severity is SEV1 in the same P0-P3 vocabulary", async () => {
    incidentsApiMock.list.mockResolvedValue([
      { ...makeRecord({ guardianVerdict: "APPROVED" }), severity: "SEV1" },
    ]);

    render(<IncidentsPage />);

    expect(await screen.findByTestId("incident-severity")).toHaveTextContent("P0");
  });

  it("renders an unrecognised severity as sent rather than crashing", async () => {
    incidentsApiMock.list.mockResolvedValue([
      { ...makeRecord({ guardianVerdict: "APPROVED" }), severity: "urgent" },
    ]);

    render(<IncidentsPage />);

    expect(await screen.findByTestId("incident-severity")).toHaveTextContent("urgent");
  });

  it("says there is no effect estimate for a stored intervention, and shows no interval", async () => {
    incidentsApiMock.list.mockResolvedValue([makeRecord({ guardianVerdict: "APPROVED" })]);

    render(<IncidentsPage />);

    const card = await screen.findByTestId("intervention-card");
    expect(within(card).getByTestId("no-effect-estimate")).toHaveTextContent("No effect estimate");
    expect(within(card).queryByTestId("effect-estimate-bar")).toBeNull();
    expect(card.textContent).not.toMatch(/95% CI/);
  });

  it("shows the Guardian verdict and a stored human decision separately", async () => {
    incidentsApiMock.list.mockResolvedValue([
      makeRecord({ guardianVerdict: "REQUIRES_ESCALATION", humanDecision: "REJECTED" }),
    ]);

    render(<IncidentsPage />);

    expect(await cardVerdict()).toBe("REQUIRES_ESCALATION");
    expect(await humanDecision()).toBe("REJECTED");
  });

  it("shows the load failure instead of an empty feed", async () => {
    incidentsApiMock.list.mockRejectedValue(
      new CausalApiError(500, "HTTP_ERROR", "Request failed with status 500")
    );

    render(<IncidentsPage />);

    expect(await screen.findByTestId("incidents-load-error")).toHaveTextContent(/status 500/);
  });

  it("says not authorised when the list answers 401", async () => {
    incidentsApiMock.list.mockRejectedValue(
      new CausalApiError(401, "HTTP_ERROR", "Authentication required: Bearer token missing.")
    );

    render(<IncidentsPage />);

    expect(await screen.findByTestId("incidents-load-error")).toHaveTextContent(/not authorised/i);
  });
});

describe("IncidentsPage — a newly triaged incident", () => {
  it("shows no fabricated effect estimate for a recommendation", async () => {
    const user = userEvent.setup();
    incidentsApiMock.list.mockResolvedValue([]);
    sessionApiMock.incident.mockResolvedValue(sessionState());

    render(<IncidentsPage />);
    await submitIncident(user, "Checkout latency spike after deploy");

    const card = await screen.findByTestId("intervention-card");
    expect(within(card).getByTestId("no-effect-estimate")).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/95% CI/);
  });

  it("gets an id that does not collide with a listed incident, and a fresh one each time", async () => {
    const user = userEvent.setup();
    // One incident listed, stored as INC-002: the page used to name the next
    // one INC-00{length+1} = INC-002, overwriting it on save.
    incidentsApiMock.list.mockResolvedValue([
      { ...makeRecord({ guardianVerdict: "APPROVED" }), id: "INC-002" },
    ]);
    sessionApiMock.incident.mockResolvedValue(sessionState());

    render(<IncidentsPage />);
    await screen.findByTestId("intervention-card");
    await submitIncident(user, "Checkout latency spike after deploy");
    await waitFor(() => expect(sessionApiMock.incident).toHaveBeenCalledTimes(1));
    await submitIncident(user, "Second latency spike after deploy");
    await waitFor(() => expect(sessionApiMock.incident).toHaveBeenCalledTimes(2));

    const ids = sessionApiMock.incident.mock.calls.map(
      (c) => (c[0] as { incident_id: string }).incident_id
    );
    expect(ids).not.toContain("INC-002");
    expect(new Set(ids).size).toBe(2);
    for (const id of ids) expect(id).toMatch(/^INC-[0-9a-f]{8}$/);
  });

  it("sends the severity in the backend's P0-P3 vocabulary", async () => {
    const user = userEvent.setup();
    incidentsApiMock.list.mockResolvedValue([]);
    sessionApiMock.incident.mockResolvedValue(sessionState());

    render(<IncidentsPage />);
    await submitIncident(user, "Checkout latency spike after deploy");

    await waitFor(() => expect(sessionApiMock.incident).toHaveBeenCalled());
    const body = sessionApiMock.incident.mock.calls[0]![0] as { severity: string };
    expect(["P0", "P1", "P2", "P3"]).toContain(body.severity);
    expect(await screen.findByTestId("incident-severity")).toHaveTextContent(body.severity);
  });
});

// ─── Phone width (the tour, 2026-09-26) ───────────────────────────────────────
//
// At 390px the feed and the brief stayed side by side and the escalation
// banner and its button were clipped. jsdom does no layout, so this pins the
// responsive classes; the scrollWidth at 390px was measured in Chromium
// (390 on every page after the change).
describe("IncidentsPage — stacks below md", () => {
  it("stacks the feed above the brief below md, side by side from md", async () => {
    incidentsApiMock.list.mockResolvedValue([makeRecord({})]);
    render(<IncidentsPage />);
    await screen.findByTestId("incident-brief");

    const layout = screen.getByTestId("incidents-layout");
    expect(layout.className).toMatch(/(^|\s)flex-col(\s|$)/);
    expect(layout.className).toMatch(/(^|\s)md:flex-row(\s|$)/);
    const feed = screen.getByRole("complementary", { name: /incident feed/i });
    expect(feed.className).toMatch(/(^|\s)w-full(\s|$)/);
    expect(feed.className).toMatch(/(^|\s)md:w-64(\s|$)/);
  });
});

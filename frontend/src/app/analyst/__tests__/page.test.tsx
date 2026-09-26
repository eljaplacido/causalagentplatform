/**
 * Analyst workbench — shows what the session API returns, and nothing else.
 *
 * Preconditions: POST /api/v1/session/run returns SessionStateResponse
 * (backend/src/api/session.py): it reports how many edges the session proposed
 * (proposed_edge_count) and its recommendations, but not the proposed edges
 * themselves. GET /api/semantic/fragments may fail.
 *
 * Postconditions:
 *   - after a run, no causal graph is drawn from data the response does not
 *     carry: no invented "Root Cause" / "Observable Effect" nodes, no edge
 *     proposals, no node description attributed to an agent;
 *   - the proposed-edge count the backend sent is shown, with why no graph is
 *     drawn;
 *   - the recommendations are listed as the backend sent them;
 *   - a fragment-library failure is shown as a failure (not authorised for a
 *     401/403), never as "No fragments yet"; an empty library still says so.
 *
 * Undefined, not asserted: layout, and the wording around the count.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { sessionApiMock, semanticApiMock } = vi.hoisted(() => ({
  sessionApiMock: { run: vi.fn(), incident: vi.fn(), getState: vi.fn() },
  semanticApiMock: { fragments: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, sessionApi: sessionApiMock, semanticApi: semanticApiMock };
});

import AnalystPage from "../page";
import { CausalApiError, type SessionStateResponse } from "@/lib/api";

const SESSION: SessionStateResponse = {
  session_id: "3b241101-e2bb-4255-8caf-4136c566a962",
  agent_id: "causal-discovery-agent",
  cynefin_domain: "COMPLICATED",
  domain_confidence: 0.78,
  guardian_verdict: "APPROVED",
  policy_rules_cited: ["PR-001"],
  overall_confidence: 0.64,
  requires_human_review: false,
  flags: [],
  draft_explanation: "Latency rose after the payment-api deploy.",
  proposed_edge_count: 2,
  recommendation_count: 1,
  recommendations: [
    {
      rank: 1,
      action: "Roll back payment-api to v2.3.0",
      rationale: "The regression coincides with the v2.4 deploy.",
      confidence: 0.7,
      risk_tier: "MEDIUM",
    },
  ],
  cycle_count: 1,
  timestamp: "2026-09-18T10:00:00+00:00",
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnalystPage />
    </QueryClientProvider>
  );
}

async function runSession(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText("Problem description"),
    "High p99 latency on checkout after deployment"
  );
  await user.click(screen.getByTestId("run-session-button"));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AnalystPage — after a session run", () => {
  it("draws no graph the response does not carry, and shows the proposed-edge count", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValue(SESSION);

    renderPage();
    await runSession(user);

    const summary = await screen.findByTestId("proposed-edges-summary");
    expect(summary).toHaveTextContent("2");
    expect(screen.queryByTestId("dag-canvas")).toBeNull();
    expect(screen.queryByText("Root Cause")).toBeNull();
    expect(screen.queryByText("Observable Effect")).toBeNull();
    expect(screen.queryByText(/Root Cause Candidate/)).toBeNull();
    expect(screen.queryByText(/CausalDiscoveryAgent/)).toBeNull();
    expect(screen.queryAllByTestId("edge-proposal")).toHaveLength(0);
  });

  it("lists the recommendations as the backend sent them", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValue(SESSION);

    renderPage();
    await runSession(user);

    const rec = await screen.findByTestId("session-recommendation");
    expect(rec).toHaveTextContent("Roll back payment-api to v2.3.0");
    expect(rec).toHaveTextContent("The regression coincides with the v2.4 deploy.");
  });
});

describe("AnalystPage — fragment library", () => {
  it("shows a load failure as a failure, not as an empty library", async () => {
    semanticApiMock.fragments.mockRejectedValue(
      new CausalApiError(500, "HTTP_ERROR", "Request failed with status 500")
    );

    renderPage();

    expect(await screen.findByTestId("fragments-error")).toHaveTextContent(/status 500/);
    expect(screen.queryByText(/No fragments yet/)).toBeNull();
  });

  it("says not authorised on a 403", async () => {
    semanticApiMock.fragments.mockRejectedValue(
      new CausalApiError(403, "forbidden", "role viewer may not read fragments")
    );

    renderPage();

    expect(await screen.findByTestId("fragments-error")).toHaveTextContent(/not authorised/i);
  });

  it("still says the library is empty when it is", async () => {
    semanticApiMock.fragments.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText(/No fragments yet/)).toBeInTheDocument();
  });
});

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

// ─── What the tour found (2026-09-26) ─────────────────────────────────────────
//
// A run whose response said cynefin_domain COMPLICATED carried an explanation
// and a first recommendation written for CHAOTIC (the backend's escalation text
// named CHAOTIC whatever the reason -- fixed in the backend by dad4cfd). The
// header showed "Complicated" and the text said CHAOTIC, with nothing saying
// which was the classification. Both recommendations read "1." (the backend's
// ranks were 1 and 1), and a proposed edge was two bare UUIDs.
describe("AnalystPage — one classification, consistent numbering, labelled ids", () => {
  const MIXED: SessionStateResponse = {
    ...SESSION,
    cynefin_domain: "COMPLICATED",
    draft_explanation:
      "CHAOTIC domain: automated analysis suspended. Route this incident to your human incident commander now.",
    recommendations: [
      {
        rank: 1,
        action: "ESCALATE: Route to human decision-maker immediately.",
        rationale: "CHAOTIC domain detected. Human must: Stabilise → Act → Sense.",
        confidence: 1.0,
        risk_tier: "CRITICAL",
      },
      {
        rank: 1,
        action:
          "Propose edge: 7b06d720-d6d8-40fa-a89c-baafa9870425 → b2d176c5-82eb-4f27-8696-44ca294b8b2d (CAUSES)",
        rationale: "Statistical association detected in COMPLICATED domain context.",
        confidence: 0.38,
        risk_tier: "HIGH",
      },
    ],
    recommendation_count: 2,
  };

  it("shows the response's classification and flags backend text that names another domain", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValue(MIXED);

    renderPage();
    await runSession(user);

    const classification = await screen.findByTestId("session-classification");
    expect(classification).toHaveTextContent(/Classification/);
    expect(classification).toHaveTextContent("Complicated");
    const note = screen.getByTestId("domain-text-mismatch");
    expect(note).toHaveTextContent(/CHAOTIC/);
    expect(note).toHaveTextContent(/COMPLICATED/);
  });

  it("numbers the recommendations by their position in the list", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValue(MIXED);

    renderPage();
    await runSession(user);

    const recs = await screen.findAllByTestId("session-recommendation");
    expect(recs).toHaveLength(2);
    expect(recs[0]).toHaveTextContent(/^1\./);
    expect(recs[1]).toHaveTextContent(/^2\./);
  });

  it("shows a proposed edge's endpoints as short labelled ids, not bare UUIDs", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValue(MIXED);

    renderPage();
    await runSession(user);

    const recs = await screen.findAllByTestId("session-recommendation");
    expect(recs[1]).toHaveTextContent(/entity id 7b06d720…/);
    expect(recs[1]).toHaveTextContent(/entity id b2d176c5…/);
    expect(recs[1]).not.toHaveTextContent("7b06d720-d6d8-40fa-a89c-baafa9870425");
  });

  it("drops the previous run's classification while a new run is in flight", async () => {
    const user = userEvent.setup();
    semanticApiMock.fragments.mockResolvedValue([]);
    sessionApiMock.run.mockResolvedValueOnce(SESSION);
    renderPage();
    await runSession(user);
    await screen.findByTestId("session-classification");

    sessionApiMock.run.mockReturnValueOnce(new Promise(() => {}));
    await user.click(screen.getByTestId("run-session-button"));
    expect(screen.queryByTestId("session-classification")).toBeNull();
    expect(screen.queryAllByTestId("cynefin-badge")).toHaveLength(0);
  });
});

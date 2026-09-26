/**
 * DAG Workbench: authoring a fragment draft through the governed draft flow.
 *
 * Preconditions: rendered inside a QueryClientProvider; semanticApi and
 * governanceApi are the page's only way to the backend (mocked here with
 * backend-shaped responses: GraphFragment and CausalEdge camelCase, the draft
 * and queue envelopes snake_case).
 *
 * Postconditions (register dag-workbench; docs/UIX.md §4.2):
 *   - from one surface a user can open a fragment, pick its edges into a new
 *     fragment draft, classify the situation, and submit it;
 *   - the submission is POST /api/semantic/drafts with object_type
 *     GraphFragment, the picked edge ids, and the classification (a declared
 *     Cynefin domain, or a session id) -- never without one: submit stays
 *     disabled until the drafter classifies, and DISORDER is not offered;
 *   - the Guardian's verdict is shown only as the backend returned it: on 201
 *     the verdict, audit entry and domain judged in, and then whether the
 *     review queue (GET /api/governance/review-queue) lists the draft; on the
 *     403 the Guardian raises, its verdict, reason and rules cited, and no
 *     "saved" state;
 *   - any other failure shows the backend's message.
 *
 * Undefined here: the canvas layout, and what the backend decides.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { semanticApiMock, governanceApiMock } = vi.hoisted(() => ({
  semanticApiMock: {
    fragments: vi.fn(),
    fragment: vi.fn(),
    edge: vi.fn(),
    submitDraft: vi.fn(),
  },
  governanceApiMock: { reviewQueue: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, semanticApi: semanticApiMock, governanceApi: governanceApiMock };
});

import WorkbenchPage from "../page";
import {
  CausalApiError,
  type CausalEdgeResponse,
  type DraftSubmissionResponse,
  type GraphFragmentResponse,
} from "@/lib/api";

// ─── Backend-shaped fixtures ────────────────────────────────────────────────────

const EDGE_A = "0b7f7a52-5c1e-4a4e-9a0c-6f1f8b2d3e41";
const EDGE_B = "5d2c9e10-7a3b-4f6e-8c1d-2e4f6a8b0c93";
const DEPLOY = "9a1b2c3d-4e5f-4061-8a7b-8c9d0e1f2a3b";
const LATENCY = "1f2e3d4c-5b6a-4798-8a1b-2c3d4e5f6a7b";
const CHECKOUT = "7c6b5a49-3827-4165-9f0e-1d2c3b4a5968";

const FRAGMENT: GraphFragmentResponse = {
  id: "c0ffee00-1234-4abc-8def-0123456789ab",
  name: "deployment_latency_attribution",
  domain: "devops",
  description: "How a deployment moves latency and conversion.",
  edges: [EDGE_A, EDGE_B],
  approvalStatus: "APPROVED",
  version: "1.0.0",
  usageCount: 3,
  tags: ["latency"],
  guardianVerdictRef: "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  createdAt: "2026-09-10T08:00:00Z",
};

function edge(id: string, source: string, target: string, confidence: number): CausalEdgeResponse {
  return {
    id,
    source: { id: source, objectType: "CausalEntity" },
    target: { id: target, objectType: "Outcome" },
    edgeType: "CAUSES",
    direction: "FORWARD",
    confidence,
    evidenceRefs: [],
    assumptionRefs: [],
    approvalStatus: "APPROVED",
    approvedBy: { id: "gov-olu" },
    approvedAt: "2026-09-10T09:00:00Z",
    guardianVerdictRef: "8f14e45f-ceea-467a-9575-8b1f0d6a4c2e",
    version: "1.0.0",
    domain: "devops",
  };
}

const EDGES: Record<string, CausalEdgeResponse> = {
  [EDGE_A]: edge(EDGE_A, DEPLOY, LATENCY, 0.82),
  [EDGE_B]: edge(EDGE_B, LATENCY, CHECKOUT, 0.74),
};

const DRAFT_ID = "d4a1f0c2-6b8e-4c3a-9f7d-1e2b3c4d5e6f";
const AUDIT_ID = "a0d1e2f3-4a5b-4c6d-8e7f-9a0b1c2d3e4f";

const SAVED: DraftSubmissionResponse = {
  object_id: DRAFT_ID,
  object_type: "GraphFragment",
  guardian_verdict: "APPROVED",
  audit_entry_id: AUDIT_ID,
  cynefin_domain: "COMPLICATED",
  domain_source: "declared by dev-user-001",
  message: "GraphFragment draft submitted. Awaiting Governance Officer review.",
};

/** The error apiFetch throws for the Guardian's 403 (pinned in lib/__tests__/api.test.ts). */
function guardianBlock(detail: Record<string, unknown>): CausalApiError {
  return new CausalApiError(403, "guardian_blocked", String(detail["reason"]), detail);
}

// ─── Helpers ────────────────────────────────────────────────────────────────────

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkbenchPage />
    </QueryClientProvider>
  );
}

async function composeDraft(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /deployment_latency_attribution/ }));
  const composer = screen.getByTestId("fragment-draft-composer");
  // Edges load, then both are picked into the draft.
  const boxes = await within(composer).findAllByRole("checkbox");
  expect(boxes).toHaveLength(2);
  for (const b of boxes) await user.click(b);
  await user.type(within(composer).getByLabelText(/fragment name/i), "latency_to_conversion");
  return composer;
}

beforeEach(() => {
  vi.clearAllMocks();
  semanticApiMock.fragments.mockResolvedValue([FRAGMENT]);
  semanticApiMock.fragment.mockResolvedValue(FRAGMENT);
  semanticApiMock.edge.mockImplementation((id: string) => Promise.resolve(EDGES[id]));
  governanceApiMock.reviewQueue.mockResolvedValue([
    {
      id: DRAFT_ID,
      object_type: "GraphFragment",
      domain: "devops",
      name: "latency_to_conversion",
      approval_status: "DRAFT",
      created_at: "2026-09-26T10:00:00Z",
    },
  ]);
});

describe("workbench fragment authoring", () => {
  it("keeps submit disabled until the situation is classified, and offers no DISORDER", async () => {
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    const submit = within(composer).getByRole("button", { name: /submit draft for review/i });
    expect(submit).toBeDisabled();

    const domainSelect = within(composer).getByRole("combobox", { name: /cynefin domain/i });
    const options = within(domainSelect).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(options).not.toContain("DISORDER");
    expect((domainSelect as HTMLSelectElement).value).toBe("");

    await user.selectOptions(domainSelect, "COMPLICATED");
    expect(submit).toBeEnabled();
  });

  it("proposes the fragment, shows the Guardian's verdict, and confirms it is queued for approval", async () => {
    semanticApiMock.submitDraft.mockResolvedValue(SAVED);
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    expect(within(composer).getAllByTestId("draft-edge")).toHaveLength(2);

    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "COMPLICATED");
    expect(within(composer).queryByTestId("draft-saved")).toBeNull();
    await user.click(within(composer).getByRole("button", { name: /submit draft for review/i }));

    expect(semanticApiMock.submitDraft).toHaveBeenCalledTimes(1);
    const [objectType, payload, classification] = semanticApiMock.submitDraft.mock.calls[0]!;
    expect(objectType).toBe("GraphFragment");
    expect(payload).toMatchObject({
      name: "latency_to_conversion",
      domain: "devops",
      edges: [EDGE_A, EDGE_B],
    });
    expect(classification).toEqual({ cynefinDomain: "COMPLICATED" });

    const saved = await within(composer).findByTestId("draft-saved");
    expect(within(saved).getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "APPROVED");
    expect(saved).toHaveTextContent(AUDIT_ID);
    expect(saved).toHaveTextContent("Judged in COMPLICATED (declared by dev-user-001)");
    const queue = within(saved).getByTestId("draft-queue-status");
    expect(await within(queue).findByText(/in the governance review queue as/i)).toHaveTextContent("DRAFT");
    expect(within(queue).getByRole("link", { name: /governance page/i })).toHaveAttribute("href", "/governance");
    expect(governanceApiMock.reviewQueue).toHaveBeenCalled();
  });

  it("says so when the review queue does not list the saved draft", async () => {
    semanticApiMock.submitDraft.mockResolvedValue(SAVED);
    governanceApiMock.reviewQueue.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "CLEAR");
    await user.click(within(composer).getByRole("button", { name: /submit draft for review/i }));
    const queue = await within(composer).findByTestId("draft-queue-status");
    expect(await within(queue).findByText(/does not list this draft/i)).toBeInTheDocument();
    expect(within(queue).queryByText(/in the governance review queue as/i)).toBeNull();
  });

  it("shows the Guardian's block with its verdict, reason and rules, and nothing as saved", async () => {
    const reason = "PR-006: situation not classified (DISORDER); classify before proceeding";
    semanticApiMock.submitDraft.mockRejectedValue(
      guardianBlock({
        error: "guardian_blocked",
        verdict: "REJECTED",
        reason,
        policy_rules_cited: ["PR-006"],
        cynefin_domain: "DISORDER",
        domain_source: "not declared",
        audit_entry_id: AUDIT_ID,
      })
    );
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "COMPLICATED");
    await user.click(within(composer).getByRole("button", { name: /submit draft for review/i }));

    const blocked = await within(composer).findByTestId("draft-blocked");
    expect(blocked).toHaveAttribute("role", "alert");
    expect(within(blocked).getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "REJECTED");
    expect(within(blocked).getByTestId("draft-blocked-reason")).toHaveTextContent(reason);
    expect(blocked).toHaveTextContent("Rules cited: PR-006");
    expect(blocked).toHaveTextContent("Judged in DISORDER (not declared)");
    expect(blocked).toHaveTextContent(AUDIT_ID);
    expect(within(composer).queryByTestId("draft-saved")).toBeNull();
    expect(governanceApiMock.reviewQueue).not.toHaveBeenCalled();
  });

  it("shows a block from a backend that sends only verdict, reason and audit entry", async () => {
    semanticApiMock.submitDraft.mockRejectedValue(
      guardianBlock({
        error: "guardian_blocked",
        verdict: "REQUIRES_ESCALATION",
        reason: "PR-003: CHAOTIC domain requires human escalation",
        audit_entry_id: AUDIT_ID,
      })
    );
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "CHAOTIC");
    expect(composer).toHaveTextContent(/escalates to a human \(PR-003\)/);
    await user.click(within(composer).getByRole("button", { name: /submit draft for review/i }));
    const blocked = await within(composer).findByTestId("draft-blocked");
    expect(within(blocked).getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "REQUIRES_ESCALATION");
    expect(blocked).toHaveTextContent("PR-003: CHAOTIC domain requires human escalation");
  });

  it("sends a reasoning session instead of a declaration when the drafter chooses one", async () => {
    semanticApiMock.submitDraft.mockResolvedValue({ ...SAVED, domain_source: "session sess-42" });
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.click(within(composer).getByRole("radio", { name: /reasoning session/i }));
    const submit = within(composer).getByRole("button", { name: /submit draft for review/i });
    expect(submit).toBeDisabled();
    await user.type(within(composer).getByLabelText(/session id/i), "sess-42");
    await user.click(submit);
    expect(semanticApiMock.submitDraft.mock.calls[0]![2]).toEqual({ sessionId: "sess-42" });
    expect(await within(composer).findByTestId("draft-saved")).toHaveTextContent("session sess-42");
  });

  it("shows any other failure with the backend's message", async () => {
    semanticApiMock.submitDraft.mockRejectedValue(
      new CausalApiError(403, "HTTP_ERROR", "domain 'devops' is outside this user's domain scope")
    );
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "COMPLEX");
    await user.click(within(composer).getByRole("button", { name: /submit draft for review/i }));
    expect(await within(composer).findByTestId("draft-error")).toHaveTextContent(
      "domain 'devops' is outside this user's domain scope"
    );
    expect(within(composer).queryByTestId("draft-blocked")).toBeNull();
    expect(within(composer).queryByTestId("draft-saved")).toBeNull();
  });

  it("removes an edge from the draft, and needs at least one to submit", async () => {
    const user = userEvent.setup();
    renderPage();
    const composer = await composeDraft(user);
    await user.selectOptions(within(composer).getByRole("combobox", { name: /cynefin domain/i }), "CLEAR");
    for (const btn of within(composer).getAllByRole("button", { name: /remove .* from the draft/i })) {
      await user.click(btn);
    }
    expect(within(composer).queryAllByTestId("draft-edge")).toHaveLength(0);
    expect(within(composer).getByRole("button", { name: /submit draft for review/i })).toBeDisabled();
  });

  it("disables drafting when the fragment list cannot be loaded", async () => {
    semanticApiMock.fragments.mockRejectedValue(new CausalApiError(0, "NETWORK_ERROR", "Network request failed."));
    renderPage();
    const composer = await screen.findByTestId("fragment-draft-composer");
    expect(await within(composer).findByRole("note")).toHaveTextContent(/drafting is disabled/i);
    expect(within(composer).getByRole("button", { name: /submit draft for review/i })).toBeDisabled();
  });
});

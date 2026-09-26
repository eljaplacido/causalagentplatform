/**
 * Assumption registry view.
 *
 * Preconditions: rendered inside a QueryClientProvider; modelsApi is the only
 * way the page reaches the backend (mocked here; versions, diff and impact are
 * kernel-produced, src/test-fixtures/kernel.ts).
 *
 * Postconditions:
 *   - every registered model the backend lists is shown, with its unreviewed
 *     edges counted from the backend's list;
 *   - selecting a model shows its head version: the graph's edges with origin
 *     and review status, its assumptions and why it exists;
 *   - an older version is fetched from the backend when chosen;
 *   - a diff and an impact report are requested with the versions chosen, and
 *     shown as the backend returned them (the kernel's status and detail);
 *   - an unreviewed (proposed) edge is flagged in words;
 *   - a failed request shows the backend's reason; there is no mock registry.
 *
 * Undefined here: layout and styling.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { modelsApiMock } = vi.hoisted(() => ({
  modelsApiMock: {
    list: vi.fn(),
    get: vi.fn(),
    version: vi.fn(),
    diff: vi.fn(),
    impact: vi.fn(),
    verify: vi.fn(),
  },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, modelsApi: modelsApiMock };
});

import RegistryPage from "../page";
import { CausalApiError, type ImpactReportResponse, type ModelVersionResponse } from "@/lib/api";
import {
  DIFF_V1_V2,
  IMPACT_V1_TO_V2,
  MODEL_V1,
  MODEL_V2,
  RECORD_ACT,
} from "@/test-fixtures/kernel";

const NAME = MODEL_V2.name;

const IMPACT_REPORT: ImpactReportResponse = {
  model: NAME,
  to: { name: NAME, version: MODEL_V2.version, digest: MODEL_V2.digest },
  decisions: [
    {
      entry_digest: RECORD_ACT.entry_digest,
      model_version: 1,
      verdict: RECORD_ACT.decision.verdict,
      action: RECORD_ACT.decision.request.action,
      impact: IMPACT_V1_TO_V2,
    },
  ],
  by_status: { invalidated: 1 },
  note: "Each decision made on an older version, judged as if the target version were true.",
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RegistryPage />
    </QueryClientProvider>
  );
}

async function openModel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId("model-row"));
  return screen.findByTestId("model-version");
}

beforeEach(() => {
  vi.clearAllMocks();
  modelsApiMock.list.mockResolvedValue({
    models: [
      {
        name: NAME,
        version: MODEL_V2.version,
        digest: MODEL_V2.digest,
        author_id: MODEL_V2.author_id,
        created_at: MODEL_V2.created_at,
        unreviewed_edges: [],
      },
    ],
  });
  modelsApiMock.get.mockResolvedValue({
    head: MODEL_V2,
    versions: [
      { name: NAME, version: 1, digest: MODEL_V1.digest },
      { name: NAME, version: 2, digest: MODEL_V2.digest },
    ],
  });
  modelsApiMock.version.mockResolvedValue(MODEL_V1);
  modelsApiMock.diff.mockResolvedValue(DIFF_V1_V2);
  modelsApiMock.impact.mockResolvedValue(IMPACT_REPORT);
  modelsApiMock.verify.mockResolvedValue({ ok: true, length: 2, head_digest: MODEL_V2.digest, broken_at: null, reason: "" });
});

describe("assumption registry page", () => {
  it("lists the registered models", async () => {
    renderPage();
    const row = await screen.findByTestId("model-row");
    expect(row).toHaveTextContent(NAME);
    expect(row).toHaveTextContent("v2");
    expect(row).not.toHaveTextContent(/unreviewed/);
  });

  it("shows the head version's graph, assumptions and reason", async () => {
    const user = userEvent.setup();
    renderPage();
    const version = await openModel(user);
    expect(modelsApiMock.get).toHaveBeenCalledWith(NAME);
    const edges = within(version).getAllByTestId("model-edge");
    expect(edges).toHaveLength(MODEL_V2.graph.edges.length);
    expect(within(version).getByText("team -> playbook")).toBeInTheDocument();
    expect(within(version).getByText(MODEL_V2.why)).toBeInTheDocument();
    expect(within(version).getByText(MODEL_V2.assumptions[0]!.statement, { exact: false })).toBeInTheDocument();
    // Who introduced each edge is the backend's edge_authors.
    expect(within(version).getAllByText(MODEL_V2.author_id).length).toBeGreaterThan(0);
  });

  it("fetches an older version from the backend when chosen", async () => {
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.click(screen.getByRole("button", { name: "v1" }));
    expect(modelsApiMock.version).toHaveBeenCalledWith(NAME, 1);
    const version = await screen.findByLabelText(`${NAME} version 1`);
    expect(within(version).getAllByTestId("model-edge")).toHaveLength(MODEL_V1.graph.edges.length);
  });

  it("flags a proposed edge as unreviewed, in words", async () => {
    const proposed: ModelVersionResponse = {
      ...MODEL_V2,
      graph: {
        ...MODEL_V2.graph,
        edges: MODEL_V2.graph.edges.map((e) =>
          e.source === "team" && e.target === "minutes" ? { ...e, origin: "llm_prior", status: "proposed" } : e
        ),
      },
    };
    modelsApiMock.get.mockResolvedValue({ head: proposed, versions: [{ name: NAME, version: 2, digest: MODEL_V2.digest }] });
    const user = userEvent.setup();
    renderPage();
    const version = await openModel(user);
    expect(within(version).getByTestId("unreviewed-warning")).toHaveTextContent("1 edge is proposed and unreviewed");
    expect(within(version).getByText("(unreviewed)")).toBeInTheDocument();
  });

  it("requests the diff for the chosen versions and shows what changed", async () => {
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(modelsApiMock.diff).toHaveBeenCalledWith(NAME, 1, 2);
    const diff = await screen.findByTestId("model-diff");
    expect(within(diff).getByText("team -> minutes")).toBeInTheDocument();
    expect(within(diff).getByText("Added nodes:", { exact: false })).toBeInTheDocument();
    expect(within(diff).getByText(DIFF_V1_V2.assumptions_added[0]!.statement, { exact: false })).toBeInTheDocument();
  });

  it("says so when two versions do not differ", async () => {
    modelsApiMock.diff.mockResolvedValue({
      added_edges: [], removed_edges: [], changed_edges: [], added_nodes: [], removed_nodes: [],
      latent_added: [], latent_removed: [], assumptions_added: [], assumptions_removed: [],
    });
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.selectOptions(screen.getByRole("combobox", { name: "From" }), "2");
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(modelsApiMock.diff).toHaveBeenCalledWith(NAME, 2, 2);
    expect(await screen.findByTestId("model-diff-empty")).toBeInTheDocument();
  });

  it("shows the kernel's impact verdict for each recorded decision", async () => {
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.click(screen.getByRole("button", { name: "Check impact" }));
    expect(modelsApiMock.impact).toHaveBeenCalledWith(NAME, 2);
    const report = await screen.findByTestId("impact-report");
    const row = within(report).getByTestId("impact-row");
    expect(row).toHaveTextContent(RECORD_ACT.decision.request.action);
    expect(row).toHaveTextContent("invalidated");
    expect(row).toHaveTextContent("re-analyse");
    expect(row).toHaveTextContent(IMPACT_V1_TO_V2.detail);
    expect(within(report).getByTestId("impact-by-status")).toHaveTextContent("invalidated: 1");
  });

  it("says so when no decision rested on an earlier version", async () => {
    modelsApiMock.impact.mockResolvedValue({ ...IMPACT_REPORT, decisions: [], by_status: {} });
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.click(screen.getByRole("button", { name: "Check impact" }));
    expect(await screen.findByTestId("impact-empty")).toBeInTheDocument();
  });

  it("verifies the history only when asked", async () => {
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    expect(modelsApiMock.verify).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /verify the history/i }));
    expect(await screen.findByTestId("history-report")).toHaveTextContent("2 versions recompute");
  });

  it("shows the backend's reason when the registry cannot be read", async () => {
    modelsApiMock.list.mockRejectedValue(new CausalApiError(0, "NETWORK_ERROR", "Network request failed. Check your connection."));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Network request failed");
    expect(screen.queryByTestId("model-row")).toBeNull();
  });

  it("shows a model's 404 instead of a version", async () => {
    modelsApiMock.get.mockRejectedValue(new CausalApiError(404, "HTTP_ERROR", `no model ${NAME}`));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId("model-row"));
    expect(await screen.findByRole("alert")).toHaveTextContent(`no model ${NAME}`);
    expect(screen.queryByTestId("model-version")).toBeNull();
  });

  it("shows a failed impact request's reason", async () => {
    modelsApiMock.impact.mockRejectedValue(new CausalApiError(404, "HTTP_ERROR", `no model ${NAME} v2`));
    const user = userEvent.setup();
    renderPage();
    await openModel(user);
    await user.click(screen.getByRole("button", { name: "Check impact" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(`no model ${NAME} v2`);
    expect(screen.queryByTestId("impact-report")).toBeNull();
  });

  it("says so when no model is registered", async () => {
    modelsApiMock.list.mockResolvedValue({ models: [] });
    renderPage();
    expect(await screen.findByTestId("models-empty")).toBeInTheDocument();
  });
});

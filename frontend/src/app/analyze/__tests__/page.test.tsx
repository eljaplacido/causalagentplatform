/**
 * The analysis page, end to end against a mocked analysis client.
 *
 * Postconditions:
 *   - an example loads its data, question and graph, and the graph is checked
 *     with the data's column names (so unmeasured nodes are judged as the
 *     analysis judges them);
 *   - the identification verdict and adjustment set shown are the check's;
 *   - "Estimate the effect" sends the graph, question and data, and shows the
 *     kernel's estimate; nothing is recorded until "Decide and record", which
 *     sends the same inputs plus what the action is worth;
 *   - a user's CSV is parsed and the question is built from its columns, the
 *     graph from the confounders picked;
 *   - a check that says "not identified" shows why and what would help.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { analysisMock, brainMock, params } = vi.hoisted(() => ({
  brainMock: { suggestions: vi.fn(), data: vi.fn() },
  analysisMock: {
    examples: vi.fn(),
    example: vi.fn(),
    check: vi.fn(),
    run: vi.fn(),
    decide: vi.fn(),
  },
  params: { value: new URLSearchParams() },
}));

vi.mock("@/lib/analysis", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analysis")>();
  return { ...actual, analysisApi: analysisMock, brainApi: brainMock };
});

vi.mock("next/navigation", () => ({
  useSearchParams: () => params.value,
  usePathname: () => "/analyze",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import AnalyzeRoute from "../page";
import type { CausalResult, CheckResponse, ExampleData } from "@/lib/analysis";

const EXAMPLE: ExampleData = {
  id: "email-campaign",
  title: "Did the email campaign raise spend?",
  summary: "s",
  graph: "engagement -> emailed; engagement -> spend; emailed -> spend",
  treatment: "emailed",
  outcome: "spend",
  design: "observational",
  direction: "increase",
  action: "send the email to everyone",
  minimum_worthwhile_effect: 1,
  true_effect: 3,
  true_effect_note: "",
  lesson: "The raw difference is inflated.",
  n_rows: 3,
  column_names: ["engagement", "emailed", "spend"],
  columns: { engagement: [0.1, -0.2, 0.3], emailed: [1, 0, 1], spend: [25, 20, 27] },
};

function checkOk(adjust: string[]): CheckResponse {
  return {
    valid: true,
    error: null,
    graph: {
      nodes: ["engagement", "emailed", "spend"],
      edges: [
        { source: "engagement", target: "emailed", kind: "directed", origin: "human", status: "asserted" },
        { source: "emailed", target: "spend", kind: "directed", origin: "human", status: "asserted" },
      ],
      latent: [],
    },
    observed: ["emailed", "engagement", "spend"],
    latent: [],
    unmeasured: [],
    unused_columns: [],
    unreviewed_edges: [],
    testable_implications: [],
    identification: {
      identified: true,
      certificate: {
        status: "identified", strategy: "backdoor", tier: "A", estimand: "", adjustment_set: adjust,
        instruments: [], mediators: [], conditions: [], assumptions: [], general_id: null, unreviewed_edges: [],
      },
      refusal: null,
      missing_information: [],
    },
  };
}

const RESULT: CausalResult = {
  kind: "result",
  status: "supported",
  query: { treatment: "emailed", outcome: "spend", estimand: "ate", design: "observational" },
  certificate: checkOk(["engagement"]).identification!.certificate!,
  primary: { estimator: "aipw", family: "dr", estimand: "ATE", point: 3.21, se: 0.2,
    ci: { level: 0.95, lo: 2.8, hi: 3.6, method: "if" }, n_used: 3, assumptions: [] },
  estimates: [],
  diagnostics: { n_rows: 3, n_used: 3, n_dropped_missing: 0, treatment_kind: "binary", n_treated: 2,
    n_control: 1, outcome_sd: 3, propensity: null, overlap: null, balance: [], srm_p_value: null, checks: [] },
  refutations: [],
  sensitivity: null,
  triangulation: null,
  segments: [],
  warnings: [],
  result_digest: null,
};

beforeEach(() => {
  Object.values(analysisMock).forEach((m) => m.mockReset());
  Object.values(brainMock).forEach((m) => m.mockReset());
  params.value = new URLSearchParams();
  analysisMock.examples.mockResolvedValue([EXAMPLE]);
  analysisMock.example.mockResolvedValue(EXAMPLE);
  analysisMock.check.mockResolvedValue(checkOk(["engagement"]));
  analysisMock.run.mockResolvedValue({ outcome: RESULT, report: "Estimated effect..." });
});

describe("/analyze", () => {
  it("opens a second-brain hypothesis with its data, and records where it came from", async () => {
    const user = userEvent.setup();
    params.value = new URLSearchParams("suggestion=oracle:H-01");
    brainMock.suggestions.mockResolvedValue({
      enabled: true, project: "p", emit: true, sources: [],
      suggestions: [{
        id: "oracle:H-01", source: "oracle.hypothesis", kind: "experiment",
        title: "Route code tasks to the dense writer", why: "cheap to try", project: "agentcenter",
        detail: [], zone: null, rung: null, time: null,
        study: { graph: "arm -> outcome", treatment: "arm", outcome: "outcome", design: "randomized",
          estimand: "ate", action: "route code tasks", direction: "increase",
          minimum_worthwhile_effect: 0.05, arms: { "1": "dense", "0": "MoE" },
          outcome_label: "item passed" },
        data: { fetchable: true, builder: "bench_pair", note: "" },
      }],
    });
    const cols = { item: ["a", "a"], arm: [1, 0], outcome: [1, 0] };
    brainMock.data.mockResolvedValue({ suggestion_id: "oracle:H-01", columns: cols,
      sources: ["agentcenter:runs:T1 (1 items)"], note: "Both arms answered the same items." });
    analysisMock.check.mockResolvedValue({ ...checkOk([]), identification: { ...checkOk([]).identification!, certificate: { ...checkOk([]).identification!.certificate!, strategy: "randomized", adjustment_set: [] } } });
    analysisMock.decide.mockResolvedValue({
      verdict: "experiment", explanation: "x", decision: { experiment: null, what_would_change_it: [] },
      outcome: RESULT, record: { sequence: 1, entry_digest: "sha256:0123456789abcdef0123" }, narrative: null,
    });
    render(<AnalyzeRoute />);

    expect(await screen.findByText(/from your second brain: H-01/i)).toBeInTheDocument();
    expect(screen.getByText(/arm = 1: dense · arm = 0: MoE/)).toBeInTheDocument();
    expect(await screen.findByText(/both arms answered the same items/i)).toBeInTheDocument();
    expect(brainMock.data).toHaveBeenCalledWith("oracle:H-01");
    await waitFor(() =>
      expect(analysisMock.check).toHaveBeenLastCalledWith(
        expect.objectContaining({ graph: "arm -> outcome", treatment: "arm", outcome: "outcome", query: { design: "randomized" } })
      )
    );
    await user.click(await screen.findByRole("button", { name: /estimate the effect/i }));
    await user.click(await screen.findByRole("button", { name: /decide and record/i }));
    expect(analysisMock.decide).toHaveBeenCalledWith(
      expect.objectContaining({ origin: "oracle:H-01", action: "route code tasks", minimum_worthwhile_effect: 0.05, data: cols })
    );
  });

  it("runs an example from data to a recorded decision", async () => {
    const user = userEvent.setup();
    params.value = new URLSearchParams("example=email-campaign");
    analysisMock.decide.mockResolvedValue({
      verdict: "act",
      explanation: "ACT: the whole interval clears the worthwhile effect",
      decision: { experiment: null, what_would_change_it: [] },
      outcome: RESULT,
      record: { sequence: 0, entry_digest: "sha256:abcdef0123456789abcdef" },
      narrative: null,
    });
    render(<AnalyzeRoute />);

    expect(await screen.findByText(/the raw difference is inflated/i)).toBeInTheDocument();
    await waitFor(() => expect(analysisMock.check).toHaveBeenCalled());
    const checked = analysisMock.check.mock.calls.at(-1)![0];
    expect(checked).toMatchObject({ treatment: "emailed", outcome: "spend", query: { design: "observational" } });
    expect(checked.columns).toEqual(["engagement", "emailed", "spend"]);
    expect(await screen.findByText(/adjusting for engagement/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /estimate the effect/i }));
    expect(analysisMock.run).toHaveBeenCalledWith(
      expect.objectContaining({ treatment: "emailed", outcome: "spend", data: EXAMPLE.columns })
    );
    expect(await screen.findByText("+3.21")).toBeInTheDocument();
    expect(screen.getByText(/the interval contains it/i)).toBeInTheDocument();
    expect(analysisMock.decide).not.toHaveBeenCalled();

    expect(screen.getByLabelText(/action you'd take/i)).toHaveValue("send the email to everyone");
    await user.click(screen.getByRole("button", { name: /decide and record/i }));
    expect(analysisMock.decide).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "send the email to everyone",
        direction: "increase",
        minimum_worthwhile_effect: 1,
        data: EXAMPLE.columns,
      })
    );
    expect(await screen.findByText(/entry #0/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open in the decision ledger/i })).toHaveAttribute("href", "/decisions");
  });

  it("builds the question and graph from the user's own CSV", async () => {
    const user = userEvent.setup();
    render(<AnalyzeRoute />);
    await user.click(await screen.findByRole("button", { name: /paste csv text/i }));
    await user.type(screen.getByLabelText(/csv text/i), "promo,sales,season{enter}1,10,2{enter}0,8,1");
    await user.click(screen.getByRole("button", { name: /use this data/i }));

    await user.selectOptions(screen.getByRole("combobox", { name: /^treatment\b/i }), "promo");
    await user.selectOptions(screen.getByRole("combobox", { name: /^outcome\b/i }), "sales");
    // The first "season" chip is in the confounders group, the second in "what else affects".
    await user.click(screen.getAllByRole("button", { name: "season" })[0]!);

    await waitFor(() =>
      expect(analysisMock.check).toHaveBeenLastCalledWith(
        expect.objectContaining({
          graph: "season -> promo; season -> sales; promo -> sales",
          columns: ["promo", "sales", "season"],
        })
      )
    );
  });

  it("explains a graph that does not identify the effect", async () => {
    params.value = new URLSearchParams("example=email-campaign");
    analysisMock.check.mockResolvedValue({
      ...checkOk([]),
      identification: {
        identified: false,
        certificate: null,
        refusal: "not identified: FAIL: hedge",
        missing_information: ["randomize emailed (an experiment identifies the effect by design)"],
      },
    });
    render(<AnalyzeRoute />);
    expect(await screen.findByText(/not identified: not identified: FAIL: hedge/i)).toBeInTheDocument();
    expect(screen.getByText(/randomize emailed/)).toBeInTheDocument();
  });
});

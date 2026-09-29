/**
 * SecondBrainPanel shows what GET /api/v1/brain/suggestions returned: hypotheses
 * that open as analyses, next steps, improvement targets; which source is down;
 * and how to connect when the connector is off.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { brainMock } = vi.hoisted(() => ({ brainMock: { suggestions: vi.fn(), data: vi.fn() } }));
vi.mock("@/lib/analysis", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analysis")>();
  return { ...actual, brainApi: brainMock };
});

import { SecondBrainPanel } from "../SecondBrainPanel";
import type { Suggestion, SuggestionsResponse } from "@/lib/analysis";

const base: Omit<Suggestion, "id" | "kind" | "title" | "source"> = {
  why: "", project: "agentcenter", detail: [], zone: null, rung: null, study: null, data: null, time: null,
};

const RESPONSE: SuggestionsResponse = {
  enabled: true,
  project: "causalplatform",
  emit: true,
  sources: [
    { name: "oracle", url: "http://o", reachable: true, error: null, count: 2 },
    { name: "agentcenter", url: "http://a", reachable: false, error: "ConnectError", count: 0 },
  ],
  suggestions: [
    {
      ...base, id: "oracle:H-01", source: "oracle.hypothesis", kind: "experiment",
      title: "Route code tasks to the dense writer", zone: "probe first", why: "cheap to try",
      study: { graph: "arm -> outcome", treatment: "arm", outcome: "outcome", design: "randomized",
        estimand: "ate", action: "route", direction: "increase", minimum_worthwhile_effect: 0.05,
        arms: {}, outcome_label: null },
      data: { fetchable: true, builder: "bench_pair", note: "" },
    },
    { ...base, id: "review:x", source: "oracle.review", kind: "next_step", title: "Void the runs",
      detail: ["check", "void"], time: "10 min" },
    { ...base, id: "radar:1", source: "radar", kind: "improvement", title: "Integrate sealvera",
      project: "RCCAEF" },
  ],
};

beforeEach(() => {
  brainMock.suggestions.mockReset();
});

describe("SecondBrainPanel", () => {
  it("lists hypotheses that open as analyses, and which source is down", async () => {
    brainMock.suggestions.mockResolvedValue(RESPONSE);
    render(<SecondBrainPanel />);
    expect(await screen.findByText("Route code tasks to the dense writer")).toBeInTheDocument();
    expect(screen.getByText("probe first")).toBeInTheDocument();
    expect(screen.getByText("data available")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open as an analysis/i })).toHaveAttribute(
      "href",
      "/analyze?suggestion=oracle%3AH-01"
    );
    expect(screen.getByText(/agentcenter unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/decisions reported back/)).toBeInTheDocument();
  });

  it("switches to next steps and filters by project", async () => {
    const user = userEvent.setup();
    brainMock.suggestions.mockResolvedValue(RESPONSE);
    render(<SecondBrainPanel />);
    await user.click(await screen.findByRole("tab", { name: /next steps/i }));
    const step = screen.getByText("Void the runs").closest("li")!;
    expect(within(step).getByText("void")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /improvement targets/i }));
    expect(screen.getByText("Integrate sealvera")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: /project/i }), "agentcenter");
    expect(screen.queryByText("Integrate sealvera")).not.toBeInTheDocument();
  });

  it("says how to connect when the connector is off", async () => {
    brainMock.suggestions.mockResolvedValue({ ...RESPONSE, enabled: false, sources: [], suggestions: [] });
    render(<SecondBrainPanel />);
    expect(await screen.findByText(/not connected/i)).toBeInTheDocument();
    expect(screen.getAllByText("AGENTCENTER_URL").length).toBeGreaterThan(0);
  });
});

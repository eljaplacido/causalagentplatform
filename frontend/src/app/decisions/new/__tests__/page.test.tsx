/**
 * New decision: start a causal analysis from the UI.
 *
 * Preconditions: the page is rendered inside a QueryClientProvider;
 * decisionsApi.decide (POST /api/v1/decisions) and modelsApi.list
 * (GET /api/v1/models) are its only ways to the backend, mocked here with the
 * responses the real kernel produced for DECIDE_CSV (src/test-fixtures/decide.ts:
 * a structural model with a set effect of -9 minutes).
 *
 * Postconditions (decisions.py DecideRequest / DecideResponse):
 *   - the request carries only DecideRequest fields: the CSV's columns as parsed,
 *     exactly one of `graph` (inline) or `model` ({name, version?}), treatment and
 *     outcome from the columns, direction, the two thresholds, risk class and
 *     reversibility;
 *   - nothing is sent until the form names data, a graph, both columns and a direction;
 *   - the response is shown as returned: the verdict, the explanation verbatim, the
 *     estimate with its raw value (a rounded display beside it, never instead), and
 *     the ledger record, whose rationale's claim markers are filled from this
 *     outcome's claims;
 *   - a refusal is a result: its reason and missing_information are shown;
 *   - a request the backend rejects shows the backend's message, and no verdict.
 *
 * Undefined here: layout and styling.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { decisionsApiMock, modelsApiMock } = vi.hoisted(() => ({
  decisionsApiMock: { decide: vi.fn(), list: vi.fn(), verify: vi.fn(), calibration: vi.fn(), followup: vi.fn() },
  modelsApiMock: { list: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, decisionsApi: decisionsApiMock, modelsApi: modelsApiMock };
});

import NewDecisionPage from "../page";
import { CausalApiError } from "@/lib/api";
import { parseCsv } from "@/lib/csv";
import { DECIDE_ACT, DECIDE_CSV, DECIDE_MODEL, DECIDE_REFUSED, MODELS_LIST } from "@/test-fixtures/decide";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NewDecisionPage />
    </QueryClientProvider>
  );
}

const parsedCsv = (() => {
  const p = parseCsv(DECIDE_CSV);
  if ("error" in p) throw new Error(p.error);
  return p;
})();

type User = ReturnType<typeof userEvent.setup>;

/** Fill the question the fixtures were produced for, up to the graph. */
async function fillQuestion(user: User) {
  await user.type(screen.getByRole("textbox", { name: /action/i }), "adopt the incident playbook");
  const file = new File([DECIDE_CSV], "incidents.csv", { type: "text/csv" });
  await user.upload(screen.getByLabelText(/csv file/i), file);
  expect(await screen.findByTestId("data-summary")).toHaveTextContent("incidents.csv: 400 rows · columns load, playbook, minutes");
  await user.selectOptions(screen.getByRole("combobox", { name: /treatment/i }), "playbook");
  await user.selectOptions(screen.getByRole("combobox", { name: /^outcome$/i }), "minutes");
  await user.click(screen.getByRole("radio", { name: "decrease" }));
  const mwe = screen.getByRole("spinbutton", { name: /minimum worthwhile effect/i });
  await user.clear(mwe);
  await user.type(mwe, "2");
  const harm = screen.getByRole("spinbutton", { name: /harm threshold/i });
  await user.clear(harm);
  await user.type(harm, "0.5");
}

async function useInlineGraph(user: User, text: string) {
  await user.click(screen.getByRole("radio", { name: /type a graph/i }));
  await user.type(screen.getByRole("textbox", { name: /^graph/i }), text);
}

beforeEach(() => {
  vi.clearAllMocks();
  modelsApiMock.list.mockResolvedValue(MODELS_LIST);
});

describe("new decision page", () => {
  it("sends the CSV's columns and the question as DecideRequest fields, with an inline graph", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockResolvedValue(DECIDE_ACT);
    renderPage();
    await fillQuestion(user);
    await useInlineGraph(user, "load -> playbook; load -> minutes; playbook -> minutes");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));

    expect(decisionsApiMock.decide).toHaveBeenCalledTimes(1);
    expect(decisionsApiMock.decide).toHaveBeenCalledWith({
      action: "adopt the incident playbook",
      graph: "load -> playbook; load -> minutes; playbook -> minutes",
      treatment: "playbook",
      outcome: "minutes",
      direction: "decrease",
      data: parsedCsv.data,
      risk_class: "medium",
      reversible: true,
      minimum_worthwhile_effect: 2,
      harm_threshold: 0.5,
    });
  });

  it("shows the verdict, the explanation and the estimate exactly as returned", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockResolvedValue(DECIDE_ACT);
    renderPage();
    await fillQuestion(user);
    await useInlineGraph(user, "load -> playbook; load -> minutes; playbook -> minutes");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));

    const result = await screen.findByTestId("decide-result");
    expect(within(result).getByTestId("decide-verdict").textContent).toBe(DECIDE_ACT.verdict);
    expect(within(result).getByTestId("decide-explanation").textContent).toBe(DECIDE_ACT.explanation);

    // The raw point estimate, and its display-only rounding beside it.
    const point = within(result).getByTestId("outcome-point");
    expect(point.querySelector("[data-raw]")).toHaveTextContent(/^-9\.00921617223674$/);
    expect(point.querySelector("[data-rounded]")).toHaveTextContent("(≈ -9.009)");
    // The set effect (-9) lies inside the interval the kernel returned.
    const lo = Number(within(result).getByTestId("outcome-ci-lo").querySelector("[data-raw]")!.textContent);
    const hi = Number(within(result).getByTestId("outcome-ci-hi").querySelector("[data-raw]")!.textContent);
    expect(lo).toBeLessThan(-9);
    expect(hi).toBeGreaterThan(-9);

    // The kernel's claims, raw value beside the kernel's own text.
    const claimRows = within(result).getAllByTestId("claim-row");
    expect(claimRows[0]).toHaveTextContent("effect-9.00921617223674-9.009/primary/point");
  });

  it("fills the record's rationale from this outcome's claims", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockResolvedValue(DECIDE_ACT);
    renderPage();
    await fillQuestion(user);
    await useInlineGraph(user, "load -> playbook; load -> minutes; playbook -> minutes");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));

    const detail = await screen.findByTestId("decision-detail");
    const rationale = within(detail).getByTestId("decision-rationale");
    expect(within(rationale).queryAllByTestId("claim-placeholder")).toHaveLength(0);
    const filled = within(rationale).getAllByTestId("claim-filled");
    expect(filled.map((f) => f.getAttribute("data-claim"))).toEqual(["effect", "ci.level", "effect.ci.lo", "effect.ci.hi", "n"]);
    expect(filled[0]).toHaveTextContent("-9.009");
    expect(filled[0]).toHaveAttribute("title", "claim effect = -9.00921617223674 (/primary/point)");
    // The filled rationale is the explanation's first line after the verdict head.
    expect(`ACT: ${rationale.textContent}`).toBe(DECIDE_ACT.explanation.split("\n")[0]);
    // And the record is the one the ledger sealed.
    expect(detail).toHaveTextContent(DECIDE_ACT.record.entry_digest);
  });

  it("shows a refusal's reason and what is missing", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockResolvedValue(DECIDE_REFUSED);
    renderPage();
    await fillQuestion(user);
    await useInlineGraph(user, "latent U; U -> playbook; U -> minutes; load -> playbook; load -> minutes; playbook -> minutes");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));

    const refusal = await screen.findByTestId("outcome-refusal");
    expect(within(refusal).getByTestId("refusal-reason")).toHaveTextContent("NON_IDENTIFIABLE");
    const missing = within(refusal).getByTestId("missing-information");
    const items = within(missing).getAllByRole("listitem").map((li) => li.textContent);
    expect(DECIDE_REFUSED.outcome.kind).toBe("refusal");
    expect(items).toEqual(DECIDE_REFUSED.outcome.kind === "refusal" ? DECIDE_REFUSED.outcome.missing_information : []);
    expect(screen.getByTestId("decide-explanation").textContent).toBe(DECIDE_REFUSED.explanation);
    expect(screen.queryByTestId("outcome-point")).not.toBeInTheDocument();
  });

  it("names a registered model and version instead of sending a graph", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockResolvedValue(DECIDE_MODEL);
    renderPage();
    await fillQuestion(user);
    await user.selectOptions(await screen.findByRole("combobox", { name: /^model$/i }), "incident-playbook");
    await user.type(screen.getByRole("spinbutton", { name: /version/i }), "1");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));

    const body = decisionsApiMock.decide.mock.calls[0]![0];
    expect(body.model).toEqual({ name: "incident-playbook", version: 1 });
    expect(body).not.toHaveProperty("graph");
    const detail = await screen.findByTestId("decision-detail");
    expect(detail).toHaveTextContent("incident-playbook v1");
  });

  it("sends nothing until the form names the data, a graph, both columns and a direction", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));
    const errors = await screen.findByTestId("form-errors");
    expect(errors).toHaveTextContent("Give the data");
    expect(errors).toHaveTextContent("Choose a registered model");
    expect(errors).toHaveTextContent("Choose the treatment column");
    expect(errors).toHaveTextContent("Say which way the outcome should move");
    expect(decisionsApiMock.decide).not.toHaveBeenCalled();
  });

  it("refuses a malformed CSV with its reason", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.upload(screen.getByLabelText(/csv file/i), new File(["a,b\n1,2\n3\n"], "bad.csv", { type: "text/csv" }));
    expect(await screen.findByTestId("data-error")).toHaveTextContent("Row 3 has 1 field; the header has 2.");
    expect(screen.getByRole("combobox", { name: /treatment/i })).toBeDisabled();
  });

  it("shows the backend's message when it rejects the request, and no verdict", async () => {
    const user = userEvent.setup();
    decisionsApiMock.decide.mockRejectedValue(new CausalApiError(404, "HTTP_ERROR", "no model incident-playbook v7"));
    renderPage();
    await fillQuestion(user);
    await user.selectOptions(await screen.findByRole("combobox", { name: /^model$/i }), "incident-playbook");
    await user.type(screen.getByRole("spinbutton", { name: /version/i }), "7");
    await user.click(screen.getByRole("button", { name: /analyse and decide/i }));
    expect(await screen.findByTestId("decide-error")).toHaveTextContent("No decision was recorded: no model incident-playbook v7");
    expect(screen.queryByTestId("decide-result")).not.toBeInTheDocument();
  });
});

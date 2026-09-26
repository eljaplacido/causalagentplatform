/**
 * Decision ledger view.
 *
 * Preconditions: the page is rendered inside a QueryClientProvider; decisionsApi
 * is the only way it reaches the backend (mocked here with kernel-produced
 * responses, src/test-fixtures/kernel.ts).
 *
 * Postconditions (docs/UIX.md, AGENTS.md "numbers come from tools"):
 *   - every recorded decision the backend returns is listed, with its verdict;
 *   - a selected decision shows the backend's numbers in the kernel's own
 *     4-significant-digit format, with the exact value in the title, and an
 *     absent value as "—", never 0;
 *   - the rationale's claim markers are bound from the record's own evidence
 *     when every marker maps to a field it carries, else the text is labelled
 *     as a template;
 *   - a calibration report whose rates are null shows "—", never 0% or 100%;
 *   - a failed request shows the backend's reason; nothing is invented in its place;
 *   - a follow-up result appears only once the backend returns it, and a 409
 *     (already measured) is shown as the backend worded it;
 *   - a decision whose analysis refused offers no follow-up (the backend holds
 *     no plan for it);
 *   - chain verification shows the backend's ChainReport.
 *
 * Undefined here: layout, ordering beyond "newest sequence first", styling.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { decisionsApiMock } = vi.hoisted(() => ({
  decisionsApiMock: {
    list: vi.fn(),
    verify: vi.fn(),
    calibration: vi.fn(),
    followup: vi.fn(),
  },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, decisionsApi: decisionsApiMock };
});

import DecisionsPage from "../page";
import { CausalApiError } from "@/lib/api";
import {
  CALIBRATION_EMPTY,
  CALIBRATION_ONE,
  CALIBRATION_TWELVE,
  CHAIN_OK,
  REALIZATION,
  REALIZATION_EXPLANATION,
  RECORD_ACT,
  RECORD_REFUSED,
} from "@/test-fixtures/kernel";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DecisionsPage />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  decisionsApiMock.list.mockResolvedValue({ records: [RECORD_ACT, RECORD_REFUSED], total: 2 });
  decisionsApiMock.calibration.mockResolvedValue(CALIBRATION_EMPTY);
  decisionsApiMock.verify.mockResolvedValue(CHAIN_OK);
});

describe("decision ledger page", () => {
  it("lists every recorded decision with the policy's verdict", async () => {
    renderPage();
    const rows = await screen.findAllByTestId("decision-row");
    expect(rows).toHaveLength(2);
    // Newest sequence first.
    expect(rows[0]).toHaveTextContent("page the on-call lead first");
    expect(rows[0]).toHaveTextContent("experiment");
    expect(rows[1]).toHaveTextContent("adopt the incident playbook");
    expect(rows[1]).toHaveTextContent("act");
    expect(screen.getByTestId("records-total")).toHaveTextContent("2 returned");
  });

  it("shows a selected decision's numbers in the kernel's format, the exact value as the title", async () => {
    const user = userEvent.setup();
    renderPage();
    const rows = await screen.findAllByTestId("decision-row");
    await user.click(rows[1]!);

    const detail = screen.getByTestId("decision-detail");
    const ev = RECORD_ACT.decision.evidence;
    // 9.09615087806273 and [8.5148727454821, 9.67742901064336], in the kernel's
    // format (causal_core.claims.format_number); the exact value is the title.
    const benefit = within(detail).getByTestId("decision-benefit");
    expect(benefit).toHaveTextContent(/^9\.096$/);
    expect(benefit).toHaveAttribute("title", String(ev.benefit));
    expect(within(detail).getByTestId("decision-benefit-ci")).toHaveTextContent("[8.515, 9.677]");
    // Every check the policy ran is listed.
    for (const c of RECORD_ACT.decision.checks) {
      expect(within(detail).getByText(c.name)).toBeInTheDocument();
    }
    expect(within(detail).getByTestId("decision-rationale")).not.toHaveTextContent("⟦claim:");
    expect(within(detail).getByText(RECORD_ACT.entry_digest)).toBeInTheDocument();
  });

  it("offers no follow-up for a decision whose analysis refused", async () => {
    const user = userEvent.setup();
    renderPage();
    const rows = await screen.findAllByTestId("decision-row");
    await user.click(rows[0]!);
    const detail = screen.getByTestId("decision-detail");
    expect(within(detail).getByTestId("decision-refusal-reason")).toHaveTextContent("NON_IDENTIFIABLE");
    expect(within(detail).getByTestId("followup-unavailable")).toBeInTheDocument();
    expect(within(detail).queryByRole("button", { name: /measure the follow-up/i })).toBeNull();
  });

  // The report's rates are RateEstimate objects (causal_decision 0.5): counts
  // always, a rate and Clopper-Pearson interval only from min_followups (10)
  // up, and otherwise the reason none is stated. The panel used to render each
  // object as "[object Object]" (seen on a live backend, 2026-09-26).
  it("shows a rate stated below the minimum as a dash, with its counts and the reason", async () => {
    renderPage();
    const panel = await screen.findByTestId("calibration-panel");
    expect(await within(panel).findByTestId("calibration-empty")).toBeInTheDocument();
    for (const key of ["point_in_interval_rate", "interval_overlap_rate", "acted_and_worthwhile_rate", "acted_and_harmful_rate"]) {
      const cell = within(panel).getByTestId(`calibration-${key}`);
      expect(cell).toHaveTextContent("—");
      expect(cell).toHaveTextContent("0 of 0");
      expect(cell).toHaveTextContent(/states none below 10/);
      expect(cell).not.toHaveTextContent("%");
      expect(cell).not.toHaveTextContent("[object Object]");
    }
    expect(within(panel).getByTestId("calibration-n_decisions")).toHaveTextContent("0");
  });

  it("states no rate from one follow-up, only its counts", async () => {
    decisionsApiMock.calibration.mockResolvedValue(CALIBRATION_ONE);
    renderPage();
    const panel = await screen.findByTestId("calibration-panel");
    const cell = await within(panel).findByTestId("calibration-point_in_interval_rate");
    expect(cell).toHaveTextContent("—");
    expect(cell).toHaveTextContent("1 of 1");
    expect(within(panel).queryByTestId("calibration-empty")).toBeNull();
    expect(within(panel).getByText(CALIBRATION_ONE.note, { exact: false })).toBeInTheDocument();
  });

  it("shows a stated rate with its counts and interval, as the kernel computed them", async () => {
    // Known truth: 9 of the 12 follow-ups have the realized point inside the
    // predicted interval, so 0.75; [0.4281, 0.9451] is the 95% Clopper-Pearson
    // interval for 9/12, computed by causal_decision.outcome.report.
    decisionsApiMock.calibration.mockResolvedValue(CALIBRATION_TWELVE);
    renderPage();
    const panel = await screen.findByTestId("calibration-panel");
    const cell = await within(panel).findByTestId("calibration-point_in_interval_rate");
    expect(cell).toHaveTextContent("0.75");
    expect(cell).toHaveTextContent("9 of 12");
    expect(cell).toHaveTextContent("95% CI [0.4281, 0.9451]");
  });

  it("shows the backend's reason when the ledger cannot be read, and no rows", async () => {
    decisionsApiMock.list.mockRejectedValue(
      new CausalApiError(401, "HTTP_ERROR", "Authentication required")
    );
    renderPage();
    expect(await screen.findByTestId("records-error")).toHaveTextContent("Authentication required");
    expect(screen.queryAllByTestId("decision-row")).toHaveLength(0);
  });

  it("shows the calibration error instead of a report when it fails", async () => {
    decisionsApiMock.calibration.mockRejectedValue(new CausalApiError(500, "HTTP_ERROR", "database unavailable"));
    renderPage();
    const panel = await screen.findByTestId("calibration-panel");
    expect(await within(panel).findByRole("alert")).toHaveTextContent("database unavailable");
    expect(within(panel).queryByTestId("calibration-n_decisions")).toBeNull();
  });

  it("says so when no decision is recorded", async () => {
    decisionsApiMock.list.mockResolvedValue({ records: [], total: 0 });
    renderPage();
    expect(await screen.findByTestId("records-empty")).toBeInTheDocument();
  });

  it("sends the action filter and limit to the backend", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByTestId("decision-row");
    await user.type(screen.getByRole("searchbox"), "adopt the incident playbook");
    await user.selectOptions(screen.getByRole("combobox", { name: /limit/i }), "100");
    await user.click(screen.getByRole("button", { name: "Filter" }));
    expect(decisionsApiMock.list).toHaveBeenLastCalledWith({
      action: "adopt the incident playbook",
      limit: 100,
    });
  });

  it("records a follow-up and shows the realization the backend returned", async () => {
    decisionsApiMock.followup.mockResolvedValue({
      realization: REALIZATION,
      explanation: REALIZATION_EXPLANATION,
    });
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByTestId("decision-row"))[1]!);

    const rows = '{"load": [0.1, -0.3], "playbook": [1, 0], "minutes": [52.0, 61.5]}';
    const box = screen.getByLabelText(/rows after the decision/i);
    await user.click(box);
    await user.paste(rows);
    expect(screen.queryByTestId("followup-result")).toBeNull();
    await user.click(screen.getByRole("button", { name: /measure the follow-up/i }));

    expect(decisionsApiMock.followup).toHaveBeenCalledWith(RECORD_ACT.entry_digest, JSON.parse(rows));
    const result = await screen.findByTestId("followup-result");
    expect(result).toHaveTextContent(REALIZATION_EXPLANATION);
    // 8.579432204469548 in the kernel's format, exact value in the title.
    expect(within(result).getByTestId("followup-realized-benefit")).toHaveTextContent("8.579");
    expect(within(result).getByTestId("followup-realized-benefit")).toHaveAttribute(
      "title",
      String(REALIZATION.realized_benefit)
    );
    // A new realization changes the calibration report: it is re-read.
    expect(decisionsApiMock.calibration.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("shows a second follow-up's 409 as the backend worded it, and no result", async () => {
    const msg = `decision ${RECORD_ACT.entry_digest} has already been measured, and the first measurement stands`;
    decisionsApiMock.followup.mockRejectedValue(new CausalApiError(409, "HTTP_ERROR", msg));
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByTestId("decision-row"))[1]!);
    await user.click(screen.getByLabelText(/rows after the decision/i));
    await user.paste('{"minutes": [1]}');
    await user.click(screen.getByRole("button", { name: /measure the follow-up/i }));
    expect(await screen.findByTestId("followup-error")).toHaveTextContent(msg);
    expect(screen.queryByTestId("followup-result")).toBeNull();
  });

  it("refuses to send rows that are not columns, without calling the backend", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByTestId("decision-row"))[1]!);
    await user.click(screen.getByLabelText(/rows after the decision/i));
    await user.paste("[1, 2, 3]");
    await user.click(screen.getByRole("button", { name: /measure the follow-up/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/columns/);
    expect(decisionsApiMock.followup).not.toHaveBeenCalled();
  });

  it("verifies the chain only when asked, and shows the backend's report", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByTestId("decision-row");
    expect(decisionsApiMock.verify).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /verify the chain/i }));
    const report = await screen.findByTestId("chain-report");
    expect(report).toHaveTextContent(`${CHAIN_OK.length} entries recompute`);
    expect(report).toHaveTextContent(CHAIN_OK.head_digest!);
  });

  it("shows where a broken chain stops holding", async () => {
    decisionsApiMock.verify.mockResolvedValue({
      ok: false,
      length: 2,
      head_digest: null,
      broken_at: 1,
      reason: "the stored digest does not match the entry's content",
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByTestId("decision-row");
    await user.click(screen.getByRole("button", { name: /verify the chain/i }));
    expect(await screen.findByTestId("chain-report")).toHaveTextContent(
      "Broken at entry 1 of 2: the stored digest does not match the entry's content"
    );
  });
});

// ─── What the tour found (2026-09-26) ─────────────────────────────────────────
//
// The rationale showed its claim markers as bare tokens ("effect.ci.lo"), raw
// floats ran to 16 digits, and the checks table's headers ran together.
describe("decision detail — rationale, figures and checks", () => {
  it("binds the rationale's figures from the record's own evidence", async () => {
    const user = userEvent.setup();
    renderPage();
    const rows = await screen.findAllByTestId("decision-row");
    await user.click(rows[1]!);

    // Known truth: the fixture's structural model sets the playbook's effect
    // on minutes to -9; the kernel estimated -9.096 (95% CI -9.677 to -8.515,
    // n = 2000) and the decision layer stored it as a benefit of 9.096 in the
    // requested direction (decrease). The rationale reads the effect back.
    const rationale = screen.getByTestId("decision-rationale");
    expect(rationale).toHaveTextContent(
      "The estimate is -9.096 on minutes (95% CI -9.677 to -8.515, n = 2000, estimator aipw)"
    );
    expect(rationale).not.toHaveTextContent("effect.ci.lo");
    expect(screen.queryByTestId("rationale-template-label")).toBeNull();
  });

  it("labels the rationale as a template when the record lacks a figure it names", async () => {
    const user = userEvent.setup();
    const noInterval = {
      ...RECORD_ACT,
      decision: {
        ...RECORD_ACT.decision,
        evidence: { ...RECORD_ACT.decision.evidence, benefit_ci: null, ci_level: null },
      },
    };
    decisionsApiMock.list.mockResolvedValue({ records: [noInterval], total: 1 });
    renderPage();
    await user.click(await screen.findByTestId("decision-row"));

    expect(screen.getByTestId("rationale-template-label")).toHaveTextContent(/template/i);
    expect(screen.getByTestId("decision-rationale")).toHaveTextContent("effect.ci.lo");
    expect(screen.getByTestId("decision-benefit-ci")).toHaveTextContent("—");
  });

  it("gives the checks table spaced, labelled columns", async () => {
    const user = userEvent.setup();
    renderPage();
    const rows = await screen.findAllByTestId("decision-row");
    await user.click(rows[1]!);

    const table = screen.getByRole("table", { name: /checks/i });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Check", "Passed", "Allows", "Detail"]);
    for (const h of within(table).getAllByRole("columnheader")) {
      expect(h.className).toMatch(/\bp[xr]-\d/);
    }
  });
});

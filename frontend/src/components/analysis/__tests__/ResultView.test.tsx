/**
 * ResultView says what the kernel's outcome says, and no more: a refusal is
 * shown as an answer with what would help, an interval that misses the known
 * truth is said to miss it, and contested results are not called effects.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResultView } from "../ResultView";
import type { Abstention, CausalResult } from "@/lib/analysis";

function result(over: Partial<CausalResult> = {}): CausalResult {
  return {
    kind: "result",
    status: "supported",
    query: { treatment: "emailed", outcome: "spend", estimand: "ate", design: "observational" },
    certificate: {
      status: "identified", strategy: "backdoor", tier: "A", estimand: "E[..]",
      adjustment_set: ["engagement", "tenure"], instruments: [], mediators: [], conditions: [],
      assumptions: [], general_id: null, unreviewed_edges: [],
    },
    primary: { estimator: "aipw", family: "dr", estimand: "ATE", point: 3.29, se: 0.26,
      ci: { level: 0.95, lo: 2.77, hi: 3.8, method: "if" }, n_used: 2000, assumptions: [] },
    estimates: [],
    diagnostics: { n_rows: 2000, n_used: 2000, n_dropped_missing: 0, treatment_kind: "binary",
      n_treated: 1065, n_control: 935, outcome_sd: 6, propensity: null, overlap: null,
      balance: [], srm_p_value: null, checks: [] },
    refutations: [{ name: "placebo_treatment", passed: true, statistic: null, p_value: 0.4, threshold: null, detail: "" }],
    sensitivity: null, triangulation: null, segments: [], warnings: [], result_digest: null,
    ...over,
  };
}

describe("ResultView", () => {
  it("states the estimate, the interval, and whether the known truth is inside", () => {
    render(<ResultView outcome={result()} report="r" direction="increase" minimumWorthwhileEffect={1} trueEffect={3} />);
    expect(screen.getByText("+3.29")).toBeInTheDocument();
    expect(screen.getByText(/the data rule out no effect/i)).toBeInTheDocument();
    expect(screen.getByText(/cautious end clears the worthwhile threshold/i)).toBeInTheDocument();
    expect(screen.getByText(/the interval contains it/i)).toBeInTheDocument();
    expect(screen.getByText(/engagement, tenure/)).toBeInTheDocument();
  });

  it("says so when the interval misses the truth", () => {
    render(<ResultView outcome={result()} report="r" direction="increase" minimumWorthwhileEffect={null} trueEffect={5} />);
    expect(screen.getByText(/the interval misses it/i)).toBeInTheDocument();
  });

  it("does not call a contested result an effect", () => {
    render(<ResultView outcome={result({ status: "contested" })} report="r" direction="increase" minimumWorthwhileEffect={null} />);
    expect(screen.getByText(/estimated difference/i)).toBeInTheDocument();
    expect(screen.queryByText(/estimated effect/i)).not.toBeInTheDocument();
  });

  it("shows 0/1 outcomes in percentage points", () => {
    const r = result({ primary: { ...result().primary, point: 0.0238, ci: { level: 0.95, lo: 0.0068, hi: 0.0407, method: "x" } } });
    render(<ResultView outcome={r} report="r" direction="increase" minimumWorthwhileEffect={null} binaryOutcome />);
    expect(screen.getByText("+2.38 pp")).toBeInTheDocument();
  });

  it("renders a refusal as an answer, with what would make it answerable", () => {
    const refusal: Abstention = {
      kind: "refusal", reason: "NON_IDENTIFIABLE", detail: "hedge",
      missing_information: ["randomize ad_spend"], certificate: null, diagnostics: null,
    };
    render(<ResultView outcome={refusal} report="" direction="increase" minimumWorthwhileEffect={null} />);
    expect(screen.getByText(/declined to answer/i)).toBeInTheDocument();
    expect(screen.getByText("randomize ad_spend")).toBeInTheDocument();
    expect(screen.getByText(/correct result, not an error/i)).toBeInTheDocument();
  });
});

/**
 * formatFigure -- the kernel's own number format, so a figure reads the same in
 * the UI as in the kernel's rendered narratives.
 *
 * Known truth: every expected string below was printed by
 * causal_core.claims.format_number (packages/causal-core/src/causal_core/claims.py)
 * for the same input.
 */

import { describe, it, expect } from "vitest";
import { formatFigure, formatKernelNumber } from "../utils";

describe("formatKernelNumber matches causal_core.claims.format_number", () => {
  it.each<[number, string]>([
    [9.09615087806273, "9.096"],
    [-9.67742901064336, "-9.677"],
    [-8.5148727454821, "-8.515"],
    [0, "0"],
    [2000, "2000"],
    [95.0, "95"],
    [0.5, "0.5"],
    [12345.6, "1.235e+4"],
    [0.000012345, "1.234e-5"],
    [1.8347202061515093, "1.835"],
    [3.2751601360268907, "3.275"],
    [-0.001, "-0.001"],
    [1e20, "1e+20"],
    [0.1 + 0.2, "0.3"],
  ])("%s -> %s", (x, expected) => {
    expect(formatKernelNumber(x)).toBe(expected);
  });
});

describe("formatFigure", () => {
  it("shows an absent value as a dash, never as 0", () => {
    expect(formatFigure(null)).toBe("—");
    expect(formatFigure(undefined)).toBe("—");
  });
  it("formats an interval's ends and keeps a null end as a dash", () => {
    expect(formatFigure([1.6781453151172814, 1.991295097])).toBe("[1.678, 1.991]");
    expect(formatFigure([null, 2])).toBe("[—, 2]");
  });
  it("leaves text and flags as sent", () => {
    expect(formatFigure("aipw")).toBe("aipw");
    expect(formatFigure(true)).toBe("true");
  });
});

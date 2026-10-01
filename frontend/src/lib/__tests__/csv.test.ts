/**
 * CSV and JSON columns for POST /api/v1/decisions (src/lib/csv.ts).
 *
 * Preconditions: text as a file or textarea holds it.
 * Postconditions:
 *   - a header row names the columns; each row's cells land in their column, in order;
 *   - a plain number is that number, "true"/"false" a boolean, an empty cell null
 *     (missing, never 0), anything else the string as written;
 *   - quoted fields may hold commas, newlines and doubled quotes;
 *   - a malformed file is refused with a reason, never half-read.
 * Undefined: how whitespace around a string cell is kept.
 */

import { describe, expect, it } from "vitest";

import { parseCsv, parseJsonColumns, toCell } from "@/lib/csv";
import { DECIDE_ACT, DECIDE_CSV } from "@/test-fixtures/decide";

describe("parseCsv", () => {
  it("reads the simulated sample into the columns the kernel analysed", () => {
    const parsed = parseCsv(DECIDE_CSV);
    if ("error" in parsed) throw new Error(parsed.error);
    expect(parsed.columns).toEqual(["load", "playbook", "minutes"]);
    expect(parsed.rows).toBe(400);
    // The kernel saw 400 rows of these columns (its own diagnostics say so).
    expect(DECIDE_ACT.outcome.kind === "result" && DECIDE_ACT.outcome.diagnostics.n_rows).toBe(400);
    const firstLine = DECIDE_CSV.split("\n")[1]!.split(",");
    expect(parsed.data["load"]![0]).toBe(Number(firstLine[0]));
    expect(parsed.data["playbook"]!.every((v) => v === 0 || v === 1)).toBe(true);
    expect(parsed.data["minutes"]!.every((v) => typeof v === "number")).toBe(true);
  });

  it("types each cell as the backend's Cell, and keeps an empty cell missing", () => {
    const parsed = parseCsv("a,b,c,d\n1.5,,true,north\n-2,3e-2,FALSE,\"south, east\"\n");
    if ("error" in parsed) throw new Error(parsed.error);
    expect(parsed.data).toEqual({
      a: [1.5, -2],
      b: [null, 0.03],
      c: [true, false],
      d: ["north", "south, east"],
    });
  });

  it("reads quoted fields with newlines and doubled quotes, and CRLF line ends", () => {
    const parsed = parseCsv('name,note\r\n"x","said ""hi""\r\nthen left"\r\n');
    if ("error" in parsed) throw new Error(parsed.error);
    expect(parsed.data["note"]).toEqual(['said "hi"\r\nthen left']);
    expect(parsed.rows).toBe(1);
  });

  it("refuses a malformed file with the reason", () => {
    expect(parseCsv("")).toEqual({ error: "The file is empty." });
    expect(parseCsv("a,b\n")).toEqual({ error: "The file has a header but no rows." });
    expect(parseCsv("a,a\n1,2")).toEqual({ error: 'The column "a" is named twice.' });
    expect(parseCsv("a,,c\n1,2,3")).toEqual({ error: "Every column needs a name in the header row." });
    expect(parseCsv("a,b\n1,2\n3")).toEqual({ error: "Row 3 has 1 field; the header has 2." });
    expect(parseCsv('a\n"open')).toEqual({ error: "A quoted field is never closed." });
  });

  it("does not read a number-like string with other characters as a number", () => {
    expect(toCell("12kg")).toBe("12kg");
    expect(toCell("0x10")).toBe("0x10");
    expect(toCell(" 7 ")).toBe(7);
  });
});

describe("parseJsonColumns", () => {
  it("accepts equally long lists of cells", () => {
    const parsed = parseJsonColumns('{"t": [0, 1], "y": [1.5, null]}');
    expect(parsed).toEqual({ columns: ["t", "y"], data: { t: [0, 1], y: [1.5, null] }, rows: 2 });
  });

  it("refuses what the backend would refuse", () => {
    expect(parseJsonColumns("{")).toEqual({ error: "The columns are not valid JSON." });
    expect(parseJsonColumns("[1]")).toHaveProperty("error");
    expect(parseJsonColumns("{}")).toEqual({ error: "The data names no column." });
    expect(parseJsonColumns('{"t": 1}')).toEqual({ error: 'Column "t" is not a list.' });
    expect(parseJsonColumns('{"t": [1, 2], "y": [1]}')).toEqual({
      error: 'Column "y" has 1 values; the first column has 2.',
    });
    expect(parseJsonColumns('{"t": [{"a": 1}]}')).toHaveProperty("error");
  });
});

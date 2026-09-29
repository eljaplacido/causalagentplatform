/**
 * The analysis page's CSV reader and graph builder: what the user's file
 * becomes is what the kernel analyses, so the parsing rules are pinned here.
 */

import { describe, expect, it } from "vitest";
import { fmt, graphFromRoles, parseCsv, profileColumns, splitCsv } from "../analysis";

describe("parseCsv", () => {
  it("types numeric columns as numbers and keeps text as text", () => {
    const t = parseCsv("treated,spend,region\n1,3.5,north\n0,2,south\n");
    expect(t.nRows).toBe(2);
    expect(t.columns).toEqual({ treated: [1, 0], spend: [3.5, 2], region: ["north", "south"] });
  });

  it("reads missing markers as null without turning the column into text", () => {
    const t = parseCsv("x,y\n1,NA\n2,\n3,4");
    expect(t.columns.y).toEqual([null, null, 4]);
  });

  it("maps true/false columns to 1/0", () => {
    expect(parseCsv("t,y\ntrue,1\nFALSE,2").columns.t).toEqual([1, 0]);
  });

  it("handles quotes, embedded delimiters, CRLF and a BOM", () => {
    const t = parseCsv('﻿name,score\r\n"Smith, J.",1\r\n"say ""hi""",2\r\n');
    expect(t.columns.name).toEqual(["Smith, J.", 'say "hi"']);
    expect(t.columns.score).toEqual([1, 2]);
  });

  it("detects semicolon and tab delimiters", () => {
    expect(parseCsv("a;b\n1;2").names).toEqual(["a", "b"]);
    expect(parseCsv("a\tb\n1\t2").names).toEqual(["a", "b"]);
  });

  it("refuses ragged rows, duplicate headers and header-only files", () => {
    expect(() => parseCsv("a,b\n1,2,3")).toThrow(/row 2 has 3 fields/);
    expect(() => parseCsv("a,a\n1,2")).toThrow(/duplicate/);
    expect(() => parseCsv("a,b\n")).toThrow(/at least one data row/);
  });

  it("splits a quoted newline inside one field", () => {
    expect(splitCsv('a,b\n"x\ny",2')).toEqual([["a", "b"], ["x\ny", "2"]]);
  });
});

describe("profileColumns", () => {
  it("tells binary from numeric from text and counts missing", () => {
    const p = profileColumns({ t: [0, 1, null], y: [1.5, 2, 3], g: ["a", "b", "a"] });
    expect(p.map((c) => [c.name, c.kind, c.missing])).toEqual([
      ["t", "binary", 1],
      ["y", "numeric", 0],
      ["g", "text", 0],
    ]);
  });
});

describe("graphFromRoles", () => {
  it("makes each confounder a cause of both, and the treatment a cause of the outcome", () => {
    expect(graphFromRoles("T", "Y", ["Z"], ["W"])).toBe("Z -> T; Z -> Y; W -> Y; T -> Y");
  });
});

describe("fmt", () => {
  it("rounds to significant figures and marks absent values", () => {
    expect(fmt(3.28549)).toBe("3.29");
    expect(fmt(-0.0006676)).toBe("-0.000668");
    expect(fmt(null)).toBe("—");
  });
});

/**
 * CSV → columns, in the browser, for POST /api/v1/decisions (`data`: columns as lists).
 *
 * RFC 4180 as far as a data upload needs it: a header row, comma-separated
 * fields, double-quoted fields that may hold commas, newlines and doubled
 * quotes (""), and CRLF or LF line ends. Each cell is read as the backend's
 * Cell type (float | int | str | bool | None):
 *
 *   - an empty cell is null (missing), never 0;
 *   - a cell that is a plain decimal number ("3", "-0.25", "1e-3") is that number;
 *   - "true" / "false" (any case) is a boolean;
 *   - anything else is kept as the string it is.
 *
 * Nothing is rounded, rescaled or imputed: what the analysis sees is what the
 * file says. A malformed file is refused with the reason, not half-read.
 */

import type { DecideCell } from "@/lib/api";

export interface ParsedColumns {
  columns: string[];
  data: Record<string, DecideCell[]>;
  rows: number;
}

const NUMBER = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/;

/** One CSV cell as the backend's Cell type. */
export function toCell(raw: string): DecideCell {
  const s = raw.trim();
  if (s === "") return null;
  if (NUMBER.test(s)) return Number(s);
  const lower = s.toLowerCase();
  if (lower === "true") return true;
  if (lower === "false") return false;
  return raw;
}

/** Split CSV text into rows of raw fields; throws on an unterminated quote. */
function records(text: string): string[][] {
  const out: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = 0;
  const src = text.startsWith("﻿") ? text.slice(1) : text;
  while (i < src.length) {
    const ch = src[i]!;
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"' && field === "") {
      quoted = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      row.push(field);
      out.push(row);
      row = [];
      field = "";
      if (ch === "\r" && src[i + 1] === "\n") i += 1;
    } else {
      field += ch;
    }
    i += 1;
  }
  if (quoted) throw new Error("A quoted field is never closed.");
  if (field !== "" || row.length > 0) {
    row.push(field);
    out.push(row);
  }
  // Blank lines (a trailing newline, spacing) carry no row.
  return out.filter((r) => !(r.length === 1 && r[0]!.trim() === ""));
}

/** Parse a CSV file's text into named columns, or say why it cannot be used. */
export function parseCsv(text: string): ParsedColumns | { error: string } {
  let rows: string[][];
  try {
    rows = records(text);
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  if (rows.length === 0) return { error: "The file is empty." };
  const header = rows[0]!.map((h) => h.trim());
  if (header.some((h) => h === "")) return { error: "Every column needs a name in the header row." };
  const seen = new Set<string>();
  for (const h of header) {
    if (seen.has(h)) return { error: `The column "${h}" is named twice.` };
    seen.add(h);
  }
  const body = rows.slice(1);
  if (body.length === 0) return { error: "The file has a header but no rows." };
  const data: Record<string, DecideCell[]> = Object.fromEntries(header.map((h) => [h, []]));
  for (let r = 0; r < body.length; r++) {
    const fields = body[r]!;
    if (fields.length !== header.length) {
      return {
        error: `Row ${r + 2} has ${fields.length} field${fields.length === 1 ? "" : "s"}; the header has ${header.length}.`,
      };
    }
    header.forEach((h, c) => data[h]!.push(toCell(fields[c]!)));
  }
  return { columns: header, data, rows: body.length };
}

/**
 * Pasted JSON columns ({"column": [values, ...], ...}), checked for the shape the
 * backend accepts: an object of equally long lists of cells.
 */
export function parseJsonColumns(text: string): ParsedColumns | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "The columns are not valid JSON." };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { error: 'Give the data as columns: {"column": [values, ...], ...}.' };
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length === 0) return { error: "The data names no column." };
  let length: number | null = null;
  for (const [name, col] of entries) {
    if (!Array.isArray(col)) return { error: `Column "${name}" is not a list.` };
    for (const v of col) {
      if (v !== null && !["number", "string", "boolean"].includes(typeof v)) {
        return { error: `Column "${name}" holds a value that is not a number, string, boolean or null.` };
      }
    }
    if (length !== null && col.length !== length) {
      return { error: `Column "${name}" has ${col.length} values; the first column has ${length}.` };
    }
    length = col.length;
  }
  return {
    columns: entries.map(([name]) => name),
    data: parsed as Record<string, DecideCell[]>,
    rows: length ?? 0,
  };
}

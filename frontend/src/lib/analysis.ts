/**
 * The analysis page's client: /api/v1/analysis (check, run, examples) and
 * POST /api/v1/decisions, plus a CSV reader for the user's own data.
 *
 * Types mirror causal_core.contract (CausalResult, Abstention,
 * ApparatusFailure) for the fields the page renders; the contract itself is
 * the published JSON Schema in conformance/causal-claims/.
 */

import {
  api,
  type DecisionRecordResponse,
  type DecisionResponse,
} from "@/lib/api";

export type Cell = number | string | boolean | null;
export type Columns = Record<string, Cell[]>;

// ── causal_core.contract (rendered subset) ───────────────────────────────────

export interface Interval {
  level: number;
  lo: number;
  hi: number;
  method: string;
}

export interface EstimateRecord {
  estimator: string;
  family: string;
  estimand: string;
  point: number;
  se: number | null;
  ci: Interval | null;
  ci_null_reason?: string | null;
  n_used: number;
  assumptions: string[];
}

export interface ConditionCheck {
  name: string;
  holds: boolean;
  detail: string;
}

export interface IdentificationCertificate {
  status: "identified" | "not_identified" | string;
  strategy: string | null;
  tier: string;
  estimand: string;
  adjustment_set: string[];
  instruments: string[];
  mediators: string[];
  conditions: ConditionCheck[];
  assumptions: string[];
  general_id: string | null;
  unreviewed_edges: string[];
}

export interface RefutationResult {
  name: string;
  passed: boolean;
  statistic: number | null;
  p_value: number | null;
  threshold: number | null;
  detail: string;
}

export interface BalanceRow {
  covariate: string;
  smd_raw: number;
  smd_weighted: number | null;
}

export interface DesignCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Diagnostics {
  n_rows: number;
  n_used: number;
  n_dropped_missing: number;
  treatment_kind: "binary" | "continuous";
  n_treated: number | null;
  n_control: number | null;
  outcome_sd: number | null;
  propensity: {
    min: number;
    max: number;
    share_outside_bounds: number;
    ess_treated: number;
    ess_control: number;
  } | null;
  overlap: { violation_share: number; limited_share: number; limited_overlap: boolean } | null;
  balance: BalanceRow[];
  srm_p_value: number | null;
  checks: DesignCheck[];
}

export interface Sensitivity {
  e_value: number | null;
  e_value_ci: number | null;
  e_value_method: string | null;
  robustness_value: number | null;
  rosenbaum: { gamma_star: number | null; n_pairs: number } | null;
}

export interface Triangulation {
  estimators: string[];
  agree: boolean;
  detail: string;
}

export interface SegmentEffect {
  variable: string;
  level: string;
  n_used: number;
  estimate: EstimateRecord | null;
  family_wise_ci: Interval | null;
  refusal: string | null;
  refusal_detail: string;
}

export interface CausalQueryView {
  treatment: string;
  outcome: string;
  estimand: string;
  design: string;
}

export interface CausalResult {
  kind: "result";
  status: "supported" | "provisional" | "contested";
  query: CausalQueryView;
  certificate: IdentificationCertificate;
  primary: EstimateRecord;
  estimates: EstimateRecord[];
  diagnostics: Diagnostics;
  refutations: RefutationResult[];
  sensitivity: Sensitivity | null;
  triangulation: Triangulation | null;
  segments: SegmentEffect[];
  warnings: string[];
  result_digest: string | null;
}

export interface Abstention {
  kind: "refusal";
  reason: string;
  detail: string;
  missing_information: string[];
  certificate: IdentificationCertificate | null;
  diagnostics: Diagnostics | null;
}

export interface ApparatusFailure {
  kind: "apparatus_failure";
  stage: string;
  error_type: string;
  detail: string;
}

export type CausalOutcome = CausalResult | Abstention | ApparatusFailure;

// ── /api/v1/analysis ─────────────────────────────────────────────────────────

export type Design = "observational" | "randomized";
export type Estimand = "ate" | "att";
export type Direction = "increase" | "decrease";

export interface ExampleSummary {
  id: string;
  title: string;
  summary: string;
  graph: string;
  treatment: string;
  outcome: string;
  design: Design;
  direction: Direction;
  action: string;
  minimum_worthwhile_effect: number;
  true_effect: number | null;
  true_effect_note: string;
  lesson: string;
  n_rows: number;
  column_names: string[];
}

export interface ExampleData extends ExampleSummary {
  columns: Columns;
}

export interface Identification {
  identified: boolean;
  certificate: IdentificationCertificate | null;
  refusal: string | null;
  missing_information: string[];
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: "directed" | "bidirected";
  origin: string;
  status: string;
}

export interface CheckResponse {
  valid: boolean;
  error: string | null;
  graph: { nodes: string[]; edges: GraphEdge[]; latent: string[] } | null;
  observed: string[];
  latent: string[];
  unmeasured: string[];
  unused_columns: string[];
  unreviewed_edges: string[];
  testable_implications: Array<{ a: string; b: string; given: string[] }>;
  identification: Identification | null;
}

export interface CheckRequest {
  graph: string;
  treatment?: string;
  outcome?: string;
  estimand?: Estimand;
  columns?: string[];
  query?: { design?: Design };
}

export interface RunRequest {
  graph: string;
  treatment: string;
  outcome: string;
  estimand: Estimand;
  data: Columns;
  query?: { design?: Design };
}

export interface RunResponse {
  outcome: CausalOutcome;
  report: string;
}

export interface DecideRequest extends RunRequest {
  action: string;
  direction: Direction;
  risk_class: "low" | "medium" | "high";
  reversible: boolean;
  minimum_worthwhile_effect: number;
  harm_threshold: number;
  can_randomize: boolean;
  narrate?: boolean;
  /** Where the question came from (e.g. `oracle:H-01`); sent on to agentcenter. */
  origin?: string;
}

export interface DecideResponse {
  verdict: DecisionResponse["verdict"];
  explanation: string;
  decision: DecisionResponse;
  outcome: CausalOutcome;
  record: DecisionRecordResponse;
  narrative: { text: string; source: "llm" | "deterministic"; reason: string } | null;
}

export const analysisApi = {
  examples: () => api.get<ExampleSummary[]>("/api/v1/analysis/examples"),
  example: (id: string) =>
    api.get<ExampleData>(`/api/v1/analysis/examples/${encodeURIComponent(id)}`),
  check: (body: CheckRequest) => api.post<CheckResponse>("/api/v1/analysis/check", body),
  run: (body: RunRequest) => api.post<RunResponse>("/api/v1/analysis/run", body),
  decide: (body: DecideRequest) => api.post<DecideResponse>("/api/v1/decisions", body),
};

// ── CSV ──────────────────────────────────────────────────────────────────────

export interface ParsedTable {
  columns: Columns;
  names: string[];
  nRows: number;
}

/** Split CSV text into rows of fields (RFC 4180: quotes, doubled quotes, CRLF). */
export function splitCsv(text: string, delimiter = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => !(r.length === 1 && (r[0] ?? "").trim() === ""));
}

const MISSING = new Set(["", "na", "nan", "null", "none", "n/a"]);

/** A column's cells: numbers when every present value parses as one, else strings. */
function typed(raw: string[]): Cell[] {
  const present = raw.filter((v) => !MISSING.has(v.trim().toLowerCase()));
  const numeric = present.length > 0 && present.every((v) => v.trim() !== "" && Number.isFinite(Number(v)));
  const bool = !numeric && present.length > 0 && present.every((v) => /^(true|false)$/i.test(v.trim()));
  return raw.map((v) => {
    const t = v.trim();
    if (MISSING.has(t.toLowerCase())) return null;
    if (numeric) return Number(t);
    if (bool) return t.toLowerCase() === "true" ? 1 : 0;
    return t;
  });
}

/** Parse CSV (or semicolon/tab separated) text with a header row. */
export function parseCsv(text: string): ParsedTable {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  const delimiter = [",", ";", "\t"].reduce((best, d) =>
    firstLine.split(d).length > firstLine.split(best).length ? d : best
  );
  const rows = splitCsv(text.replace(/^﻿/, ""), delimiter);
  const head = rows[0];
  if (!head || rows.length < 2) {
    throw new Error("the file needs a header row and at least one data row");
  }
  const header = head.map((h, i) => h.trim() || `column_${i + 1}`);
  const dupes = header.filter((h, i) => header.indexOf(h) !== i);
  if (dupes.length) throw new Error(`duplicate column names: ${[...new Set(dupes)].join(", ")}`);
  const body = rows.slice(1);
  const bad = body.findIndex((r) => r.length !== header.length);
  if (bad >= 0) {
    throw new Error(
      `row ${bad + 2} has ${body[bad]?.length ?? 0} fields, the header has ${header.length}`
    );
  }
  const columns: Columns = {};
  header.forEach((name, j) => {
    columns[name] = typed(body.map((r) => r[j] ?? ""));
  });
  return { columns, names: header, nRows: body.length };
}

export type ColumnKind = "binary" | "numeric" | "text";

export interface ColumnProfile {
  name: string;
  kind: ColumnKind;
  missing: number;
  distinct: number;
  mean: number | null;
}

export function profileColumns(columns: Columns): ColumnProfile[] {
  return Object.entries(columns).map(([name, cells]) => {
    const present = cells.filter((c): c is Exclude<Cell, null> => c !== null);
    const distinct = new Set(present.map(String)).size;
    const nums = present.filter((c): c is number => typeof c === "number");
    const numeric = nums.length === present.length && present.length > 0;
    const binary = numeric && nums.every((v) => v === 0 || v === 1);
    return {
      name,
      kind: binary ? "binary" : numeric ? "numeric" : "text",
      missing: cells.length - present.length,
      distinct,
      mean: numeric ? nums.reduce((a, b) => a + b, 0) / nums.length : null,
    };
  });
}

// ── Graph text ───────────────────────────────────────────────────────────────

/** The graph a "treatment, outcome, and what influences both" form describes. */
export function graphFromRoles(
  treatment: string,
  outcome: string,
  confounders: string[],
  outcomeOnly: string[] = []
): string {
  const parts: string[] = [];
  for (const c of confounders) parts.push(`${c} -> ${treatment}`, `${c} -> ${outcome}`);
  for (const c of outcomeOnly) parts.push(`${c} -> ${outcome}`);
  parts.push(`${treatment} -> ${outcome}`);
  return parts.join("; ");
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** A number to a sensible number of significant figures for display. */
export function fmt(x: number | null | undefined, digits = 3): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "—";
  if (x === 0) return "0";
  const a = Math.abs(x);
  if (a >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return Number(x.toPrecision(digits)).toString();
}

// ── Second brain (/api/v1/brain: agentcenter and its Oracle) ─────────────────

export interface StudyPrefill {
  graph: string;
  treatment: string;
  outcome: string;
  design: Design;
  estimand: Estimand;
  action: string | null;
  direction: Direction | null;
  minimum_worthwhile_effect: number | null;
  arms: Record<string, string>;
  outcome_label: string | null;
}

export interface Suggestion {
  id: string;
  source: "oracle.hypothesis" | "oracle.review" | "radar";
  kind: "experiment" | "next_step" | "improvement";
  title: string;
  why: string;
  project: string | null;
  detail: string[];
  zone: string | null;
  rung: string | null;
  study: StudyPrefill | null;
  data: { fetchable: boolean; builder: string; note: string } | null;
  time: string | null;
}

export interface SuggestionSource {
  name: string;
  url: string | null;
  reachable: boolean;
  error: string | null;
  count: number;
}

export interface SuggestionsResponse {
  enabled: boolean;
  project: string;
  emit: boolean;
  sources: SuggestionSource[];
  suggestions: Suggestion[];
}

export interface BoundData {
  suggestion_id: string;
  columns: Columns;
  sources: string[];
  note: string;
}

export const brainApi = {
  suggestions: () => api.get<SuggestionsResponse>("/api/v1/brain/suggestions"),
  /** `id` is the suggestion id, `oracle:H-01`, or the bare hypothesis id. */
  data: (id: string) =>
    api.get<BoundData>(
      `/api/v1/brain/suggestions/${encodeURIComponent(id.replace(/^oracle:/, ""))}/data`
    ),
};

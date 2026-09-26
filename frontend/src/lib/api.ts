/**
 * Typed fetch wrapper for the Causal Platform API.
 * All requests inject the auth token from the Zustand auth store and
 * include a request ID for distributed tracing / audit correlation.
 *
 * Route reference (as-built, 2026-04-05):
 *   Session   → /api/v1/session/run | /incident | /{id}/state
 *   Governance→ /api/governance/review-queue | /approve | /reject | /audit-log
 *   Semantic  → /api/semantic/entities | /fragments | /drafts | /edges/{id}/audit-trail
 *   Decisions → /api/v1/decisions | /verify | /calibration | /{entry_digest}/followup
 *   Models    → /api/v1/models | /{name} | /versions/{v} | /diff | /impact | /verify
 *   Health    → /health  (public)
 */

import type {
  ApprovalStatus,
  AuditEntry,
  CynefinDomain,
  EdgeDirection,
  EdgeType,
  GraphFragment,
  GuardianVerdict,
  RiskTier,
} from "@causalplatform/semantic-types";
import { type ApiError } from "@/lib/types";

// ─── Configuration ────────────────────────────────────────────────────────────

const API_BASE_URL =
  process.env["NEXT_PUBLIC_API_BASE_URL"] ?? "http://localhost:8000";

// ─── Errors ───────────────────────────────────────────────────────────────────

export class CausalApiError extends Error {
  constructor(
    public readonly status: number,
    /**
     * The backend's machine-readable code: the `error` field its exception
     * handlers send (unauthorized, forbidden, guardian_blocked, not_found,
     * circuit_breaker_open), else HTTP_ERROR, or NETWORK_ERROR (status 0).
     */
    public readonly code: string,
    message: string,
    public readonly details?: Record<string, unknown>
  ) {
    super(message);
    this.name = "CausalApiError";
  }

  /**
   * The caller is not authenticated (401), or is authenticated and not allowed
   * (403). A 403 from a Guardian block is a policy verdict, not an
   * authorisation failure, and does not count.
   */
  get isNotAuthorised(): boolean {
    return (
      this.status === 401 ||
      (this.status === 403 && this.code !== "guardian_blocked")
    );
  }
}

/** True when `err` is a 401/403 authorisation failure from the backend. */
export function isNotAuthorised(err: unknown): boolean {
  return err instanceof CausalApiError && err.isNotAuthorised;
}

/**
 * FastAPI's 422 body: `detail` is a list of {loc, msg, type}. Rendered as
 * "field.path: message; ..." so the UI can say which field was refused.
 */
function formatValidationDetail(detail: unknown): string | undefined {
  if (!Array.isArray(detail) || detail.length === 0) return undefined;
  const parts = detail.map((d) => {
    const item = (d ?? {}) as { loc?: unknown; msg?: unknown };
    const loc = Array.isArray(item.loc)
      ? item.loc.filter((p) => p !== "body").map(String).join(".")
      : "";
    const msg = typeof item.msg === "string" ? item.msg : JSON.stringify(d);
    return loc ? `${loc}: ${msg}` : msg;
  });
  return parts.join("; ");
}

// ─── Token helpers ────────────────────────────────────────────────────────────

/**
 * Reads the auth token from the Zustand persisted store, with a direct-key
 * fallback for the local-dev bypass token.
 *
 * Zustand persist key: "causal-platform-auth"
 * Shape: { state: { token: string|null, userId: string|null }, version: 0 }
 */
function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;

  // Primary: read from Zustand persisted store
  try {
    const raw = localStorage.getItem("causal-platform-auth");
    if (raw) {
      const parsed = JSON.parse(raw) as {
        state?: { token?: string | null };
      };
      if (parsed.state?.token) return parsed.state.token;
    }
  } catch {
    // Malformed store — fall through
  }

  // Fallback: direct key (legacy / test environments)
  const legacyToken = localStorage.getItem("causal_platform_token");
  if (legacyToken) return legacyToken;

  // Dev bypass: return the fixed dev token when running locally.
  // This guarantees the token is available on first render regardless of
  // useEffect timing in DevAuthInit.
  if (
    typeof process !== "undefined" &&
    process.env["NEXT_PUBLIC_ENVIRONMENT"] === "local"
  ) {
    return "dev-local-token";
  }

  return null;
}

// ─── Request ID generation ────────────────────────────────────────────────────

function generateRequestId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `req_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

interface FetchOptions extends RequestInit {
  /** Skip automatic auth token injection (for public endpoints) */
  skipAuth?: boolean;
}

async function apiFetch<T>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const { skipAuth = false, ...fetchOptions } = options;
  const requestId = generateRequestId();

  const headers = new Headers(fetchOptions.headers);

  headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", requestId);

  if (!skipAuth) {
    const token = getAuthToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const url = path.startsWith("http") ? path : `${API_BASE_URL}${path}`;

  let response: Response;

  try {
    response = await fetch(url, {
      ...fetchOptions,
      headers,
    });
  } catch (networkError) {
    throw new CausalApiError(
      0,
      "NETWORK_ERROR",
      "Network request failed. Check your connection.",
      { originalError: String(networkError) }
    );
  }

  if (!response.ok) {
    type ErrorBody = Partial<ApiError> & {
      error?: unknown;
      detail?: unknown;
      reason?: unknown;
    };
    let errorBody: ErrorBody | null = null;

    try {
      errorBody = (await response.json()) as ErrorBody;
    } catch {
      // Body is not JSON — use status text
    }

    // FastAPI HTTPException bodies carry the explanation in `detail` (a string,
    // or on a 422 a list of field errors); the backend's own exception handlers
    // send `error` (the code) with `message`, or with `reason` for a Guardian
    // block. Surface whichever is present so the UI can show the real cause
    // instead of a bare status code. A route that raises with a structured
    // `detail` (POST /api/semantic/drafts when the Guardian blocks: {error,
    // verdict, reason, audit_entry_id, ...}) has that object kept as `details`,
    // and its `reason` becomes the message.
    const validationMessage = formatValidationDetail(errorBody?.detail);
    const objectDetail =
      errorBody?.detail !== null &&
      typeof errorBody?.detail === "object" &&
      !Array.isArray(errorBody.detail)
        ? (errorBody.detail as Record<string, unknown>)
        : undefined;
    const backendMessage =
      (typeof errorBody?.message === "string" ? errorBody.message : undefined) ??
      (typeof errorBody?.detail === "string" ? errorBody.detail : undefined) ??
      validationMessage ??
      (typeof objectDetail?.["reason"] === "string" ? objectDetail["reason"] : undefined) ??
      (typeof errorBody?.reason === "string" ? errorBody.reason : undefined);

    const code =
      (typeof errorBody?.error === "string" ? errorBody.error : undefined) ??
      (typeof errorBody?.code === "string" ? errorBody.code : undefined) ??
      (typeof objectDetail?.["error"] === "string" ? objectDetail["error"] : undefined) ??
      (response.status === 422 ? "validation_error" : "HTTP_ERROR");

    throw new CausalApiError(
      response.status,
      code,
      backendMessage ?? `Request failed with status ${response.status}`,
      errorBody?.details ??
        objectDetail ??
        (validationMessage ? { detail: errorBody?.detail } : undefined)
    );
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

// ─── Convenience methods ──────────────────────────────────────────────────────

export const api = {
  get<T>(path: string, options?: FetchOptions): Promise<T> {
    return apiFetch<T>(path, { ...options, method: "GET" });
  },

  post<T>(path: string, body: unknown, options?: FetchOptions): Promise<T> {
    return apiFetch<T>(path, {
      ...options,
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  put<T>(path: string, body: unknown, options?: FetchOptions): Promise<T> {
    return apiFetch<T>(path, {
      ...options,
      method: "PUT",
      body: JSON.stringify(body),
    });
  },

  patch<T>(path: string, body: unknown, options?: FetchOptions): Promise<T> {
    return apiFetch<T>(path, {
      ...options,
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  delete<T>(path: string, options?: FetchOptions): Promise<T> {
    return apiFetch<T>(path, { ...options, method: "DELETE" });
  },
};

// ─── Domain-scoped API clients ────────────────────────────────────────────────

// ─── Response shapes ──────────────────────────────────────────────────────────
// What the backend actually serialises, checked against its pydantic models.
// Two conventions coexist: models with alias_generator=_camel (AuditEntry,
// GraphFragment, CausalEdge) arrive camelCase; plain models (the envelopes,
// SessionStateResponse, ReviewQueueItem) arrive snake_case.

/**
 * Response of /api/v1/session/{run,incident,{id}/state} — backend
 * SessionStateResponse (backend/src/api/session.py), snake_case.
 */
export interface SessionStateResponse {
  session_id: string;
  agent_id: string;
  cynefin_domain: CynefinDomain;
  domain_confidence: number;
  guardian_verdict: GuardianVerdict;
  policy_rules_cited: string[];
  overall_confidence: number;
  requires_human_review: boolean;
  flags: string[];
  draft_explanation: string;
  proposed_edge_count: number;
  recommendation_count: number;
  recommendations: Array<{
    rank: number;
    action: string;
    rationale: string;
    confidence: number;
    risk_tier: string;
  }>;
  cycle_count: number;
  timestamp: string;
}

/** One row of GET /api/governance/review-queue (governance.py ReviewQueueItem), snake_case. */
export interface ReviewQueueItemResponse {
  id: string;
  object_type: string;
  domain: string;
  name: string;
  /** DRAFT or UNDER_REVIEW -- the queue lists only objects pending review. */
  approval_status: ApprovalStatus;
  created_at: string | null;
  /**
   * Derived by the backend from stored fields (review_risk.py): edges by stated
   * confidence and stored sensitivity results, fragments by whether they carry an
   * unapproved edge, entities LOW. The queue arrives most at risk first.
   */
  risk_tier: RiskTier;
  risk_reasons: string[];
  /** The id in the object's created_by; null where none was recorded. */
  submitted_by: string | null;
}

/**
 * GET /api/governance/audit-log — governance.py AuditLogResponse. The envelope
 * is snake_case; each entry is AuditEntry, which serialises camelCase.
 */
export interface AuditLogResponse {
  entries: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** Query parameters GET /api/governance/audit-log accepts (governance.py get_audit_log). */
export interface AuditLogParams {
  from?: string;
  to?: string;
  agent_id?: string;
  verdict?: GuardianVerdict;
  page?: number;
  /** 1..500, backend default 100 */
  page_size?: number;
}

/**
 * GET /api/semantic/fragments[/{id}] — backend GraphFragment, camelCase.
 * Only the fields whose canonical type matches the wire: entryNodes,
 * exitNodes and createdBy are left out because the canonical CausalObjectRef
 * and UserRef are shaped differently from the backend's.
 */
export type GraphFragmentResponse = Pick<
  GraphFragment,
  | "id"
  | "name"
  | "domain"
  | "description"
  | "edges"
  | "approvalStatus"
  | "version"
  | "usageCount"
  | "tags"
  | "guardianVerdictRef"
  | "createdAt"
>;

/**
 * GET /api/semantic/edges/{id} — backend CausalEdge, camelCase. source and
 * target are the backend's CausalObjectRef ({id, objectType}), not the
 * canonical one; approvedBy is the backend's UserRef ({id, email, name}).
 */
export interface CausalEdgeResponse {
  id: string;
  source: { id: string; objectType: string };
  target: { id: string; objectType: string };
  edgeType: EdgeType;
  direction: EdgeDirection;
  confidence: number;
  evidenceRefs: string[];
  assumptionRefs: string[];
  approvalStatus: ApprovalStatus;
  approvedBy: { id: string; email?: string | null; name?: string | null } | null;
  approvedAt: string | null;
  guardianVerdictRef: string;
  version: string;
  domain: string;
}

/** Build `?a=1&b=2` from the defined entries of `params`; "" when there are none. */
function toQuery(params?: object): string {
  if (!params) return "";
  const defined = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== ""
  );
  if (defined.length === 0) return "";
  return `?${new URLSearchParams(
    defined.map(([k, v]) => [k, String(v)])
  ).toString()}`;
}

/** Session orchestration — primary AI pipeline entry point */
export const sessionApi = {
  run: (body: unknown) =>
    api.post<SessionStateResponse>("/api/v1/session/run", body),
  incident: (body: unknown) =>
    api.post<SessionStateResponse>("/api/v1/session/incident", body),
  getState: (sessionId: string) =>
    api.get<SessionStateResponse>(`/api/v1/session/${sessionId}/state`),
};

/** Response of POST /api/governance/check (backend GuardianCheckResponse). */
export interface GuardianCheckResponse {
  verdict: string;
  reason: string;
  policy_rules_cited: string[];
  /** audit_log row written by the Guardian — used as verdict_id for approve/reject */
  audit_entry_id: string;
  session_id: string | null;
}

/** Governance — review queue, approve/reject, audit log */
export const governanceApi = {
  // Routes: /api/governance/... (no v1 prefix)
  reviewQueue: (domain?: string) =>
    api.get<ReviewQueueItemResponse[]>(
      `/api/governance/review-queue${toQuery({ domain })}`
    ),
  auditLog: (params?: AuditLogParams) =>
    api.get<AuditLogResponse>(`/api/governance/audit-log${toQuery(params)}`),
  check: (body: unknown) =>
    api.post<GuardianCheckResponse>("/api/governance/check", body),
  approve: (objectId: string, objectType: string, verdictId: string, notes?: string) =>
    api.post(`/api/governance/approve/${objectId}`, {
      object_type: objectType,
      verdict_id: verdictId,
      notes,
    }),
  reject: (objectId: string, objectType: string, reason: string, verdictId: string) =>
    api.post(`/api/governance/reject/${objectId}`, {
      object_type: objectType,
      reason,
      verdict_id: verdictId,
    }),
};

/** Incidents — durable list + single fetch */
export interface IncidentRecord {
  id: string;
  title: string;
  severity: string;
  affected_services: string[];
  status: string;
  session_id: string | null;
  cynefin_domain: string | null;
  domain_confidence: number | null;
  overall_confidence: number | null;
  guardian_verdict: string | null;
  draft_explanation: string;
  causal_hypotheses: Array<Record<string, unknown>>;
  proposed_interventions: Array<Record<string, unknown>>;
  submitted_by: string;
  created_at: string;
  updated_at: string;
}

export const incidentsApi = {
  list: (params?: { limit?: number; status?: string }) => {
    const search = params
      ? `?${new URLSearchParams(
          Object.fromEntries(
            Object.entries(params).filter(([, v]) => v !== undefined)
          ) as Record<string, string>
        ).toString()}`
      : "";
    return api.get<IncidentRecord[]>(`/api/v1/incidents/${search}`);
  },
  get: (id: string) =>
    api.get<IncidentRecord>(`/api/v1/incidents/${encodeURIComponent(id)}`),
};

/** Interventions — approve/reject decisions on proposed interventions */
export interface InterventionDecisionResponse {
  intervention_id: string;
  decision: "APPROVED" | "REJECTED";
  audit_log_id: string;
  recorded_at: string;
  already_decided: boolean;
}

export interface InterventionDecisionRecord {
  intervention_id: string;
  decision: "APPROVED" | "REJECTED";
  audit_log_id: string;
  recorded_at: string;
  incident_id: string | null;
  decided_by: string;
}

export const interventionApi = {
  approve: (
    interventionId: string,
    body: { reason: string; incident_id?: string; intervention_title?: string }
  ) =>
    api.post<InterventionDecisionResponse>(
      `/api/v1/interventions/${encodeURIComponent(interventionId)}/approve`,
      body
    ),
  reject: (
    interventionId: string,
    body: { reason: string; incident_id?: string; intervention_title?: string }
  ) =>
    api.post<InterventionDecisionResponse>(
      `/api/v1/interventions/${encodeURIComponent(interventionId)}/reject`,
      body
    ),
  decisions: (params?: { incident_id?: string; limit?: number }) => {
    const search = params
      ? `?${new URLSearchParams(
          Object.fromEntries(
            Object.entries(params).filter(([, v]) => v !== undefined)
          ) as Record<string, string>
        ).toString()}`
      : "";
    return api.get<InterventionDecisionRecord[]>(
      `/api/v1/interventions/decisions${search}`
    );
  },
};

// ─── Drafts (POST /api/semantic/drafts) ───────────────────────────────────────

/** DraftSubmissionRequest.object_type (backend/src/api/semantic.py type_map). */
export type DraftObjectType = "CausalEdge" | "GraphFragment" | "CausalEntity" | "Assumption";

/**
 * What a draft is judged in: a reasoning session's classification, or the
 * drafter's declared Cynefin domain. DISORDER is not offered: it means "not
 * classified", and Guardian rule PR-006 refuses every draft in it.
 */
export type DraftClassification =
  | { sessionId: string }
  | { cynefinDomain: Exclude<CynefinDomain, "DISORDER"> };

/**
 * 201 of POST /api/semantic/drafts (DraftSubmissionResponse), snake_case. The
 * route that reads the classification also returns the domain it judged the
 * draft in and where that came from; a backend that predates it omits both.
 */
export interface DraftSubmissionResponse {
  object_id: string;
  object_type: string;
  guardian_verdict: string;
  audit_entry_id: string;
  cynefin_domain?: string;
  domain_source?: string;
  message: string;
}

/**
 * The `detail` of the 403 POST /api/semantic/drafts raises when the Guardian
 * does not approve: `{error: "guardian_blocked", verdict, reason,
 * audit_entry_id}`, plus the rules cited and the domain judged in on a backend
 * that reads the classification.
 */
export interface DraftBlockedDetail {
  error: "guardian_blocked";
  verdict: string;
  reason: string;
  audit_entry_id: string;
  policy_rules_cited?: string[];
  cynefin_domain?: string;
  domain_source?: string;
}

/** The Guardian's block, if `err` is one; null for any other failure. */
export function draftBlockOf(err: unknown): DraftBlockedDetail | null {
  if (!(err instanceof CausalApiError) || err.status !== 403) return null;
  const d = err.details;
  if (!d || d["error"] !== "guardian_blocked") return null;
  if (typeof d["verdict"] !== "string" || typeof d["reason"] !== "string") return null;
  return d as unknown as DraftBlockedDetail;
}

// ─── Decision ledger (/api/v1/decisions) ──────────────────────────────────────
// Shapes of causal_decision's pydantic models as FastAPI serialises them
// (snake_case, no aliases). Tuples arrive as arrays.

/** causal_decision.contract.Verdict */
export type DecisionVerdict = "act" | "act_with_approval" | "experiment" | "refuse";

/** causal_decision.assumptions.ModelRef */
export interface ModelRefResponse {
  name: string;
  version: number;
  digest: string;
}

/** causal_decision.contract.Check */
export interface DecisionCheck {
  name: string;
  passed: boolean;
  allows: DecisionVerdict;
  detail: string;
}

/** causal_decision.contract.SegmentEvidence */
export interface SegmentEvidenceResponse {
  level: string;
  variable: string;
  n_used: number;
  benefit?: number | null;
  benefit_ci?: [number, number] | null;
  refusal?: string | null;
}

/**
 * causal_decision.contract.Evidence. Fields added after decisions were first
 * recorded (segments' siblings `trend_breakdown`, `heterogeneity_differs`) are
 * omitted from the JSON when unset, hence optional.
 */
export interface DecisionEvidence {
  kind: "result" | "refusal" | "apparatus_failure";
  status?: string | null;
  refusal_reason?: string | null;
  estimator?: string | null;
  estimand?: string | null;
  identification_strategy?: string | null;
  identification_tier?: string | null;
  benefit?: number | null;
  benefit_ci?: [number, number] | null;
  ci_level?: number | null;
  e_value_ci?: number | null;
  refuters_failed?: string[];
  triangulation_agree?: boolean | null;
  n_used?: number | null;
  outcome_sd?: number | null;
  result_digest?: string | null;
  segments?: SegmentEvidenceResponse[];
  trend_breakdown?: number | null;
  heterogeneity_differs?: boolean | null;
}

/** causal_decision.contract.DecisionRequest */
export interface DecisionRequestResponse {
  action: string;
  outcome: string;
  direction: "increase" | "decrease";
  risk_class: "low" | "medium" | "high";
  reversible: boolean;
  minimum_worthwhile_effect: number;
  harm_threshold: number;
  population: string | null;
  horizon: string | null;
  can_randomize: boolean;
  randomization_unit: string;
  units_per_day: number | null;
}

/** causal_decision.contract.ExperimentSpec */
export interface ExperimentSpecResponse {
  question: string;
  design: "randomized_units";
  unit: string;
  arms: [string, string];
  allocation: number;
  minimum_detectable_effect: number;
  outcome_sd: number | null;
  units_per_arm: number | null;
  estimated_days: number | null;
  guardrails: string[];
  note: string;
}

/** causal_decision.contract.Decision */
export interface DecisionResponse {
  verdict: DecisionVerdict;
  request: DecisionRequestResponse;
  evidence: DecisionEvidence;
  checks: DecisionCheck[];
  rationale: string;
  experiment: ExperimentSpecResponse | null;
  what_would_change_it: string[];
  policy_digest: string;
  request_digest: string;
  decision_digest: string | null;
}

/** causal_decision.ledger.EvidenceRef */
export interface EvidenceRefResponse {
  outcome_kind: "result" | "refusal" | "apparatus_failure";
  result_digest: string | null;
  graph_digest: string | null;
  data_digest: string | null;
  query_digest: string | null;
  contract_version: string | null;
  core_version: string | null;
  model?: ModelRefResponse | null;
}

/** causal_decision.ledger.Approval */
export interface ApprovalResponse {
  approver_id: string;
  approved_at: string;
  authorisation_id: string | null;
  notes: string;
}

/** causal_decision.ledger.DecisionRecord -- one link of the hash chain. */
export interface DecisionRecordResponse {
  sequence: number;
  recorded_at: string;
  decision: DecisionResponse;
  evidence: EvidenceRefResponse;
  approval: ApprovalResponse | null;
  executed: boolean;
  prev_digest: string;
  entry_digest: string;
}

/** GET /api/v1/decisions (decisions.py RecordList). */
export interface DecisionRecordList {
  records: DecisionRecordResponse[];
  total: number;
}

/** GET /api/v1/decisions/verify (causal_decision.ledger.ChainReport). */
export interface ChainReportResponse {
  ok: boolean;
  length: number;
  head_digest: string | null;
  broken_at: number | null;
  reason: string;
}

/** causal_decision.outcome.Realization */
export interface RealizationResponse {
  decision_digest: string;
  verdict: DecisionVerdict;
  predicted_benefit: number | null;
  predicted_ci: [number, number] | null;
  realized_benefit: number | null;
  realized_ci: [number, number] | null;
  decidable: boolean;
  undecidable_reason: string | null;
  undecidable_detail: string;
  point_in_predicted_ci: boolean | null;
  intervals_overlap: boolean | null;
  cleared_worthwhile: boolean | null;
  realized_harm: boolean | null;
  /** Did the predicted interval clear the worthwhile bar; null before causal-decision 0.5.0. */
  claimed_worthwhile?: boolean | null;
}

/** POST /api/v1/decisions/{entry_digest}/followup (decisions.py FollowUpResponse). */
export interface FollowUpResponse {
  realization: RealizationResponse;
  explanation: string;
}

/** A follow-up's rows: columns as lists, covering the period after the decision. */
export type FollowUpColumns = Record<string, Array<number | string | boolean | null>>;

/**
 * GET /api/v1/decisions/calibration (causal_decision.outcome.CalibrationReport).
 * Every rate is null when nothing stands behind it.
 */
/**
 * causal_decision.outcome.RateEstimate: a proportion with its Clopper-Pearson
 * interval, or the reason none is stated (`rate` and `ci` are null exactly
 * when `refused` says why). The counts are always there.
 */
export interface RateEstimateResponse {
  successes: number;
  denominator: number;
  rate: number | null;
  ci: [number, number] | null;
  level: number;
  method: string;
  refused: string | null;
}

/** causal_decision.outcome.RateBounds: worst-case bounds with the undecidable counted as unknowns. */
export interface RateBoundsResponse {
  successes: number;
  unresolved: number;
  denominator: number;
  lower: number | null;
  upper: number | null;
  ci: [number, number] | null;
  level: number;
  method: string;
  refused: string | null;
}

/** causal_decision.outcome.PairedComparison: claimed vs confirmed worthwhile (McNemar). */
export interface PairedComparisonResponse {
  n_pairs: number;
  claimed_not_confirmed: number;
  confirmed_not_claimed: number;
  min_discordant: number;
  alpha: number;
  p_value: number | null;
  status: "difference" | "no_difference_detected" | "could_not_resolve";
  detail: string;
}

/** GET /api/v1/decisions/calibration (causal_decision.outcome.CalibrationReport). */
export interface CalibrationReportResponse {
  n_decisions: number;
  n_acted: number;
  n_decidable: number;
  n_undecidable: number;
  undecidable_by_reason: Record<string, number>;
  min_followups: number;
  point_in_interval_rate: RateEstimateResponse;
  interval_overlap_rate: RateEstimateResponse;
  acted_and_worthwhile_rate: RateEstimateResponse;
  acted_and_harmful_rate: RateEstimateResponse;
  acted_worthwhile_bounds: RateBoundsResponse;
  worthwhile_claimed_vs_confirmed: PairedComparisonResponse;
  acted_and_harmful: number;
  not_acted_but_worthwhile: number;
  by_verdict: Record<string, number>;
  note: string;
}

/** Decision ledger — records, chain verification, follow-ups, calibration */
export const decisionsApi = {
  list: (params?: { action?: string; limit?: number }) =>
    api.get<DecisionRecordList>(`/api/v1/decisions${toQuery(params)}`),
  verify: () => api.get<ChainReportResponse>("/api/v1/decisions/verify"),
  calibration: (params?: { limit?: number }) =>
    api.get<CalibrationReportResponse>(
      `/api/v1/decisions/calibration${toQuery(params)}`
    ),
  followup: (entryDigest: string, data: FollowUpColumns) =>
    api.post<FollowUpResponse>(
      `/api/v1/decisions/${encodeURIComponent(entryDigest)}/followup`,
      { data }
    ),
};

// ─── Assumption registry (/api/v1/models) ─────────────────────────────────────

/** causal_core.contract.Edge (the kernel's edge, not the semantic layer's CausalEdge). */
export interface KernelEdge {
  source: string;
  target: string;
  kind: "directed" | "bidirected";
  origin: "human" | "data" | "experiment" | "literature" | "llm_prior";
  status: "asserted" | "proposed" | "approved" | "refuted";
  evidence: string[];
}

/** causal_core.contract.CausalGraph */
export interface KernelGraph {
  nodes: string[];
  edges: KernelEdge[];
  latent: string[];
}

/** causal_decision.assumptions.Assumption */
export interface ModelAssumption {
  kind:
    | "no_interference"
    | "consistency"
    | "positivity"
    | "measurement"
    | "stable_population"
    | "timing"
    | "other";
  statement: string;
  about: string[];
}

/** causal_decision.assumptions.EdgeReview */
export interface EdgeReviewResponse {
  edge: string;
  status: "approved" | "refuted";
  reason: string;
}

/** causal_decision.assumptions.ModelVersion -- one immutable version. */
export interface ModelVersionResponse {
  name: string;
  version: number;
  graph: KernelGraph;
  assumptions: ModelAssumption[];
  parent_digest: string | null;
  author_id: string;
  created_at: string;
  why: string;
  edge_authors: Record<string, string>;
  reviews: EdgeReviewResponse[];
  digest: string;
}

/** One row of GET /api/v1/models (models.py ModelSummary). */
export interface ModelSummaryResponse {
  name: string;
  version: number;
  digest: string;
  author_id: string;
  created_at: string;
  unreviewed_edges: string[];
}

/** GET /api/v1/models/{name} (models.py ModelHistory). */
export interface ModelHistoryResponse {
  head: ModelVersionResponse;
  versions: ModelRefResponse[];
}

/** causal_decision.assumptions.EdgeChange */
export interface EdgeChangeResponse {
  edge: string;
  before: KernelEdge;
  after: KernelEdge;
}

/** GET /api/v1/models/{name}/diff (causal_decision.assumptions.ModelDiff). */
export interface ModelDiffResponse {
  added_edges: KernelEdge[];
  removed_edges: KernelEdge[];
  changed_edges: EdgeChangeResponse[];
  added_nodes: string[];
  removed_nodes: string[];
  latent_added: string[];
  latent_removed: string[];
  assumptions_added: ModelAssumption[];
  assumptions_removed: ModelAssumption[];
}

/** causal_decision.assumptions.ImpactStatus */
export type ImpactStatus =
  | "holds"
  | "invalidated"
  | "unverified"
  | "gained"
  | "still_unidentified";

/** causal_decision.assumptions.Impact */
export interface ImpactResponse {
  status: ImpactStatus;
  detail: string;
  old_strategy: string | null;
  new_strategy: string | null;
  old_adjustment_set: string[];
  new_adjustment_set: string[];
  unreviewed_before: string[];
  unreviewed_after: string[];
  digest: string;
}

/** models.py DecisionImpact */
export interface DecisionImpactResponse {
  entry_digest: string;
  model_version: number;
  verdict: string;
  action: string;
  impact: ImpactResponse;
}

/** GET /api/v1/models/{name}/impact (models.py ImpactReport). */
export interface ImpactReportResponse {
  model: string;
  to: ModelRefResponse;
  decisions: DecisionImpactResponse[];
  by_status: Record<string, number>;
  note: string;
}

/** GET /api/v1/models/{name}/verify (causal_decision.assumptions.HistoryReport). */
export interface HistoryReportResponse {
  ok: boolean;
  length: number;
  head_digest: string | null;
  broken_at: number | null;
  reason: string;
}

/** Assumption registry — models, versions, diff, impact, history verification */
export const modelsApi = {
  list: () => api.get<{ models: ModelSummaryResponse[] }>("/api/v1/models"),
  get: (name: string) =>
    api.get<ModelHistoryResponse>(`/api/v1/models/${encodeURIComponent(name)}`),
  version: (name: string, version: number) =>
    api.get<ModelVersionResponse>(
      `/api/v1/models/${encodeURIComponent(name)}/versions/${version}`
    ),
  diff: (name: string, from: number, to: number) =>
    api.get<ModelDiffResponse>(
      `/api/v1/models/${encodeURIComponent(name)}/diff${toQuery({ from, to })}`
    ),
  impact: (name: string, to?: number) =>
    api.get<ImpactReportResponse>(
      `/api/v1/models/${encodeURIComponent(name)}/impact${toQuery({ to })}`
    ),
  verify: (name: string) =>
    api.get<HistoryReportResponse>(
      `/api/v1/models/${encodeURIComponent(name)}/verify`
    ),
};

/** Semantic layer — entities, fragments, drafts */
export const semanticApi = {
  // Routes: /api/semantic/... (no v1 prefix)
  fragments: (domain?: string, status?: GraphFragmentResponse["approvalStatus"]) =>
    api.get<GraphFragmentResponse[]>(
      `/api/semantic/fragments${toQuery({ domain, status })}`
    ),
  fragment: (id: string) =>
    api.get<GraphFragmentResponse>(`/api/semantic/fragments/${id}`),
  entities: (domain?: string) =>
    api.get(`/api/semantic/entities${domain ? `?domain=${domain}` : ""}`),
  entity: (id: string) => api.get(`/api/semantic/entities/${id}`),
  edge: (id: string) => api.get<CausalEdgeResponse>(`/api/semantic/edges/${id}`),
  /**
   * POST /api/semantic/drafts. The Guardian judges a draft in a classified
   * situation: a reasoning session's own domain (`session_id`), or the domain
   * the drafter declares (`cynefin_domain`). With neither it is DISORDER, which
   * rule PR-006 refuses, so the client requires one. A block comes back as 403
   * with a structured detail -- read it with {@link draftBlockOf}.
   */
  submitDraft: (
    objectType: DraftObjectType,
    payload: unknown,
    classification: DraftClassification
  ) =>
    api.post<DraftSubmissionResponse>("/api/semantic/drafts", {
      object_type: objectType,
      payload,
      ...("sessionId" in classification
        ? { session_id: classification.sessionId }
        : { cynefin_domain: classification.cynefinDomain }),
    }),
  edgeAuditTrail: (edgeId: string) =>
    api.get<AuditEntry[]>(`/api/semantic/edges/${edgeId}/audit-trail`),
};

/** Health check — public, no auth */
export const healthApi = {
  check: () =>
    api.get<{ status: string; timestamp: string; version: string; env: string }>(
      "/health",
      { skipAuth: true }
    ),
};

/** Platform Ops — real backend health, metrics, drift alerts */
export const platformApi = {
  connectors: () =>
    api.get<Array<{
      connector_id: string;
      connector_name: string;
      connector_type: string;
      healthy: boolean;
      last_successful_sync: string | null;
      last_attempt_at: string;
      error_message: string | null;
      objects_mapped: number;
      sync_duration_ms: number | null;
      raw_output: Record<string, unknown>;
    }>>("/api/v1/platform/connectors"),

  metrics: () =>
    api.get<{
      weekly_sessions: Array<{
        week: string;
        week_start: string;
        session_count: number;
        approval_rate: number;
        escalation_count: number;
      }>;
      policy_patterns: Array<{
        rank: number;
        rule_id: string;
        description: string;
        occurrences: number;
        last_seen: string;
      }>;
      fragment_stats: {
        total: number;
        approved: number;
        under_review: number;
        draft: number;
        deprecated: number;
      };
      total_audit_entries: number;
      total_sessions_all_time: number;
      overall_approval_rate: number;
      last_activity_at: string | null;
    }>("/api/v1/platform/metrics"),

  drift: () =>
    api.get<Array<{
      id: string;
      domain: string;
      entity_type: string;
      severity: string;
      message: string;
      detected_at: string;
      affected_fragment_ids: string[];
    }>>("/api/v1/platform/drift"),
};

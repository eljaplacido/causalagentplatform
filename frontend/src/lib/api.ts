/**
 * Typed fetch wrapper for the Causal Platform API.
 * All requests inject the auth token from the Zustand auth store and
 * include a request ID for distributed tracing / audit correlation.
 *
 * Route reference (as-built, 2026-04-05):
 *   Session   → /api/v1/session/run | /incident | /{id}/state
 *   Governance→ /api/governance/review-queue | /approve | /reject | /audit-log
 *   Semantic  → /api/semantic/entities | /fragments | /drafts | /edges/{id}/audit-trail
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
    public readonly code: string,
    message: string,
    public readonly details?: Record<string, unknown>
  ) {
    super(message);
    this.name = "CausalApiError";
  }
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
    let errorBody: (ApiError & { detail?: unknown; reason?: unknown }) | null =
      null;

    try {
      errorBody = (await response.json()) as ApiError & {
        detail?: unknown;
        reason?: unknown;
      };
    } catch {
      // Body is not JSON — use status text
    }

    // FastAPI HTTPException bodies carry the explanation in `detail`; the
    // Guardian-blocked handler uses `reason`. Surface whichever is present so
    // the UI can show the real cause instead of a bare status code.
    const backendMessage =
      errorBody?.message ??
      (typeof errorBody?.detail === "string" ? errorBody.detail : undefined) ??
      (typeof errorBody?.reason === "string" ? errorBody.reason : undefined);

    throw new CausalApiError(
      response.status,
      errorBody?.code ?? "HTTP_ERROR",
      backendMessage ?? `Request failed with status ${response.status}`,
      errorBody?.details
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

/**
 * What a draft is judged in: a named session's classification, or the drafter's
 * declared domain. DISORDER is not offered: PR-006 refuses every draft in it.
 */
export type DraftClassification =
  | { sessionId: string }
  | { cynefinDomain: Exclude<CynefinDomain, "DISORDER"> };

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
   * POST /api/semantic/drafts. The Guardian judges the draft in a classified
   * situation: a reasoning session's own domain, or the domain the drafter
   * declares (recorded, with who declared it, in the verdict's audit row). With
   * neither it would be DISORDER, which PR-006 refuses, so one is required --
   * ask the drafter, as ApprovalPanel asks the approver.
   */
  submitDraft: (objectType: string, payload: unknown, classification: DraftClassification) =>
    api.post("/api/semantic/drafts", {
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

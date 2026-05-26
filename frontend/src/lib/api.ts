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
    let errorBody: ApiError | null = null;

    try {
      errorBody = (await response.json()) as ApiError;
    } catch {
      // Body is not JSON — use status text
    }

    throw new CausalApiError(
      response.status,
      errorBody?.code ?? "HTTP_ERROR",
      errorBody?.message ?? `Request failed with status ${response.status}`,
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

/** Session orchestration — primary AI pipeline entry point */
export const sessionApi = {
  run: (body: unknown) =>
    api.post("/api/v1/session/run", body),
  incident: (body: unknown) =>
    api.post("/api/v1/session/incident", body),
  getState: (sessionId: string) =>
    api.get(`/api/v1/session/${sessionId}/state`),
};

/** Governance — review queue, approve/reject, audit log */
export const governanceApi = {
  // Routes: /api/governance/... (no v1 prefix)
  reviewQueue: (domain?: string) =>
    api.get(`/api/governance/review-queue${domain ? `?domain=${domain}` : ""}`),
  auditLog: (params?: { from?: string; to?: string; limit?: number }) =>
    api.get(
      `/api/governance/audit-log${
        params
          ? `?${new URLSearchParams(
              params as Record<string, string>
            ).toString()}`
          : ""
      }`
    ),
  check: (body: unknown) =>
    api.post("/api/governance/check", body),
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

/** Semantic layer — entities, fragments, drafts */
export const semanticApi = {
  // Routes: /api/semantic/... (no v1 prefix)
  fragments: (domain?: string, status?: string) =>
    api.get(
      `/api/semantic/fragments${
        domain || status
          ? `?${new URLSearchParams({
              ...(domain ? { domain } : {}),
              ...(status ? { status } : {}),
            }).toString()}`
          : ""
      }`
    ),
  fragment: (id: string) => api.get(`/api/semantic/fragments/${id}`),
  entities: (domain?: string) =>
    api.get(`/api/semantic/entities${domain ? `?domain=${domain}` : ""}`),
  entity: (id: string) => api.get(`/api/semantic/entities/${id}`),
  edge: (id: string) => api.get(`/api/semantic/edges/${id}`),
  submitDraft: (objectType: string, payload: unknown) =>
    api.post("/api/semantic/drafts", { object_type: objectType, payload }),
  edgeAuditTrail: (edgeId: string) =>
    api.get(`/api/semantic/edges/${edgeId}/audit-trail`),
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

/**
 * The governance / semantic clients send the query parameters the backend
 * reads. GET /api/governance/audit-log takes from, to, agent_id, verdict, page
 * and page_size (backend/src/api/governance.py get_audit_log); it used to be
 * sent `limit`, which FastAPI ignores, so every caller got the default 100.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CausalApiError,
  decisionsApi,
  draftBlockOf,
  governanceApi,
  modelsApi,
  semanticApi,
} from "@/lib/api";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ entries: [], total: 0, page: 1, page_size: 50 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function requestedUrl(): URL {
  return new URL(String(fetchMock.mock.calls[0]![0]));
}

describe("governanceApi.auditLog", () => {
  it("sends page_size, not limit", async () => {
    await governanceApi.auditLog({ page_size: 50 });
    const url = requestedUrl();
    expect(url.pathname).toBe("/api/governance/audit-log");
    expect(url.searchParams.get("page_size")).toBe("50");
    expect(url.searchParams.has("limit")).toBe(false);
  });

  it("sends the filters under the names the backend reads, and drops undefined ones", async () => {
    await governanceApi.auditLog({
      from: "2026-09-01T00:00:00Z",
      agent_id: "governance-agent-api",
      verdict: "REJECTED",
      page: 2,
      to: undefined,
    });
    const params = requestedUrl().searchParams;
    expect(params.get("from")).toBe("2026-09-01T00:00:00Z");
    expect(params.get("agent_id")).toBe("governance-agent-api");
    expect(params.get("verdict")).toBe("REJECTED");
    expect(params.get("page")).toBe("2");
    expect(params.has("to")).toBe(false);
  });

  it("sends no query string when there are no parameters", async () => {
    await governanceApi.auditLog();
    expect(requestedUrl().search).toBe("");
  });
});

describe("semanticApi.fragments", () => {
  it("sends domain and status only when given", async () => {
    await semanticApi.fragments("devops");
    const params = requestedUrl().searchParams;
    expect(params.get("domain")).toBe("devops");
    expect(params.has("status")).toBe(false);
  });
});

function requestBody(): Record<string, unknown> {
  return JSON.parse(String((fetchMock.mock.calls[0]![1] as RequestInit).body)) as Record<string, unknown>;
}

function respond(status: number, body: unknown) {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
  );
}

/**
 * POST /api/semantic/drafts judges a draft in the situation the request
 * classifies: `session_id` (a reasoning session's own domain) or
 * `cynefin_domain` (declared). Sent with neither, it is DISORDER, which PR-006
 * refuses -- so the client never sends a draft without one.
 */
describe("semanticApi.submitDraft", () => {
  it("sends a declared Cynefin domain as cynefin_domain", async () => {
    await semanticApi.submitDraft("GraphFragment", { name: "f" }, { cynefinDomain: "COMPLICATED" });
    expect(requestedUrl().pathname).toBe("/api/semantic/drafts");
    expect(requestBody()).toEqual({
      object_type: "GraphFragment",
      payload: { name: "f" },
      cynefin_domain: "COMPLICATED",
    });
  });

  it("sends a reasoning session as session_id, and no declared domain", async () => {
    await semanticApi.submitDraft("GraphFragment", { name: "f" }, { sessionId: "sess-42" });
    const body = requestBody();
    expect(body["session_id"]).toBe("sess-42");
    expect(body).not.toHaveProperty("cynefin_domain");
  });
});

/**
 * The drafts route raises the Guardian's block as HTTPException(403,
 * detail={error, verdict, reason, audit_entry_id, ...}) -- an object, not a
 * string. The client keeps it, so the page can show the verdict and reason.
 */
describe("apiFetch error bodies", () => {
  const detail = {
    error: "guardian_blocked",
    verdict: "REJECTED",
    reason: "PR-006: situation not classified (DISORDER); classify before proceeding",
    audit_entry_id: "a0d1e2f3-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
  };

  it("keeps a structured 403 detail and reads the Guardian's block from it", async () => {
    respond(403, { detail });
    const err = await semanticApi
      .submitDraft("GraphFragment", {}, { cynefinDomain: "CLEAR" })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CausalApiError);
    expect((err as CausalApiError).status).toBe(403);
    expect((err as CausalApiError).message).toBe(detail.reason);
    expect(draftBlockOf(err)).toEqual(detail);
  });

  it("does not read a string 403 (out of domain scope) as a Guardian block", async () => {
    respond(403, { detail: "domain 'devops' is outside this user's domain scope" });
    const err = await semanticApi
      .submitDraft("GraphFragment", {}, { cynefinDomain: "CLEAR" })
      .catch((e: unknown) => e);
    expect((err as CausalApiError).message).toBe("domain 'devops' is outside this user's domain scope");
    expect(draftBlockOf(err)).toBeNull();
  });

  it("says each message of a 422 validation list", async () => {
    respond(422, {
      detail: [
        { loc: ["body", "data"], msg: "Field required", type: "missing" },
        { loc: ["body", "x"], msg: "Extra inputs are not permitted", type: "extra_forbidden" },
      ],
    });
    const err = await decisionsApi.followup("sha256:abc", { y: [1] }).catch((e: unknown) => e);
    expect((err as CausalApiError).message).toBe(
      "data: Field required; x: Extra inputs are not permitted"
    );
  });
});

// ─── Errors carry what the backend said ─────────────────────────────────────────
//
// Preconditions: the backend answers non-2xx with one of the bodies it really
// sends: its exception handlers' {"error": code, "message"|"reason": ...}
// (backend/src/api/main.py), FastAPI's {"detail": string}, or on a 422
// FastAPI's {"detail": [{loc, msg, type}, ...]}.
// Postconditions: CausalApiError.status is the HTTP status; .code is the
// body's `error` field when present; .message names the cause, including each
// refused field of a 422; isNotAuthorised is true for 401 and for a 403 that
// is not a Guardian block.
// Undefined, not asserted: the exact punctuation joining 422 field errors.

function respondWith(status: number, body: unknown) {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

async function caught(p: Promise<unknown>): Promise<CausalApiError> {
  try {
    await p;
  } catch (err) {
    expect(err).toBeInstanceOf(CausalApiError);
    return err as CausalApiError;
  }
  throw new Error("expected the request to fail");
}

describe("CausalApiError", () => {
  it("reads the code from the backend's `error` field", async () => {
    respondWith(403, { error: "forbidden", message: "role viewer may not approve" });
    const err = await caught(governanceApi.auditLog());
    expect(err.status).toBe(403);
    expect(err.code).toBe("forbidden");
    expect(err.message).toBe("role viewer may not approve");
    expect(err.isNotAuthorised).toBe(true);
  });

  it("treats a 401 with a FastAPI detail as not authorised", async () => {
    respondWith(401, { detail: "Authentication required: Bearer token missing." });
    const err = await caught(governanceApi.auditLog());
    expect(err.isNotAuthorised).toBe(true);
    expect(err.message).toMatch(/Bearer token missing/);
  });

  it("does not treat a Guardian block (403 guardian_blocked) as an authorisation failure", async () => {
    respondWith(403, {
      error: "guardian_blocked",
      reason: "PR-006: no approval in DISORDER",
      verdict_status: "REJECTED",
      verdict_id: "v-1",
    });
    const err = await caught(governanceApi.auditLog());
    expect(err.code).toBe("guardian_blocked");
    expect(err.isNotAuthorised).toBe(false);
    expect(err.message).toMatch(/PR-006/);
  });

  it("surfaces each refused field of a 422 list detail", async () => {
    respondWith(422, {
      detail: [
        { loc: ["body", "reason"], msg: "String should have at least 10 characters", type: "string_too_short" },
        { loc: ["body", "verdict_id"], msg: "Field required", type: "missing" },
      ],
    });
    const err = await caught(governanceApi.auditLog());
    expect(err.status).toBe(422);
    expect(err.message).toMatch(/reason: String should have at least 10 characters/);
    expect(err.message).toMatch(/verdict_id: Field required/);
    expect(err.isNotAuthorised).toBe(false);
  });
});

describe("decisionsApi", () => {
  it("lists with the action and limit the route reads", async () => {
    await decisionsApi.list({ action: "adopt the incident playbook", limit: 100 });
    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/decisions");
    expect(url.searchParams.get("action")).toBe("adopt the incident playbook");
    expect(url.searchParams.get("limit")).toBe("100");
  });

  it("reads the chain report and the calibration report from their routes", async () => {
    await decisionsApi.verify();
    expect(requestedUrl().pathname).toBe("/api/v1/decisions/verify");
    fetchMock.mockClear();
    respond(200, {});
    await decisionsApi.calibration();
    expect(requestedUrl().pathname).toBe("/api/v1/decisions/calibration");
    expect(requestedUrl().search).toBe("");
  });

  it("posts a follow-up's rows under `data` to the decision's own route", async () => {
    await decisionsApi.followup("sha256:79bd", { minutes: [52.0, 61.5] });
    expect(requestedUrl().pathname).toBe("/api/v1/decisions/sha256%3A79bd/followup");
    expect((fetchMock.mock.calls[0]![1] as RequestInit).method).toBe("POST");
    expect(requestBody()).toEqual({ data: { minutes: [52.0, 61.5] } });
  });
});

describe("modelsApi", () => {
  it("asks for a diff with the `from` and `to` aliases the route declares", async () => {
    await modelsApi.diff("incident-minutes", 1, 2);
    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/models/incident-minutes/diff");
    expect(url.searchParams.get("from")).toBe("1");
    expect(url.searchParams.get("to")).toBe("2");
  });

  it("asks for impact against a version, or the head when none is given", async () => {
    await modelsApi.impact("incident-minutes", 2);
    expect(requestedUrl().searchParams.get("to")).toBe("2");
    fetchMock.mockClear();
    respond(200, {});
    await modelsApi.impact("incident-minutes");
    expect(requestedUrl().search).toBe("");
  });

  it("reads a version, the history and its verification from their routes", async () => {
    await modelsApi.version("incident-minutes", 1);
    expect(requestedUrl().pathname).toBe("/api/v1/models/incident-minutes/versions/1");
    fetchMock.mockClear();
    respond(200, {});
    await modelsApi.get("incident-minutes");
    expect(requestedUrl().pathname).toBe("/api/v1/models/incident-minutes");
    fetchMock.mockClear();
    respond(200, {});
    await modelsApi.verify("incident-minutes");
    expect(requestedUrl().pathname).toBe("/api/v1/models/incident-minutes/verify");
  });
});

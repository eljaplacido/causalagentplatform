/**
 * The governance / semantic clients send the query parameters the backend
 * reads. GET /api/governance/audit-log takes from, to, agent_id, verdict, page
 * and page_size (backend/src/api/governance.py get_audit_log); it used to be
 * sent `limit`, which FastAPI ignores, so every caller got the default 100.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CausalApiError, governanceApi, semanticApi } from "@/lib/api";

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

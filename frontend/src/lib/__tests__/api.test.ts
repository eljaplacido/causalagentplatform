/**
 * The governance / semantic clients send the query parameters the backend
 * reads. GET /api/governance/audit-log takes from, to, agent_id, verdict, page
 * and page_size (backend/src/api/governance.py get_audit_log); it used to be
 * sent `limit`, which FastAPI ignores, so every caller got the default 100.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { governanceApi, semanticApi } from "@/lib/api";

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

/**
 * POST /api/semantic/drafts judges a draft in a classified situation: the named
 * session's domain, or the domain the drafter declares. Sent neither, the draft is
 * DISORDER and Guardian PR-006 refuses it, so the client asks for one of the two.
 */
describe("semanticApi.submitDraft", () => {
  function sentBody(): Record<string, unknown> {
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    return JSON.parse(String(init.body)) as Record<string, unknown>;
  }

  it("sends the declared Cynefin domain with the draft", async () => {
    await semanticApi.submitDraft("CausalEntity", { name: "svc" }, { cynefinDomain: "COMPLICATED" });
    expect(requestedUrl().pathname).toBe("/api/semantic/drafts");
    expect(sentBody()).toEqual({
      object_type: "CausalEntity",
      payload: { name: "svc" },
      cynefin_domain: "COMPLICATED",
    });
  });

  it("sends a session id instead, and no declared domain, when the draft names a session", async () => {
    await semanticApi.submitDraft("CausalEdge", { confidence: 0.7 }, { sessionId: "s-1" });
    expect(sentBody()).toEqual({
      object_type: "CausalEdge",
      payload: { confidence: 0.7 },
      session_id: "s-1",
    });
  });
});

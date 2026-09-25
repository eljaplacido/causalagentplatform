/**
 * The mappers read the shapes the backend actually sends.
 *
 * The JSON below is what the backend serialises, produced by constructing the
 * pydantic models and calling model_dump_json(by_alias=True):
 *
 *   AuditLogResponse(entries=[AuditEntry(...)], ...)  governance.py / models.py
 *   GraphFragment(...)                                 semantic_layer/models.py
 *
 * AuditEntry and GraphFragment use alias_generator=_camel, so their fields are
 * camelCase; the AuditLogResponse envelope is a plain model, so page_size is
 * snake_case. The engineer page used to cast this envelope to an array and read
 * agent_id / usage_count / edge_count -- keys that never arrive -- and every
 * row rendered as a default. These tests pin the real shape.
 */

import { describe, expect, it } from "vitest";
import type { AuditLogResponse, GraphFragmentResponse } from "@/lib/api";
import { toAuditLogRow, toFragmentCardData } from "@/lib/mappers";

const AUDIT_LOG_JSON =
  '{"entries":[{"id":"00000000-0000-0000-0000-000000000005","agentId":"governance-agent-api",' +
  '"actionType":"GOVERNANCE_APPROVE","payloadHash":"sha256-x","guardianVerdict":"APPROVED",' +
  '"policyRulesCited":["POL-001"],"sessionId":null,"createdAt":"2026-09-18T10:05:00",' +
  '"subjectId":"7c9e6679-7425-40de-944b-e07fc1f90ae7","notes":"ok"}],' +
  '"total":1,"page":1,"page_size":100}';

const FRAGMENT_JSON =
  '{"id":"7c9e6679-7425-40de-944b-e07fc1f90ae7","name":"deploy_latency","domain":"devops",' +
  '"description":"d","edges":["00000000-0000-0000-0000-000000000001",' +
  '"00000000-0000-0000-0000-000000000002","00000000-0000-0000-0000-000000000003"],' +
  '"entryNodes":[],"exitNodes":[],"approvalStatus":"APPROVED","version":"1.0.0",' +
  '"usageCount":4,"tags":["sre"],"guardianVerdictRef":"00000000-0000-0000-0000-000000000009",' +
  '"createdBy":{"id":"u1","email":null,"name":null},"createdAt":"2026-09-20T08:00:00"}';

describe("toAuditLogRow — GET /api/governance/audit-log", () => {
  const page = JSON.parse(AUDIT_LOG_JSON) as AuditLogResponse;

  it("the response is an envelope, not an array", () => {
    expect(Array.isArray(page)).toBe(false);
    expect(page.entries).toHaveLength(1);
    expect(page.page_size).toBe(100);
  });

  it("reads every field from the camelCase entry", () => {
    expect(page.entries.map(toAuditLogRow)).toEqual([
      {
        id: "00000000-0000-0000-0000-000000000005",
        timestamp: "2026-09-18T10:05:00",
        agent: "governance-agent-api",
        action: "GOVERNANCE_APPROVE",
        verdict: "APPROVED",
        policyRulesCited: ["POL-001"],
        payloadHash: "sha256-x",
        actorId: undefined,
        subjectId: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      },
    ]);
  });

  it("keeps the session id when there is one", () => {
    const [entry] = page.entries;
    const row = toAuditLogRow({ ...entry!, sessionId: "sess-42", subjectId: null });
    expect(row.actorId).toBe("sess-42");
    expect(row.subjectId).toBeUndefined();
  });
});

describe("toFragmentCardData — GET /api/semantic/fragments", () => {
  const fragment = JSON.parse(FRAGMENT_JSON) as GraphFragmentResponse;

  it("reads the camelCase fields and counts the edge ids (there is no edge_count)", () => {
    const card = toFragmentCardData(fragment);
    expect(card).toMatchObject({
      id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      name: "deploy_latency",
      domain: "devops",
      edgeCount: 3,
      usageCount: 4,
      approvalStatus: "APPROVED",
      tags: ["sre"],
      createdAt: "2026-09-20T08:00:00",
    });
  });

  it("derives the verdict from guardianVerdictRef, the only verdict field sent", () => {
    expect(toFragmentCardData(fragment).guardianVerdict).toBe("APPROVED");
    const draft = { ...fragment, approvalStatus: "DRAFT" as const, guardianVerdictRef: null };
    expect(toFragmentCardData(draft).guardianVerdict).toBe("PENDING");
    expect(toFragmentCardData(draft).approvalStatus).toBe("DRAFT");
  });

  it("does not invent an approval date the backend never sends", () => {
    expect(toFragmentCardData(fragment).approvedAt).toBeUndefined();
  });
});

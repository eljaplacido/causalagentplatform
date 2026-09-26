/**
 * Workbench authoring, end to end in a browser: from one surface a user opens a
 * fragment, proposes a new fragment from its edges, sees the Guardian's
 * verdict, and -- when the Guardian approves -- sees the draft in the
 * governance review queue, i.e. submitted for approval. Register entry
 * `dag-workbench` names this file.
 *
 * WHAT THIS DOES AND DOES NOT PROVE. The frontend is the real Next.js app in a
 * real browser; the BACKEND IS STUBBED at the network boundary (page.route),
 * with responses in the shapes backend/src/api/{semantic,governance}.py
 * serialise, including the classification fields of the drafts route that
 * reads `cynefin_domain` / `session_id`. So it proves the surface drives the
 * flow and renders what the API returns; it does not prove the backend accepts
 * a draft. On `main` today it does not: POST /api/semantic/drafts ignores the
 * classification and PR-006 refuses every draft (the fix is on another branch).
 * No gate step runs Playwright, so this spec is run by hand:
 *
 *   cd frontend && E2E_PORT=3107 npx playwright test e2e/workbench-authoring.spec.ts
 *   (or: npx next build && E2E_PROD=1 E2E_PORT=3107 npx playwright test ...)
 *
 * The register entry stays open until an end-to-end run against a live backend
 * accepts a draft, in a gate step.
 */

import { expect, test, type Page, type Route } from "@playwright/test";

const EDGE_A = "0b7f7a52-5c1e-4a4e-9a0c-6f1f8b2d3e41";
const EDGE_B = "5d2c9e10-7a3b-4f6e-8c1d-2e4f6a8b0c93";
const FRAGMENT_ID = "c0ffee00-1234-4abc-8def-0123456789ab";
const DRAFT_ID = "d4a1f0c2-6b8e-4c3a-9f7d-1e2b3c4d5e6f";
const AUDIT_ID = "a0d1e2f3-4a5b-4c6d-8e7f-9a0b1c2d3e4f";

const FRAGMENT = {
  id: FRAGMENT_ID,
  name: "deployment_latency_attribution",
  domain: "devops",
  description: "How a deployment moves latency and conversion.",
  edges: [EDGE_A, EDGE_B],
  entryNodes: [],
  exitNodes: [],
  approvalStatus: "APPROVED",
  version: "1.0.0",
  usageCount: 3,
  tags: ["latency"],
  guardianVerdictRef: "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  createdBy: { id: "analyst-ryo", email: null, name: null },
  createdAt: "2026-09-10T08:00:00Z",
};

function edge(id: string, source: string, target: string, confidence: number) {
  return {
    id,
    source: { id: source, objectType: "CausalEntity" },
    target: { id: target, objectType: "Outcome" },
    edgeType: "CAUSES",
    direction: "FORWARD",
    confidence,
    evidenceRefs: [],
    assumptionRefs: [],
    approvalStatus: "APPROVED",
    approvedBy: { id: "gov-olu", email: null, name: null },
    approvedAt: "2026-09-10T09:00:00Z",
    guardianVerdictRef: "8f14e45f-ceea-467a-9575-8b1f0d6a4c2e",
    version: "1.0.0",
    lineage: [],
    domain: "devops",
  };
}

const EDGES: Record<string, unknown> = {
  [EDGE_A]: edge(EDGE_A, "9a1b2c3d-4e5f-4061-8a7b-8c9d0e1f2a3b", "1f2e3d4c-5b6a-4798-8a1b-2c3d4e5f6a7b", 0.82),
  [EDGE_B]: edge(EDGE_B, "1f2e3d4c-5b6a-4798-8a1b-2c3d4e5f6a7b", "7c6b5a49-3827-4165-9f0e-1d2c3b4a5968", 0.74),
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", headers: CORS, body: JSON.stringify(body) });
}

interface Stub {
  drafts: Array<Record<string, unknown>>;
  queue: Array<Record<string, unknown>>;
}

/**
 * The API as the frontend sees it. `onDraft` decides the drafts route's answer
 * from the request body, so a test can check what was sent.
 */
async function stubBackend(
  page: Page,
  onDraft: (body: Record<string, unknown>, stub: Stub) => { status: number; body: unknown }
): Promise<Stub> {
  const stub: Stub = { drafts: [], queue: [] };
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
    const path = new URL(req.url()).pathname;
    if (path === "/api/semantic/fragments") return json(route, 200, [FRAGMENT]);
    if (path === `/api/semantic/fragments/${FRAGMENT_ID}`) return json(route, 200, FRAGMENT);
    const edgeMatch = path.match(/^\/api\/semantic\/edges\/([^/]+)$/);
    if (edgeMatch && EDGES[edgeMatch[1]!]) return json(route, 200, EDGES[edgeMatch[1]!]);
    if (path === "/api/semantic/drafts" && req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      stub.drafts.push(body);
      const answer = onDraft(body, stub);
      return json(route, answer.status, answer.body);
    }
    if (path === "/api/governance/review-queue") return json(route, 200, stub.queue);
    return json(route, 404, { detail: `not stubbed: ${req.method()} ${path}` });
  });
  return stub;
}

async function composeDraft(page: Page) {
  await page.goto("/workbench");
  await page.getByRole("button", { name: /deployment_latency_attribution/ }).click();
  const composer = page.getByTestId("fragment-draft-composer");
  const boxes = composer.getByRole("checkbox");
  await expect(boxes).toHaveCount(2);
  // Keyboard, not the mouse: the picker is operable without a pointer.
  for (const b of await boxes.all()) {
    await b.focus();
    await page.keyboard.press("Space");
  }
  await expect(composer.getByTestId("draft-edge")).toHaveCount(2);
  await composer.getByLabel(/fragment name/i).fill("latency_to_conversion");
  return composer;
}

test("propose a fragment, see the Guardian approve it, and find it queued for approval", async ({ page }) => {
  const stub = await stubBackend(page, (body, s) => {
    s.queue.push({
      id: DRAFT_ID,
      object_type: "GraphFragment",
      domain: "devops",
      name: (body["payload"] as { name: string }).name,
      approval_status: "DRAFT",
      created_at: "2026-09-26T10:00:00Z",
    });
    return {
      status: 201,
      body: {
        object_id: DRAFT_ID,
        object_type: "GraphFragment",
        guardian_verdict: "APPROVED",
        audit_entry_id: AUDIT_ID,
        cynefin_domain: body["cynefin_domain"],
        domain_source: "declared by dev-user-001",
        message: "GraphFragment draft submitted. Awaiting Governance Officer review.",
      },
    };
  });

  const composer = await composeDraft(page);
  const submit = composer.getByRole("button", { name: /submit draft for review/i });
  await expect(submit).toBeDisabled(); // not classified yet
  await composer.getByRole("combobox", { name: /cynefin domain/i }).selectOption("COMPLICATED");
  await submit.click();

  const saved = composer.getByTestId("draft-saved");
  await expect(saved.getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "APPROVED");
  await expect(saved).toContainText("Judged in COMPLICATED");
  await expect(saved.getByTestId("draft-queue-status")).toContainText("In the governance review queue as DRAFT");

  expect(stub.drafts).toHaveLength(1);
  expect(stub.drafts[0]).toMatchObject({
    object_type: "GraphFragment",
    cynefin_domain: "COMPLICATED",
    payload: { name: "latency_to_conversion", domain: "devops", edges: [EDGE_A, EDGE_B] },
  });
});

test("a draft the Guardian blocks shows the verdict and its reason, and is not queued", async ({ page }) => {
  const reason = "PR-006: situation not classified (DISORDER); classify before proceeding";
  const stub = await stubBackend(page, () => ({
    status: 403,
    body: {
      detail: {
        error: "guardian_blocked",
        verdict: "REJECTED",
        reason,
        audit_entry_id: AUDIT_ID,
      },
    },
  }));

  const composer = await composeDraft(page);
  await composer.getByRole("combobox", { name: /cynefin domain/i }).selectOption("CLEAR");
  await composer.getByRole("button", { name: /submit draft for review/i }).click();

  const blocked = composer.getByRole("alert").filter({ hasText: "did not approve" });
  await expect(blocked.getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "REJECTED");
  await expect(blocked).toContainText(reason);
  await expect(composer.getByTestId("draft-saved")).toHaveCount(0);
  expect(stub.queue).toHaveLength(0);
});

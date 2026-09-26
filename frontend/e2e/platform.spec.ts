/**
 * Platform E2E smoke tests.
 *
 * Prerequisites:
 *   1. Backend running: cd backend && ENV=local DEV_AUTH_BYPASS=true ANTHROPIC_API_KEY=<key> \
 *                       uvicorn src.api.main:app --reload --port 8000
 *   2. Frontend running: cd frontend && npm run dev  (or let Playwright start it)
 *
 * The dev bypass token ("dev-local-token") is injected into localStorage before
 * each test via the beforeEach hook so all authenticated routes pass.
 */

import { test, expect } from "@playwright/test";

// ─── Auth setup ───────────────────────────────────────────────────────────────

const DEV_TOKEN = "dev-local-token";
const DEV_USER_ID = "dev-user-001";

// Inject the dev bypass token into the Zustand auth store (persisted in localStorage)
test.beforeEach(async ({ page }) => {
  // Navigate to any page first so localStorage is accessible
  await page.goto("/");
  await page.evaluate(
    ([token, userId]) => {
      localStorage.setItem(
        "causal-platform-auth",
        JSON.stringify({ state: { token, userId }, version: 0 })
      );
    },
    [DEV_TOKEN, DEV_USER_ID]
  );
});

// ─── 1. Backend health check ──────────────────────────────────────────────────

test("backend /health returns ok", async ({ request }) => {
  const resp = await request.get("http://localhost:8888/health");
  expect(resp.status()).toBe(200);
  const body = await resp.json() as { status: string; env: string };
  expect(body.status).toBe("ok");
  expect(body.env).toBe("local");
});

// ─── 2. Frontend loads and shows persona selector ─────────────────────────────

test("root page loads with persona selector", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Causal Platform" })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("Select your role")).toBeVisible({ timeout: 5_000 });
});

// ─── 3. Incidents page loads ──────────────────────────────────────────────────

test("incidents page loads with empty feed and submit button", async ({ page }) => {
  await page.goto("/incidents");
  await expect(page.getByText("Active Incidents")).toBeVisible({ timeout: 10_000 });
  // Submit button is always present; the feed starts empty (state is in-memory)
  await expect(page.getByTestId("submit-incident")).toBeVisible();
});

// ─── 4. Submit incident → backend triage ─────────────────────────────────────

test("submit incident calls backend and shows result", async ({ page }) => {
  // This test requires the backend to be running
  test.slow(); // Allow extra time for AI pipeline

  await page.goto("/incidents");

  // Open the submit form
  await page.getByTestId("submit-incident").click();

  // Fill in the form
  await page.getByLabel("Title").fill("High p99 latency on payment-api after deployment");
  await page.getByLabel("Affected Services").fill("payment-api, checkout-service");
  await page.getByLabel("Severity").selectOption("P1");

  // Submit — ThinkingIndicator should appear within 150ms (UX contract)
  await page.getByRole("button", { name: "Submit Incident" }).click();

  // ThinkingIndicator should show immediately
  await expect(page.getByText(/Classifying domain|Analyzing causal/i)).toBeVisible({
    timeout: 500, // Must appear within 500ms (UIX contract: 150ms)
  });

  // Wait for result — backend AI pipeline runs (up to 30s). First submission gets INC-001.
  await expect(page.getByText(/INC-001/).first()).toBeVisible({ timeout: 30_000 });
});

// ─── 5. Analyst workbench loads ───────────────────────────────────────────────

test("analyst workbench loads with fragment library", async ({ page }) => {
  await page.goto("/analyst");
  await expect(page.getByText("Fragment Library")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("session-form")).toBeVisible();
  await expect(page.getByTestId("run-session-button")).toBeVisible();
});

// ─── 6. Analyst runs a session through the backend ───────────────────────────

test("analyst session run returns domain and explanation", async ({ page }) => {
  test.slow();

  await page.goto("/analyst");

  // Type a problem description
  await page.getByLabel("Problem description").fill(
    "High latency on checkout service after deployment of payment-api v2.3"
  );

  // Run the analysis
  await page.getByTestId("run-session-button").click();

  // ThinkingIndicator must appear immediately
  await expect(page.getByText(/Classifying domain|Running causal/i)).toBeVisible({
    timeout: 500,
  });

  // Wait for result
  await expect(page.getByText(/Analysis:/i)).toBeVisible({ timeout: 30_000 });
});

// ─── 7. Governance page loads with review queue ───────────────────────────────

test("governance page loads review queue", async ({ page }) => {
  await page.goto("/governance");
  await expect(page.getByRole("heading", { name: "Review Queue" })).toBeVisible({
    timeout: 10_000,
  });
  // Helper copy that always renders (independent of queue contents)
  await expect(
    page.getByText(/draft graph changes awaiting approval/i)
  ).toBeVisible({ timeout: 5_000 });
});

// ─── 8. Engineer page loads with connector health ─────────────────────────────

test("engineer page loads connector health dashboard", async ({ page }) => {
  await page.goto("/engineer");
  await expect(page.getByText("Platform Health")).toBeVisible({ timeout: 10_000 });
  // Connector status section
  await expect(page.getByText(/connectors healthy/i)).toBeVisible({ timeout: 5_000 });
});

// ─── 9. Executive page loads manager dashboard ────────────────────────────────

test("executive page loads manager dashboard", async ({ page }) => {
  await page.goto("/executive");
  await expect(page.getByText("Manager Dashboard")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Sessions this week/i)).toBeVisible({ timeout: 5_000 });
});

// ─── 10. Auth: missing token returns 401 ─────────────────────────────────────

test("API returns 401 without auth token", async ({ request }) => {
  const resp = await request.post("http://localhost:8888/api/v1/session/run", {
    data: { description: "test" },
    // No Authorization header
  });
  expect(resp.status()).toBe(401);
});

// ─── 11. Intervention approve endpoint records audit_log entry ───────────────

test("POST /interventions/{id}/approve records an audit entry", async ({ request }) => {
  const interventionId = `int-test-${Date.now()}`;
  const resp = await request.post(
    `http://localhost:8888/api/v1/interventions/${interventionId}/approve`,
    {
      headers: { Authorization: "Bearer dev-local-token" },
      data: {
        reason: "playwright smoke: approval path",
        incident_id: "INC-TEST",
        intervention_title: "Smoke test intervention",
      },
    }
  );
  expect(resp.status()).toBe(200);
  const body = (await resp.json()) as { intervention_id: string; decision: string; audit_log_id: string };
  expect(body.intervention_id).toBe(interventionId);
  expect(body.decision).toBe("APPROVED");
  expect(body.audit_log_id).toMatch(/^[0-9a-f-]{36}$/);
});

test("POST /interventions/{id}/reject records an audit entry", async ({ request }) => {
  const interventionId = `int-test-${Date.now()}-rej`;
  const resp = await request.post(
    `http://localhost:8888/api/v1/interventions/${interventionId}/reject`,
    {
      headers: { Authorization: "Bearer dev-local-token" },
      data: { reason: "playwright smoke: rejection path" },
    }
  );
  expect(resp.status()).toBe(200);
  const body = (await resp.json()) as { decision: string };
  expect(body.decision).toBe("REJECTED");
});

// ─── 12. End-to-end intervention approve from the UI ─────────────────────────

test("approving an intervention from the UI hits the backend", async ({ page }) => {
  test.slow();
  await page.goto("/incidents");

  // Submit a triage session so an InterventionCard is rendered
  await page.getByTestId("submit-incident").click();
  await page.getByLabel("Title").fill("Approve-flow smoke: cart abandonment after rollout");
  await page.getByLabel("Affected Services").fill("checkout-service, payment-api");
  await page.getByLabel("Severity").selectOption("P1");
  await page.getByRole("button", { name: "Submit Incident" }).click();

  // Wait until at least one InterventionCard renders (backend pipeline returned recommendations)
  const card = page.getByTestId("intervention-card").first();
  await expect(card).toBeVisible({ timeout: 30_000 });

  // Capture the network request the Approve click triggers
  const approveRequestPromise = page.waitForRequest((req) =>
    req.url().includes("/api/v1/interventions/") && req.url().endsWith("/approve")
  );

  // Two-step approve: first click reveals the reason input, second click confirms
  const approveBtn = card.getByTestId("approve-button");
  await approveBtn.click();
  await page.getByLabel(/Approval reason/i).fill("Rollback validated — auto-test");
  await approveBtn.click();

  const approveRequest = await approveRequestPromise;
  expect(approveRequest.method()).toBe("POST");
  const response = await approveRequest.response();
  expect(response?.status()).toBe(200);
});

// ─── 13. Intervention approve is idempotent ──────────────────────────────────

test("approving the same intervention twice is idempotent", async ({ request }) => {
  const interventionId = `int-idem-${Date.now()}`;
  const url = `http://localhost:8888/api/v1/interventions/${interventionId}/approve`;
  const headers = { Authorization: "Bearer dev-local-token" };

  const first = await request.post(url, {
    headers,
    data: { reason: "first call", incident_id: "INC-IDEM-E2E" },
  });
  expect(first.status()).toBe(200);
  const firstBody = (await first.json()) as { audit_log_id: string; already_decided: boolean };
  expect(firstBody.already_decided).toBe(false);

  const second = await request.post(url, {
    headers,
    data: { reason: "second call" },
  });
  expect(second.status()).toBe(200);
  const secondBody = (await second.json()) as { audit_log_id: string; already_decided: boolean };
  expect(secondBody.already_decided).toBe(true);
  // Same audit_log_id — no duplicate row was written
  expect(secondBody.audit_log_id).toBe(firstBody.audit_log_id);
});

test("rejecting an already-approved intervention preserves the approval", async ({ request }) => {
  const interventionId = `int-flip-${Date.now()}`;
  const headers = { Authorization: "Bearer dev-local-token" };

  const approveResp = await request.post(
    `http://localhost:8888/api/v1/interventions/${interventionId}/approve`,
    { headers, data: { reason: "approved first" } }
  );
  expect(approveResp.status()).toBe(200);
  const approveBody = (await approveResp.json()) as { decision: string; audit_log_id: string };

  const rejectResp = await request.post(
    `http://localhost:8888/api/v1/interventions/${interventionId}/reject`,
    { headers, data: { reason: "trying to flip" } }
  );
  expect(rejectResp.status()).toBe(200);
  const rejectBody = (await rejectResp.json()) as { decision: string; already_decided: boolean; audit_log_id: string };
  expect(rejectBody.decision).toBe("APPROVED"); // immutable — original verdict wins
  expect(rejectBody.already_decided).toBe(true);
  expect(rejectBody.audit_log_id).toBe(approveBody.audit_log_id);
});

// ─── 14. GET /interventions/decisions returns prior decisions ────────────────

test("GET /interventions/decisions filters by incident_id", async ({ request }) => {
  const incidentId = `INC-DEC-${Date.now()}`;
  const headers = { Authorization: "Bearer dev-local-token" };

  // Record two decisions tied to the same incident
  await request.post(
    `http://localhost:8888/api/v1/interventions/int-A-${Date.now()}/approve`,
    { headers, data: { reason: "A approved", incident_id: incidentId } }
  );
  await request.post(
    `http://localhost:8888/api/v1/interventions/int-B-${Date.now()}/reject`,
    { headers, data: { reason: "B rejected", incident_id: incidentId } }
  );

  const resp = await request.get(
    `http://localhost:8888/api/v1/interventions/decisions?incident_id=${incidentId}`,
    { headers }
  );
  expect(resp.status()).toBe(200);
  const decisions = (await resp.json()) as Array<{ decision: string; incident_id: string | null }>;
  expect(decisions.length).toBeGreaterThanOrEqual(2);
  expect(decisions.every((d) => d.incident_id === incidentId)).toBe(true);
  expect(decisions.some((d) => d.decision === "APPROVED")).toBe(true);
  expect(decisions.some((d) => d.decision === "REJECTED")).toBe(true);
});

// ─── 15. Seeded incidents are listed via GET /api/v1/incidents/ ──────────────

test("GET /incidents returns seeded records with merged verdicts", async ({ request }) => {
  const resp = await request.get("http://localhost:8888/api/v1/incidents/", {
    headers: { Authorization: "Bearer dev-local-token" },
  });
  expect(resp.status()).toBe(200);
  const incidents = (await resp.json()) as Array<{
    id: string;
    severity: string;
    proposed_interventions: Array<{ guardianVerdict: string }>;
  }>;
  expect(incidents.length).toBeGreaterThanOrEqual(5);

  const inc001 = incidents.find((i) => i.id === "INC-SEED-001");
  expect(inc001).toBeDefined();
  // The seeded "Rollback payment-service" intervention was APPROVED via audit_log,
  // so the merge layer must surface it.
  expect(inc001!.proposed_interventions.some((i) => i.guardianVerdict === "APPROVED")).toBe(true);
});

test("GET /incidents/{id} returns single incident with verdict merge", async ({ request }) => {
  const resp = await request.get("http://localhost:8888/api/v1/incidents/INC-SEED-002", {
    headers: { Authorization: "Bearer dev-local-token" },
  });
  expect(resp.status()).toBe(200);
  const incident = (await resp.json()) as {
    id: string;
    proposed_interventions: Array<{ guardianVerdict: string }>;
  };
  expect(incident.id).toBe("INC-SEED-002");
  // INC-SEED-002 has one APPROVED + one REJECTED seeded decision
  const verdicts = incident.proposed_interventions.map((i) => i.guardianVerdict);
  expect(verdicts).toContain("APPROVED");
  expect(verdicts).toContain("REJECTED");
});

test("GET /incidents/{id} returns 404 for unknown id", async ({ request }) => {
  const resp = await request.get("http://localhost:8888/api/v1/incidents/INC-DOES-NOT-EXIST", {
    headers: { Authorization: "Bearer dev-local-token" },
  });
  expect(resp.status()).toBe(404);
});

// ─── 16. Incidents page renders seeded records on first load (no submission needed) ──

test("incidents page shows seeded incidents on mount", async ({ page }) => {
  await page.goto("/incidents");
  // Seeded INC-SEED-001 should appear in the feed within a few seconds (after API load)
  await expect(page.getByText("INC-SEED-001").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("INC-SEED-002").first()).toBeVisible({ timeout: 5_000 });
});

// ─── 17. Approve verdict persists across page reload ─────────────────────────

test("an intervention decision survives a page reload", async ({ page, request }) => {
  // Record an APPROVED decision on a known seeded incident's intervention via API
  const interventionId = `int-1-seed-session-002`; // first intervention of INC-SEED-002
  const reloadIncidentId = "INC-SEED-002";

  // Wipe any existing decision is impossible (audit_log is append-only), so reuse it.
  // Idempotency means the prior verdict is returned — but the GET merge will surface APPROVED either way.
  await request.post(
    `http://localhost:8888/api/v1/interventions/${interventionId}/approve`,
    {
      headers: { Authorization: "Bearer dev-local-token" },
      data: { reason: "reload-survival test", incident_id: reloadIncidentId },
    }
  );

  await page.goto("/incidents");
  await expect(page.getByText(reloadIncidentId).first()).toBeVisible({ timeout: 10_000 });
  await page.getByText(reloadIncidentId).first().click();

  // Select the incident to render its InterventionCards
  const approvedBadge = page.getByTestId("intervention-card").first().getByText(/APPROVED/i);
  await expect(approvedBadge).toBeVisible({ timeout: 5_000 });

  // Now reload — the verdict must still be visible because it's persisted in audit_log
  await page.reload();
  await expect(page.getByText(reloadIncidentId).first()).toBeVisible({ timeout: 10_000 });
  await page.getByText(reloadIncidentId).first().click();
  await expect(
    page.getByTestId("intervention-card").first().getByText(/APPROVED/i)
  ).toBeVisible({ timeout: 5_000 });
});

// ─── 18. Governance review queue surfaces seeded DRAFT/UNDER_REVIEW items ────

test("governance review-queue returns seeded items", async ({ request }) => {
  const resp = await request.get("http://localhost:8888/api/governance/review-queue", {
    headers: { Authorization: "Bearer dev-local-token" },
  });
  expect(resp.status()).toBe(200);
  const items = (await resp.json()) as Array<{ approval_status: string; name: string }>;
  // Seed script creates 4 fragments + 2 entities — at minimum 1 of each should be in queue
  expect(items.length).toBeGreaterThan(0);
  expect(items.some((i) => i.approval_status === "DRAFT" || i.approval_status === "UNDER_REVIEW")).toBe(true);
});

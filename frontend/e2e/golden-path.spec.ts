/**
 * The golden path, in a browser against a live backend (septa-97): upload data
 * simulated with a known effect, have the graph checked, estimate, see an
 * interval that covers the effect, decide, and verify the ledger.
 *
 * WHAT THIS PROVES. The real Next.js app in Chromium, talking to the real
 * FastAPI backend (ENV=local, DEV_AUTH_BYPASS=true, a fresh SQLite file), which
 * calls the real kernel. Nothing is stubbed. The effect is set here, in the
 * structural model below, so the interval is judged against the truth and not
 * against what the code returned last time.
 *
 * Preconditions: E2E_API_BASE_URL points at a backend started as above; the
 * gate's frontend-e2e step (.gnomon/ci.sh) starts one, and the frontend, and
 * runs only this file. By hand:
 *
 *   SQLITE_PATH=/tmp/e2e.db ENV=local DEV_AUTH_BYPASS=true \
 *     CORS_ALLOWED_ORIGINS=http://localhost:3107 \
 *     backend/.venv/bin/uvicorn src.api.main:app --app-dir backend --port 49299
 *   cd frontend && E2E_PORT=3107 E2E_API_BASE_URL=http://localhost:49299 \
 *     npx playwright test e2e/golden-path.spec.ts
 *
 * Postconditions: the graph is identified with Z as the adjustment set; the
 * analysis is a result whose 95% interval contains TRUE_EFFECT, and the page
 * shows that interval; the decision is recorded and appears in the ledger; the
 * ledger's chain verifies from the stored rows.
 * Undefined here: the verdict itself (the policy's, tested in causal-decision's
 * D0 gate), and the sequence number (the database may hold earlier entries).
 */

import { expect, test } from "@playwright/test";

const TRUE_EFFECT = 3.0;
const GRAPH = "Z -> X; Z -> Y; X -> Y";
const ACTION = "roll out X to everyone (golden path)";

/** mulberry32: a seeded uniform generator, so the data are the same every run. */
function uniform(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Z ~ N(0,1); X ~ Bernoulli(logistic(0.8 Z)); Y = TRUE_EFFECT X + 1.5 Z + N(0,1).
 * Z confounds X and Y, so the naive difference is biased and adjusting for Z is
 * what recovers TRUE_EFFECT. */
function simulatedCsv(n = 2000, seed = 7): string {
  const u = uniform(seed);
  const normal = () => {
    const r = Math.sqrt(-2 * Math.log(1 - u()));
    return r * Math.cos(2 * Math.PI * u());
  };
  const rows = ["Z,X,Y"];
  for (let i = 0; i < n; i++) {
    const z = normal();
    const x = u() < 1 / (1 + Math.exp(-0.8 * z)) ? 1 : 0;
    const y = TRUE_EFFECT * x + 1.5 * z + normal();
    rows.push(`${z.toFixed(6)},${x},${y.toFixed(6)}`);
  }
  return rows.join("\n") + "\n";
}

test.beforeEach(async ({ page }) => {
  // The dev-bypass token, in the store the API client reads, before any page script runs.
  await page.addInitScript(() => {
    localStorage.setItem(
      "causal-platform-auth",
      JSON.stringify({ state: { token: "dev-local-token", userId: "dev-user-001" }, version: 0 })
    );
  });
});

test("upload, check, estimate, decide and verify the ledger", async ({ page }) => {
  const query = new URLSearchParams({ graph: GRAPH, treatment: "X", outcome: "Y" });
  await page.goto(`/analyze?${query.toString()}`);

  // 1 · Upload.
  await page.getByLabel("CSV file").setInputFiles({
    name: "golden-path.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(simulatedCsv()),
  });
  await expect(page.getByText("golden-path.csv")).toBeVisible();

  // 2 · The graph is checked by the backend, and the effect identified by adjusting for Z.
  // (The glossary term inside the sentence carries hidden text, so match its tail.)
  const identified = page.getByText(/from this graph, adjusting for Z\./);
  await expect(identified).toBeVisible({ timeout: 30_000 });
  await expect(identified).toContainText("The effect is");

  // 3 · Estimate. The interval is read from the response the page rendered.
  const ran = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/analysis/run") && r.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Estimate the effect" }).click();
  const runResponse = await ran;
  expect(runResponse.status()).toBe(200);
  const run = (await runResponse.json()) as {
    outcome: { kind: string; primary?: { point: number; ci: { lo: number; hi: number } | null } };
  };
  expect(run.outcome.kind).toBe("result");
  const ci = run.outcome.primary?.ci;
  expect(ci, "a result carries an interval").toBeTruthy();
  expect(ci!.lo).toBeLessThanOrEqual(TRUE_EFFECT);
  expect(ci!.hi).toBeGreaterThanOrEqual(TRUE_EFFECT);
  expect(ci!.lo, "the interval excludes zero: the effect is detected").toBeGreaterThan(0);
  // The page shows that interval: the chart's accessible name carries the numbers.
  const chart = page.getByRole("img", { name: /^Estimate .*, 95% interval .* to .*$/ });
  await expect(chart).toBeVisible();
  const shown = ((await chart.getAttribute("aria-label")) ?? "").match(
    /95% interval (-?[\d.]+) to (-?[\d.]+)/
  );
  expect(shown, "the chart names its interval").toBeTruthy();
  expect(Math.abs(Number(shown![1]) - ci!.lo)).toBeLessThan(0.01);
  expect(Math.abs(Number(shown![2]) - ci!.hi)).toBeLessThan(0.01);

  // 4 · Decide and record.
  await page.getByLabel("Action you'd take").fill(ACTION);
  await page.getByLabel("increase").check();
  await page.getByLabel("Smallest worthwhile effect").fill("1");
  const decided = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/decisions") && r.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Decide and record" }).click();
  const decideResponse = await decided;
  expect(decideResponse.status()).toBe(200);
  const decision = (await decideResponse.json()) as {
    verdict: string;
    record: { sequence: number; entry_digest: string };
  };
  expect(["act", "act_with_approval", "experiment", "refuse"]).toContain(decision.verdict);
  await expect(page.getByText(`entry #${decision.record.sequence}`)).toBeVisible();

  // 5 · The ledger lists it, and its chain verifies from the stored rows.
  await page.goto("/decisions");
  await expect(page.getByTestId("decision-row").filter({ hasText: ACTION })).toBeVisible({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "Verify the chain" }).click();
  await expect(page.getByTestId("chain-report")).toContainText(/Consistent: \d+ entries recompute\./, {
    timeout: 15_000,
  });
});

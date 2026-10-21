import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the Causal Platform frontend.
 *
 * Assumes:
 *   - Frontend: http://localhost:3000 (Next.js dev server), or E2E_PORT
 *   - Backend:  E2E_API_BASE_URL (uvicorn, ENV=local, DEV_AUTH_BYPASS=true), for
 *     specs that talk to one; the gate's frontend-e2e step (.gnomon/ci.sh) starts
 *     it and a production build for e2e/golden-path.spec.ts
 *
 * Run: npx playwright test
 * UI:  npx playwright test --ui
 *
 * E2E_PORT moves the frontend off 3000 (the dev server Playwright starts, and
 * the base URL); E2E_API_BASE_URL sets NEXT_PUBLIC_API_BASE_URL for it. Specs
 * that stub the backend with page.route (workbench-authoring.spec.ts) need
 * neither a backend nor a real API URL. E2E_PROD=1 serves a production build
 * (`next build` first) with `next start` instead of the dev server, which needs
 * no file watchers -- the dev server fails on a host at its inotify limit.
 */

const PORT = Number(process.env["E2E_PORT"] ?? 3000);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // Sequential to avoid session cache collisions
  forbidOnly: !!process.env["CI"],
  retries: process.env["CI"] ? 2 : 0,
  workers: 1,
  reporter: process.env["CI"] ? "html" : [["list"]],

  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Inject dev bypass token into every browser context
    storageState: undefined,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Start frontend dev server automatically if not already running
  webServer: {
    command: process.env["E2E_PROD"]
      ? `npm run start -- --port ${PORT}`
      : `npm run dev -- --port ${PORT}`,
    url: BASE_URL,
    env: process.env["E2E_API_BASE_URL"]
      ? { NEXT_PUBLIC_API_BASE_URL: process.env["E2E_API_BASE_URL"] }
      : {},
    reuseExistingServer: !process.env["CI"],
    timeout: 60_000,
  },
});

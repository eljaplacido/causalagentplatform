import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the Causal Platform frontend.
 *
 * Assumes:
 *   - Frontend: http://localhost:3000 (Next.js dev server)
 *   - Backend:  http://localhost:8888 (uvicorn, ENV=local, DEV_AUTH_BYPASS=true)
 *
 * Run: npx playwright test
 * UI:  npx playwright test --ui
 */

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // Sequential to avoid session cache collisions
  forbidOnly: !!process.env["CI"],
  retries: process.env["CI"] ? 2 : 0,
  workers: 1,
  reporter: "html",

  use: {
    baseURL: "http://localhost:3000",
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
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env["CI"],
    timeout: 60_000,
  },
});

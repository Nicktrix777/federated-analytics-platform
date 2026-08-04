import { defineConfig, devices } from "@playwright/test";

// The app is reached by compose service name when the runner joins federation-net
// (qa/run.sh sets BASE_URL=http://frontend). Falls back to the host-published port
// so the same specs run with `BASE_URL=http://localhost:3000 npx playwright test`.
const BASE_URL = process.env.BASE_URL || "http://frontend";

export default defineConfig({
  testDir: "./ux/tests",
  outputDir: "./artifacts/test-results",
  // AI generation flows stream for up to ~5 min; give tests room but keep assertions snappy.
  timeout: 240_000,
  expect: { timeout: 15_000 },
  // Serial: the app is a single LLM-backed instance; parallel generations would fight
  // rate limits and make timings meaningless. Also avoids react-grid-layout drag flakiness.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [
    ["list"],
    ["html", { outputFolder: "artifacts/html-report", open: "never" }],
    ["json", { outputFile: "artifacts/results.json" }],
  ],
  use: {
    baseURL: BASE_URL,
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: "on",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});

import { defineConfig } from "@playwright/test";

// Browser-only e2e for the thinking + custom-size feature.  Spec assumes
// the caller has both backend (port 18901) and frontend (port 5174) up
// before invoking ``pnpm run test:e2e`` — the runner script under
// tests-e2e/_run.sh handles that wiring.

export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5174",
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
  },
});

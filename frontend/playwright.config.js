import { defineConfig, devices } from "@playwright/test";

// Playwright config scoped to the Create-page sticky persistence tests.
// We boot the Vite dev server, mock all backend API calls inside each
// test, and assert against localStorage / IndexedDB directly.
export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./tests/e2e/.results",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "tests/e2e/.report" }]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm exec vite --port=5173 --host=127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});

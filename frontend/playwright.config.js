import { defineConfig, devices } from "@playwright/test";

const PORT_FE = Number(process.env.E2E_FE_PORT || 5173);
const PORT_BE = Number(process.env.E2E_BE_PORT || 8000);
const FE_BASE = process.env.E2E_BASE_URL || `http://localhost:${PORT_FE}`;
const BE_BASE = process.env.E2E_API_BASE || `http://127.0.0.1:${PORT_BE}`;

const SHARED_ENV = {
  ADMIN_USERNAME: "admin",
  ADMIN_PASSWORD: "test-admin-password",
  JWT_SECRET: "playwright-e2e-secret-not-for-production",
  DB_URL: "sqlite+aiosqlite:///./.e2e-data/txt2img.db",
  CORS_ALLOW_ORIGINS: FE_BASE,
};

export default defineConfig({
  testDir: "./tests/e2e/specs",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: FE_BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.E2E_NO_SERVER
    ? undefined
    : [
        {
          command: [
            "rm -rf .e2e-data && mkdir -p .e2e-data &&",
            "../.venv/bin/python -m uvicorn app.main:app",
            `--host 127.0.0.1 --port ${PORT_BE}`,
          ].join(" "),
          cwd: "../backend",
          url: `${BE_BASE}/api/health`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
          env: SHARED_ENV,
        },
        {
          command: `npx vite --port ${PORT_FE} --strictPort`,
          url: FE_BASE,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
          env: { VITE_API_BASE: BE_BASE },
        },
      ],
});

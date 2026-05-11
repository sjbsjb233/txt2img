// Playwright config for the create-page parameter-parity e2e suite.
//
// Two helper servers run alongside the test:
//
//   - scripts/playwright_test/boot_env.py boots the txt2img backend on
//     127.0.0.1:18900 and a request-capture upstream on 18890, then seeds
//     the OpenAI / Gemini providers + an e2e user.
//   - A tiny static server serves the pre-built frontend on 5174 with an
//     SPA fallback so /login, /create etc. all resolve to index.html.
//
// VITE_API_BASE was baked into the bundle at build time so the SPA
// already knows to talk to 127.0.0.1:18900.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    headless: true,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  webServer: [
    {
      command:
        ".venv/bin/python -u -m scripts.playwright_test.boot_env",
      cwd: "..",
      url: "http://127.0.0.1:18900/api/health",
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command:
        ".venv/bin/python -u scripts/playwright_test/static_serve.py 5174 frontend/dist",
      cwd: "..",
      url: "http://127.0.0.1:5174/index.html",
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});

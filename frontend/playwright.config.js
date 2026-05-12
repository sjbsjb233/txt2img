// Playwright E2E runner config.
//
// Run with: pnpm run test:e2e
// (which sets PLAYWRIGHT_BROWSERS_PATH=../.playwright-browsers so the
// chromium binary lives at the worktree root, not under node_modules.)
//
// Per-spec opt-ins are not used — the defaults below already cover what
// we need (auto trace / screenshot / video on failure). Override per-test
// only when a spec needs different behaviour.

import { defineConfig, devices } from "@playwright/test";

// Where the app under test is served. Override with E2E_BASE_URL when
// running against a different port / a deployed environment.
const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:5180";

export default defineConfig({
  testDir: "./tests",

  // Don't let an accidental `.only` reach CI.
  forbidOnly: !!process.env.CI,

  // One retry locally, two on CI — Playwright traces give us enough
  // signal that "flaky on retry" is still actionable.
  retries: process.env.CI ? 2 : 1,

  // Cap parallelism: leaves the dev box responsive while the suite runs.
  // CI environments can crank this up via PLAYWRIGHT_WORKERS.
  workers: process.env.PLAYWRIGHT_WORKERS
    ? Number(process.env.PLAYWRIGHT_WORKERS)
    : process.env.CI
    ? 2
    : undefined,

  // HTML reporter is enough; never auto-open so it doesn't hijack the
  // terminal during a long suite. Open it manually via
  // `pnpm exec playwright show-report`.
  reporter: [["list"], ["html", { open: "never" }]],

  // Generated artefacts (trace.zip, screenshots, videos) all land here.
  outputDir: "./test-results",

  use: {
    baseURL,

    // Always-on artefacts. Worktrees are short-lived here — by the time
    // a developer wants to look at "why did this run go red", the disk
    // they ran on is already deleted. Capturing everything every run is
    // the only way to keep that signal.
    //
    // `trace: 'on'`, `screenshot: 'on'`, `video: 'on'` are the strongest
    // settings Playwright offers; switch back to `*-on-failure` only if
    // disk pressure becomes a real problem.
    //
    // Note: runner-level `screenshot: 'on'` captures *one image at end
    // of each test*. For per-step screenshots inside a spec, call
    // `await page.screenshot(...)` explicitly.
    trace: "on",
    screenshot: "on",
    video: "on",

    // Force headless. Default is already headless when no display is
    // attached, but spelling it out keeps behaviour identical whether
    // a spec runs on CI, in a tmux pane, or from an IDE on macOS.
    headless: true,
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});

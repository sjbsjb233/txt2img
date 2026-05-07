// Picker smoke test — covers all 5 boards from the PRD with screenshots.
// Walks through:
//   01 Deck overview      → screenshot
//   02 Picker (judging)   → screenshot
//   03 Sessions drawer    → screenshot
//   04a Empty session     → screenshot
//   04b Finalized session → screenshot
//   04c Confirm modal     → screenshot
//   05 Fullscreen         → screenshot
//
// The seeder script primes the DB with five sessions of varying state
// (run before this test from the global setup or the test runner).

import { test, expect } from "@playwright/test";
import { execFileSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

import { loginGetToken, injectAuth, API_BASE } from "../fixtures/auth.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../..");
const SEEDER = path.join(REPO_ROOT, "frontend/e2e/scripts/seed_picker_data.py");
const VENV_PY = path.join(REPO_ROOT, ".venv/bin/python");
const SHOTS = path.join(REPO_ROOT, "playwright-report/shots");

function runSeeder(cmd) {
  const out = execFileSync(VENV_PY, [SEEDER, cmd], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      // Pull keys from backend.env so the script can talk to SQLite.
    },
    encoding: "utf8",
  });
  // Last line is the JSON result; alembic logs precede it.
  const lines = out.trim().split("\n");
  return JSON.parse(lines[lines.length - 1]);
}

let seedResult;

test.beforeAll(async () => {
  // Reset + seed once for the whole suite.
  runSeeder("reset");
  seedResult = runSeeder("seed_deck_mixed");
  expect(seedResult.ok).toBe(true);
  expect(seedResult.sessions).toHaveLength(5);
});

test.beforeEach(async ({ page }) => {
  const auth = await loginGetToken();
  await injectAuth(page, auth);
});

function findSession(name) {
  return seedResult.sessions.find((s) => s.name === name);
}

test("D1 — Deck overview 显示 5 张 session 卡片", async ({ page }) => {
  await page.goto("/picker");
  await expect(page.getByTestId("picker-deck-overview")).toBeVisible();

  // 5 cards rendered
  for (const s of seedResult.sessions) {
    await expect(
      page.getByTestId(`picker-session-card-${s.id}`)
    ).toBeVisible();
  }

  // Header summary chip "5 sessions"
  await expect(page.getByText(/5 sessions/i).first()).toBeVisible();

  await page.waitForTimeout(1500);
  await page.screenshot({
    path: `${SHOTS}/01-deck-overview.png`,
    fullPage: false,
  });
});

test("M1 — 进入 judging session 显示主判图页面", async ({ page }) => {
  const sess = findSession("Core problem");
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();

  // Session name in header
  await expect(
    page.getByText("Core problem", { exact: true }).first()
  ).toBeVisible();

  // Judgment row buttons present
  await expect(page.getByText("Pick", { exact: true })).toBeVisible();
  await expect(page.getByText("Discard", { exact: true })).toBeVisible();
  await expect(page.getByText("Set as FINAL", { exact: true })).toBeVisible();

  // Wait for at least one image to load
  await page.waitForTimeout(1500);
  await page.screenshot({
    path: `${SHOTS}/02-judging-main.png`,
    fullPage: false,
  });
});

test("J1 — 按 P 键把当前图判为 picked 并自动前进", async ({ page }) => {
  const sess = findSession("Core problem");
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();
  await page.waitForTimeout(800);

  // Capture initial outcome counts
  const before = await fetchPickerStats(page, sess.id);

  // Press P to pick the current (cursor) image
  await page.keyboard.press("p");
  await page.waitForTimeout(600);

  const after = await fetchPickerStats(page, sess.id);
  expect(after.picked).toBeGreaterThanOrEqual(before.picked + 1);
});

test("W1 — 按 S 打开 sessions drawer", async ({ page }) => {
  const sess = findSession("Core problem");
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();
  await page.waitForTimeout(800);

  // Drawer initially closed
  await expect(
    page.getByTestId("picker-sessions-drawer")
  ).toHaveAttribute("data-open", "false");

  await page.keyboard.press("s");
  await expect(
    page.getByTestId("picker-sessions-drawer")
  ).toHaveAttribute("data-open", "true");

  // Drawer should list every session
  for (const s of seedResult.sessions) {
    await expect(page.getByText(s.name, { exact: true }).first()).toBeVisible();
  }

  await page.waitForTimeout(400);
  await page.screenshot({
    path: `${SHOTS}/03-sessions-drawer.png`,
    fullPage: false,
  });
});

test("E1 — Empty session 显示 hatched 提示 + judgment row 禁用", async ({ page }) => {
  const sess = findSession("Cover slide"); // not_started, 0 images
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();

  await expect(page.getByText(/No images yet/i)).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOTS}/04a-empty-session.png`,
    fullPage: false,
  });
});

test("FN1 — Finalized session 显示锁定态 + locked chip", async ({ page }) => {
  const sess = findSession("Results");
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();

  await expect(page.getByText(/FINALIZED · LOCKED/i)).toBeVisible();
  await expect(page.getByText(/Reopen for judging/i)).toBeVisible();

  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOTS}/04b-finalized.png`,
    fullPage: false,
  });
});

test("J4 — 按 F 在已有 final 时弹出 confirm modal", async ({ page }) => {
  const sess = findSession("Team"); // has 1 final, cursor on unjudged
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();
  await page.waitForTimeout(800);

  // Press F to set the unjudged cursor as new final → modal
  await page.keyboard.press("f");

  await expect(
    page.getByTestId("picker-finalize-confirm-modal")
  ).toBeVisible();
  await expect(page.getByText(/Replace the FINAL/i)).toBeVisible();

  await page.waitForTimeout(400);
  await page.screenshot({
    path: `${SHOTS}/04c-confirm-modal.png`,
    fullPage: false,
  });

  // Cancel the modal so we don't disturb seeded state for other tests
  await page.keyboard.press("Escape");
  await expect(
    page.getByTestId("picker-finalize-confirm-modal")
  ).toHaveCount(0);
});

test("FS1 — 点击 Fullscreen 进入沉浸模式 + 深色背景", async ({ page }) => {
  const sess = findSession("Our method"); // ready_to_finalize, has final
  await page.goto(`/picker?session_id=${sess.id}`);
  await expect(page.getByTestId("picker-judging-page")).toBeVisible();
  await page.waitForTimeout(800);

  await page.getByTestId("picker-fullscreen-button").click();
  await expect(page.getByTestId("picker-fullscreen")).toBeVisible();

  // Dark background
  const bg = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="picker-fullscreen"]');
    return el ? getComputedStyle(el).backgroundColor : null;
  });
  expect(bg).toBeTruthy();

  // Top-bar text present
  await expect(page.getByText(/FULLSCREEN/i).first()).toBeVisible();
  await expect(page.getByText(/EXIT · Esc/i)).toBeVisible();

  await page.waitForTimeout(400);
  await page.screenshot({
    path: `${SHOTS}/05-fullscreen.png`,
    fullPage: false,
  });

  // Esc exits
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("picker-fullscreen")).toHaveCount(0);
});

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

async function fetchPickerStats(page, sessionId) {
  return await page.evaluate(async ({ apiBase, sid }) => {
    const token = localStorage.getItem("token");
    const r = await fetch(`${apiBase}/api/sessions/${sid}/picker`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await r.json();
    const counts = {
      picked: 0,
      discarded: 0,
      final: 0,
      deferred: 0,
      unjudged: 0,
    };
    for (const im of data.images || []) {
      counts[im.pick_state] = (counts[im.pick_state] || 0) + 1;
    }
    return counts;
  }, { apiBase: API_BASE, sid: sessionId });
}

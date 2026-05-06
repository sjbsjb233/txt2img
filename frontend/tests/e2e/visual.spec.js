// Generates explanatory screenshots for the sticky persistence feature.
// Each test takes a single full-page shot at a key state so the
// reviewer can eyeball the behaviour at-a-glance.

import { test } from "@playwright/test";
import { MODELS, seedAuth, stubBackend, readSticky } from "./fixtures.js";

const GPT = MODELS[0];
const GEMINI = MODELS[1];
const SHOT_DIR = "tests/e2e/.shots";

test.beforeEach(async ({ page }) => {
  await seedAuth(page);
  await stubBackend(page);
});

test("00 initial create page", async ({ page }) => {
  await page.goto("/create");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOT_DIR}/00-initial.png`, fullPage: true });
});

test("01 GPT with aspect 3:2 (sticky write)", async ({ page }) => {
  await page.goto("/create");
  // GPT is the user's default; click anyway to be explicit.
  await page
    .locator(".model-strip > button", { hasText: "GPT-Image" })
    .click();
  await page
    .locator('[data-field="aspect_ratio"] button:has(div.mono:has-text("3:2"))')
    .click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOT_DIR}/01-gpt-3-2.png`, fullPage: true });
  // Print sticky to test output for visibility.
  const sticky = await readSticky(page);
  console.log(
    "[sticky after GPT 3:2]",
    JSON.stringify(sticky, null, 2)
  );
});

test("02 switch to Gemini (different defaults)", async ({ page }) => {
  await page.goto("/create");
  await page
    .locator(".model-strip > button", { hasText: "GPT-Image" })
    .click();
  await page
    .locator('[data-field="aspect_ratio"] button:has(div.mono:has-text("3:2"))')
    .click();
  await page.waitForTimeout(800);
  await page
    .locator(".model-strip > button", { hasText: "Gemini Flash" })
    .click();
  await page
    .locator('[data-field="quality"] button:has-text("hd")')
    .click();
  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOT_DIR}/02-gemini-hd.png`,
    fullPage: true,
  });
  const sticky = await readSticky(page);
  console.log(
    "[sticky after Gemini hd]",
    JSON.stringify(sticky, null, 2)
  );
});

test("03 switch back to GPT — params restored", async ({ page }) => {
  await page.goto("/create");
  await page
    .locator(".model-strip > button", { hasText: "GPT-Image" })
    .click();
  await page
    .locator('[data-field="aspect_ratio"] button:has(div.mono:has-text("3:2"))')
    .click();
  await page.waitForTimeout(800);
  await page
    .locator(".model-strip > button", { hasText: "Gemini Flash" })
    .click();
  await page.waitForTimeout(800);
  // Switch back — aspect should be 3:2 again, not Gemini's 16:9.
  await page
    .locator(".model-strip > button", { hasText: "GPT-Image" })
    .click();
  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOT_DIR}/03-back-to-gpt-3-2.png`,
    fullPage: true,
  });
});

test("04 reload with sticky — model + params survive", async ({ page }) => {
  await page.goto("/create");
  await page
    .locator(".model-strip > button", { hasText: "Gemini Flash" })
    .click();
  await page
    .locator('[data-field="aspect_ratio"] button:has(div.mono:has-text("1:1"))')
    .click();
  await page
    .locator('[data-field="quality"] button:has-text("hd")')
    .click();
  await page.fill("textarea", "this prompt will be cleared on reload");
  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOT_DIR}/04-before-reload.png`,
    fullPage: true,
  });
  await page.reload();
  await page.waitForTimeout(800);
  await page.screenshot({
    path: `${SHOT_DIR}/05-after-reload.png`,
    fullPage: true,
  });
});

test("06 Clear button — sticky kept, prompt cleared", async ({ page }) => {
  await page.goto("/create");
  await page
    .locator(".model-strip > button", { hasText: "GPT-Image" })
    .click();
  await page
    .locator('[data-field="aspect_ratio"] button:has(div.mono:has-text("3:2"))')
    .click();
  await page.fill("textarea", "this prompt will be cleared");
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.waitForTimeout(400);
  await page.screenshot({
    path: `${SHOT_DIR}/06-after-clear.png`,
    fullPage: true,
  });
});

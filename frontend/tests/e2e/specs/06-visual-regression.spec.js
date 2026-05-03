// Test plan §6.13 — visual regression baselines for the right rail.
// Screenshots auto-baseline on first run; subsequent runs diff.

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = path.resolve(__dirname, "..", "..", "..", "playwright-shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

async function shot(page, name) {
  const file = path.join(SHOT_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

test.describe("Create page · visual regression baselines", () => {

  test("openai full unlock — premium user", async ({
    premiumUserPage, bltcyRestricted, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    // SSE keeps the network busy → networkidle never settles. A small
    // settle window is enough for the param-panel React update.
    await premiumUserPage.waitForTimeout(400);
    await shot(premiumUserPage, "01-openai-premium-full");
  });

  test("openai restricted — free user (lots of greys)", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.expandAdvanced();
    await freeUserPage.waitForTimeout(400);
    await shot(freeUserPage, "02-openai-free-restricted");
  });

  test("gemini 3.1 flash — full toggles", async ({
    freeUserPage, geminiFlashFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gemini-3.1-flash-image-preview");
    await cp.expandAdvanced();
    await freeUserPage.waitForTimeout(400);
    await shot(freeUserPage, "03-gemini-flash-full");
  });

  test("model switch view — both models visible in strip", async ({
    premiumUserPage, bltcyRestricted, aliyunFull, geminiFlashFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.clickOption("size", "1536x1024");
    await cp.selectModel("gemini-3.1-flash-image-preview");
    // SSE keeps the network busy → networkidle never settles. A small
    // settle window is enough for the param-panel React update.
    await premiumUserPage.waitForTimeout(400);
    await shot(premiumUserPage, "04-after-switch-to-gemini");
  });
});

// Test plan §6.11 — tricky edge cases that surface real bugs.

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";

test.describe("Create page · edge cases", () => {

  test("user with no providers can still open Create page", async ({
    freeUserPage,  // no provider fixture deliberately
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    const ids = await cp.getVisibleModels();
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) {
      expect(await cp.modelIsAvailable(id)).toBe(false);
    }
  });

  test("clicking an unavailable model tile does not navigate or crash", async ({
    freeUserPage,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    const id = (await cp.getVisibleModels())[0];
    await cp.modelTile(id).click({ force: true }).catch(() => {});
    await expect(cp.modelTile(id)).toBeVisible();
  });

  test("rapid chip clicks settle to the last value picked", async ({
    premiumUserPage, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    for (const v of ["1024x1024", "1024x1536", "1536x1024"]) {
      await cp.clickOption("size", v);
    }
    const active = (await cp.getFieldOptions("size")).find((o) => o.active);
    expect(active?.value).toBe("1536x1024");
  });

  test("super long prompt clamped to max_prompt_chars (32k default)", async ({
    premiumUserPage, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const long = "a".repeat(50_000);
    await cp.fillPrompt(long);
    const value = await premiumUserPage
      .locator("[data-test-prompt-input]")
      .inputValue();
    expect(value.length).toBeLessThanOrEqual(32_000);
  });
});

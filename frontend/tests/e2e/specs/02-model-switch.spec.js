// Test plan §6.6 — model switching behaviour.

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";

test.describe("Create page · model switching", () => {

  test("openai → gemini swaps the field set without crashing", async ({
    freeUserPage, bltcyRestricted, geminiFlashFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();

    await cp.selectModel("gpt-image-2");
    await expect(cp.paramField("size")).toBeVisible();
    expect(await cp.fieldExists("aspect_ratio")).toBe(false);

    await cp.selectModel("gemini-3.1-flash-image-preview");
    await expect(cp.paramField("aspect_ratio")).toBeVisible();
    expect(await cp.fieldExists("size")).toBe(false);
  });

  test("rapid back-and-forth switching does not crash", async ({
    freeUserPage, bltcyRestricted, geminiFlashFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    for (let i = 0; i < 5; i++) {
      await cp.selectModel("gpt-image-2");
      await cp.selectModel("gemini-3.1-flash-image-preview");
    }
    expect(await cp.getActiveModelId()).toBe("gemini-3.1-flash-image-preview");
  });

  test("switching to a model with n_max=1 clamps Output count", async ({
    premiumUserPage, bltcyRestricted, aliyunFull, geminiFlashFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    await cp.setOutputCount(8);
    // Wait for React to reflect the click in the data-test-output-count node.
    await premiumUserPage.waitForFunction(
      () =>
        Number(
          document.querySelector("[data-test-output-count]")?.textContent
        ) === 8
    );

    await cp.selectModel("gemini-3.1-flash-image-preview");
    await premiumUserPage.waitForFunction(
      () =>
        Number(
          document.querySelector("[data-test-output-count]")?.textContent
        ) === 1
    );
    expect(await cp.getOutputCount()).toBe(1);
  });

  test("switching does not refetch /api/models more than once", async ({
    freeUserPage, bltcyRestricted, geminiFlashFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    let modelsRequests = 0;
    freeUserPage.on("request", (r) => {
      if (r.url().includes("/api/models")) modelsRequests++;
    });
    await cp.goto();
    const after = modelsRequests;
    await cp.selectModel("gpt-image-2");
    await cp.selectModel("gemini-3.1-flash-image-preview");
    await cp.selectModel("gpt-image-2");
    expect(modelsRequests - after).toBeLessThanOrEqual(1);
  });
});

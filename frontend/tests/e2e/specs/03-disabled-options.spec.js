// Test plan §6.7 — disabled-option interaction details.

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";

test.describe("Create page · disabled options", () => {

  test("clicking a disabled chip does not POST anything", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");

    let postCount = 0;
    freeUserPage.on("request", (r) => {
      if (r.method() === "POST" && r.url().includes("/api/jobs")) postCount++;
    });
    await cp.paramField("size")
      .locator('[data-test-option="1024x1536"]')
      .click({ force: true })
      .catch(() => {});
    expect(postCount).toBe(0);
  });

  test("disabled chip has visibly lower opacity than enabled", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const enabled = freeUserPage.locator(
      '[data-test-field="size"] [data-test-option="1024x1024"]'
    );
    const disabled = freeUserPage.locator(
      '[data-test-field="size"] [data-test-option="auto"]'
    );
    const eOpacity = await enabled.evaluate((el) => getComputedStyle(el).opacity);
    const dOpacity = await disabled.evaluate((el) => getComputedStyle(el).opacity);
    expect(parseFloat(dOpacity)).toBeLessThan(parseFloat(eOpacity));
  });

  test("entire field disabled when caps key empty — reason text shown", async ({
    apiClient, freeUser, browser,
  }) => {
    // Ad-hoc provider with `size: []` to force the entire-field-disabled
    // path. We seed inline so the fixture set stays small.
    const id = `empty_${Date.now()}`;
    const apiBase = process.env.E2E_API_BASE || "http://127.0.0.1:8000";
    await apiClient.createProvider({
      provider_id: id, label: id,
      adapter_type: "openai_v1",
      base_url: "https://empty.test/v1", api_key: "sk-test",
      cost_per_image_cny: 0.1, initial_balance_cny: 10,
      max_concurrency: 1, rpm_limit: 10,
      tier_access: ["free"],
      supported_models: [{
        model_id: "gpt-image-2", enabled: true,
        capabilities: { n_max: 1, size: [], quality: ["auto"] },
      }],
    });
    try {
      const token = await apiClient.login(freeUser.username, freeUser.password);
      const page = await browser.newPage();
      try {
        await page.addInitScript(
          ([t, u, base]) => {
            localStorage.setItem("token", t);
            localStorage.setItem("user", JSON.stringify(u));
            localStorage.setItem("api_base", base);
          },
          [token, freeUser, apiBase]
        );
        const cp = new CreatePage(page);
        await cp.goto();
        await cp.selectModel("gpt-image-2");
        expect(await cp.isFieldDisabled("size")).toBe(true);
        const reason = await cp.getDisabledReason("size");
        expect(reason).toMatch(/not available/i);
      } finally {
        await page.close();
      }
    } finally {
      await apiClient.deleteProvider(id).catch(() => {});
    }
  });
});

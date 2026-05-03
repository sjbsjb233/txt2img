// Test plan §6.4 — render baseline + tier isolation (§6.5).

import { seedTest as test, expect } from "../fixtures/seed.js";
import { CreatePage } from "../pages/CreatePage.js";

test.describe("Create page · render baseline & tier isolation", () => {

  test("free user sees the model strip with primary models", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    const ids = await cp.getVisibleModels();
    expect(ids).toContain("gpt-image-2");
    expect(ids).toContain("gemini-3.1-flash-image-preview");
  });

  test("primary fields render in ui_schema order", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const order = await freeUserPage
      .locator('[data-test-group="primary"][data-test-field]')
      .evaluateAll((els) => els.map((e) => e.dataset.testField));
    expect(order).toEqual(["n_max", "size"]);
  });

  test("Output count rendered even when n_max=1 (Gemini)", async ({
    freeUserPage, geminiFlashFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gemini-3.1-flash-image-preview");
    await expect(cp.paramField("n_max")).toBeVisible();
    expect(await cp.getOutputCount()).toBe(1);
  });

  test("Advanced collapse rendered when there are advanced fields", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    expect(await cp.isAdvancedRendered()).toBe(true);
  });

  test("every field has a non-empty label", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const labels = await freeUserPage
      .locator("[data-test-field-label]")
      .allTextContents();
    expect(labels.length).toBeGreaterThan(0);
    for (const l of labels) {
      expect(l.trim().length).toBeGreaterThan(0);
    }
  });

  // ----- tier isolation ---------------------------------------------------

  test("free tier sees most size chips greyed; only the allowed one is enabled", async ({
    freeUserPage, bltcyRestricted, aliyunFull,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const opts = await cp.getFieldOptions("size");
    const enabled = opts.filter((o) => !o.disabled);
    expect(enabled).toHaveLength(1);
    expect(enabled[0].value).toBe("1024x1024");
    expect(opts.filter((o) => o.disabled).length).toBeGreaterThanOrEqual(2);
  });

  test("premium tier unions caps from both providers — all chips enabled", async ({
    premiumUserPage, bltcyRestricted, aliyunFull,
  }) => {
    const cp = new CreatePage(premiumUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const opts = await cp.getFieldOptions("size");
    expect(opts.filter((o) => !o.disabled).length).toBeGreaterThanOrEqual(4);
  });

  test("ui_schema field set is the same across tiers", async ({
    apiClient, freeUser, premiumUser, browser,
    bltcyRestricted, aliyunFull,
  }) => {
    const fToken = await apiClient.login(freeUser.username, freeUser.password);
    const pToken = await apiClient.login(premiumUser.username, premiumUser.password);

    const fp = await browser.newPage();
    const pp = await browser.newPage();
    try {
      for (const [pg, tok, user] of [
        [fp, fToken, freeUser], [pp, pToken, premiumUser],
      ]) {
        await pg.addInitScript(
          ([t, u, base]) => {
            localStorage.setItem("token", t);
            localStorage.setItem("user", JSON.stringify(u));
            localStorage.setItem("api_base", base);
          },
          [tok, user, process.env.E2E_API_BASE || "http://127.0.0.1:8000"]
        );
      }
      const fcp = new CreatePage(fp);
      const pcp = new CreatePage(pp);
      await fcp.goto();
      await fcp.selectModel("gpt-image-2");
      const ff = await fp
        .locator("[data-test-field]")
        .evaluateAll((els) => els.map((e) => e.dataset.testField));
      await pcp.goto();
      await pcp.selectModel("gpt-image-2");
      const pf = await pp
        .locator("[data-test-field]")
        .evaluateAll((els) => els.map((e) => e.dataset.testField));
      expect(ff.sort()).toEqual(pf.sort());
    } finally {
      await fp.close();
      await pp.close();
    }
  });

  test("clicking a disabled chip does not change the active value", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const before = (await cp.getFieldOptions("size")).find((o) => o.active)?.value;
    // Try clicking the disabled "1536x1024" chip — Playwright respects
    // the disabled attribute and won't fire the underlying onClick.
    await cp.paramField("size")
      .locator('[data-test-option="1536x1024"]')
      .click({ force: true })
      .catch(() => {});
    const after = (await cp.getFieldOptions("size")).find((o) => o.active)?.value;
    expect(after).toBe(before);
  });

  test("disabled chips have aria-disabled=true (a11y)", async ({
    freeUserPage, bltcyRestricted,
  }) => {
    const cp = new CreatePage(freeUserPage);
    await cp.goto();
    await cp.selectModel("gpt-image-2");
    const grey = freeUserPage.locator(
      '[data-test-field="size"] [data-test-option="1024x1536"]'
    );
    expect(await grey.getAttribute("aria-disabled")).toBe("true");
  });
});

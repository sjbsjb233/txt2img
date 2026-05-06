import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

// End-to-end test of the *real* customer flow: login → /create →
// pick the gpt-image-2 model → click "Custom…" → the redesigned
// SizeCustomModal opens, lets the user pick a size, and writes the
// chosen ``WxH`` back into the chip group as a custom value.
//
// Backend prerequisites:
// - uvicorn on http://127.0.0.1:8000 with the ``dev_seed_provider.py``
//   provider seeded (gpt-image-2, size_allow_custom=true)
// - user account ``tester`` / ``tester12345``

const SHOTS_DIR = "test-results/screenshots-flow";
const API_BASE = "http://127.0.0.1:8000";

function shot(name) {
  const path = `${SHOTS_DIR}/${name}.png`;
  mkdirSync(dirname(path), { recursive: true });
  return path;
}

test.describe("CreatePage → Custom size (real flow)", () => {
  test.beforeEach(async ({ page }) => {
    // Point the SPA at the dev backend before any module runs.
    await page.addInitScript((apiBase) => {
      try {
        localStorage.setItem("api_base", apiBase);
      } catch {}
    }, API_BASE);
  });

  test("login, select gpt-image-2, open redesigned Custom modal, pick a size", async ({
    page,
  }) => {
    // 1) Login page.
    await page.goto("/");
    // The router redirects unauth users to /login.
    await page.waitForURL(/\/login/);
    await page.screenshot({ path: shot("flow-01-login") });

    await page.locator('input[autocomplete="username"]').fill("tester");
    await page.locator('input[autocomplete="current-password"]').fill("tester12345");
    await page.locator('button[type="submit"]').click();

    // 2) Navigate to Create page (login lands on /dashboard).
    await page.waitForURL(/\/(dashboard|create)/);
    await page.goto("/create");
    await page.waitForURL(/\/create/);
    // Pick gpt-image-2 in the model strip — the button shows "ChatGPT Images 2.0".
    const modelChip = page.getByRole("button", { name: /ChatGPT Images 2\.0/i }).first();
    await modelChip.waitFor({ state: "visible", timeout: 15_000 });
    await modelChip.click();
    await page.screenshot({ path: shot("flow-02-create-model"), fullPage: true });

    // 3) Find the size field's "Custom…" affordance under the chip grid.
    const customBtn = page.getByTestId("size-custom-open");
    await customBtn.scrollIntoViewIfNeeded();
    await expect(customBtn).toBeVisible();
    await page.screenshot({ path: shot("flow-03-before-open"), fullPage: true });
    await customBtn.click();

    // 4) The redesigned modal renders.
    const modal = page.getByTestId("size-custom-modal");
    await expect(modal).toBeVisible();
    await expect(page.getByText("Custom · gpt-image-2")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Pick a size that matches/i })
    ).toBeVisible();
    await page.screenshot({ path: shot("flow-04-modal-open"), fullPage: false });

    // 5) Click a non-trivial preset: 2K landscape (2560×1440).
    const preset = page.getByTestId("size-preset-2560x1440");
    await preset.scrollIntoViewIfNeeded();
    await preset.click();

    // Modal closes after preset selection.
    await expect(modal).toBeHidden();

    // 6) The custom value should now appear as a selected chip beneath the
    //    chip group (the "size-custom-current" pill from CreatePage).
    const currentPill = page.getByTestId("size-custom-current");
    await expect(currentPill).toBeVisible();
    await expect(currentPill).toContainText("2560x1440");
    await page.screenshot({ path: shot("flow-05-after-pick"), fullPage: true });

    // 7) Reopen the modal and confirm the previously-picked preset is
    //    pre-selected.
    await page.getByTestId("size-custom-open").click();
    await expect(modal).toBeVisible();
    await expect(
      page.getByTestId("size-preset-2560x1440")
    ).toHaveAttribute("data-on", "true");
    await expect(page.getByTestId("size-custom-width")).toHaveValue("2560");
    await expect(page.getByTestId("size-custom-height")).toHaveValue("1440");
    await page.screenshot({ path: shot("flow-06-reopen-prefilled"), fullPage: false });

    // 8) Use the manual entry path: type 1920×1088 (FHD landscape) and Enter.
    await page.getByTestId("size-custom-width").fill("1920");
    await page.getByTestId("size-custom-height").fill("1088");
    await expect(page.getByTestId("size-custom-validation")).toContainText(
      /looks good/
    );
    await page.screenshot({ path: shot("flow-07-manual-typed"), fullPage: false });
    await page.getByTestId("size-custom-height").press("Enter");
    await expect(modal).toBeHidden();
    await expect(currentPill).toContainText("1920x1088");
    await page.screenshot({ path: shot("flow-08-after-manual"), fullPage: true });
  });
});

// Hybrid e2e for the thinking + custom-size feature.
//
// Prereq: backend on :18901 with the e2e provider seeded
// (tests-e2e/seed_e2e_provider.py) and frontend dev server on :5174
// pointed at it (VITE_API_BASE).
//
// Captures screenshots at every meaningful step under tests-e2e/_screenshots
// for visual review in the PR thread.

import { test, expect } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const FRONTEND_USERNAME = "admin";
const FRONTEND_PASSWORD = "test-admin-password";

async function login(page) {
  await page.goto("/login");
  await page.fill('input[autocomplete="username"]', FRONTEND_USERNAME);
  await page.fill('input[autocomplete="current-password"]', FRONTEND_PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/login"), {
      timeout: 8000,
    }),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForTimeout(400);
}

async function gotoCreate(page) {
  await page.goto("/create");
  // The advanced section needs to be expanded so the thinking chip-row
  // and the rest of the params are visible in screenshots.
  await page.waitForTimeout(500);
  // Expand advanced (it's a <details>); idempotent if already open.
  const adv = page.getByText(/Advanced$/i).first();
  if (await adv.isVisible().catch(() => false)) {
    await adv.click().catch(() => {});
  }
  await page.waitForTimeout(200);
}

// Anchor by the FieldRenderer's ``data-field`` attribute so the chip
// selector never bleeds into another row (Quality also has "low" /
// "high" — hence the per-field scoping).
function fieldChip(page, fieldK, value) {
  return page
    .locator(`[data-field="${fieldK}"]`)
    .locator("button", { hasText: new RegExp(`^${value}$`, "i") });
}

function thinkingChip(page, value) {
  return fieldChip(page, "thinking", value);
}

test("01 — thinking chip-row + Custom button render on Create page", async ({
  page,
}) => {
  await login(page);
  await gotoCreate(page);
  await page.screenshot({
    path: "tests-e2e/_screenshots/01_create_page.png",
    fullPage: true,
  });

  // The "Custom…" affordance lives next to the size chip grid.
  const customBtn = page.getByTestId("size-custom-open");
  await expect(customBtn).toBeVisible();

  // The thinking row must render all four canonical options inside the
  // group anchored by its "THINKING" label — not whichever "low/high"
  // happens to live in QUALITY further up.
  for (const opt of ["off", "low", "medium", "high"]) {
    const chip = thinkingChip(page, opt);
    await chip.scrollIntoViewIfNeeded();
    await expect(chip).toBeVisible();
  }
});

test("02 — opening Custom… shows the catalog and manual input", async ({
  page,
}) => {
  await login(page);
  await gotoCreate(page);
  await page.getByTestId("size-custom-open").click();
  const modal = page.getByTestId("size-custom-modal");
  await expect(modal).toBeVisible();
  await page.screenshot({
    path: "tests-e2e/_screenshots/02_modal_open.png",
    fullPage: true,
  });

  // Catalog includes a known good 16:9 entry.
  await expect(page.getByTestId("size-preset-1280x720")).toBeVisible();
  // Manual input visible
  await expect(page.getByTestId("size-custom-width")).toBeVisible();
  await expect(page.getByTestId("size-custom-height")).toBeVisible();
});

test("03 — picking a catalog row commits the custom value", async ({
  page,
}) => {
  await login(page);
  await gotoCreate(page);
  await page.getByTestId("size-custom-open").click();
  await page.waitForTimeout(200);
  await page.getByTestId("size-preset-1280x720").click();

  // Modal should close, and the current-value pill should now read "1280x720".
  await expect(page.getByTestId("size-custom-modal")).toHaveCount(0);
  const currentPill = page.getByTestId("size-custom-current");
  await expect(currentPill).toBeVisible();
  await expect(currentPill).toContainText("1280x720");
  await page.screenshot({
    path: "tests-e2e/_screenshots/03_after_pick.png",
    fullPage: true,
  });
});

test("04 — manual input rejects illegal sizes with friendly message", async ({
  page,
}) => {
  await login(page);
  await gotoCreate(page);
  await page.getByTestId("size-custom-open").click();
  await page.waitForTimeout(200);

  const w = page.getByTestId("size-custom-width");
  const h = page.getByTestId("size-custom-height");

  // 1000x1000 is not a 16-multiple — Apply should be disabled.
  await w.fill("1000");
  await h.fill("1000");
  const apply = page.getByTestId("size-custom-apply");
  await expect(apply).toBeDisabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /multiples of 16/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/04_invalid_not_16_multiple.png",
    fullPage: true,
  });

  // 1536x480 — aspect ratio above 3:1.
  await w.fill("1536");
  await h.fill("480");
  await expect(apply).toBeDisabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /ratio/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/05_invalid_ratio.png",
    fullPage: true,
  });

  // 4096x1024 — longest edge above 3840.
  await w.fill("4096");
  await h.fill("1024");
  await expect(apply).toBeDisabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /longest edge/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/06_invalid_max_edge.png",
    fullPage: true,
  });

  // 1920x1088 — legal FHD.
  await w.fill("1920");
  await h.fill("1088");
  await expect(apply).toBeEnabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /looks good/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/07_valid_fhd.png",
    fullPage: true,
  });

  // Apply commits the value.
  await apply.click();
  await expect(page.getByTestId("size-custom-modal")).toHaveCount(0);
  const pill = page.getByTestId("size-custom-current");
  await expect(pill).toContainText("1920x1088");
  await page.screenshot({
    path: "tests-e2e/_screenshots/08_after_manual_apply.png",
    fullPage: true,
  });
});

test("05 — clicking Thinking=high selects only the thinking chip", async ({
  page,
}) => {
  await login(page);
  await gotoCreate(page);
  // Capture full-page first so reviewer can see the row sits below
  // moderation.
  const chip = thinkingChip(page, "high");
  await chip.scrollIntoViewIfNeeded();
  await page.waitForTimeout(120);
  await chip.click();
  await page.waitForTimeout(150);

  // Confirm Quality's "high" did NOT get selected — pre-feature default
  // is Quality=auto, so Quality.high should still be the unselected
  // (paper-bg) variant. We assert this by checking the thinking-row
  // chip's background style differs from the quality-row chip's
  // background style for "high".
  const fullShot = "tests-e2e/_screenshots/09_thinking_high_selected.png";
  await page.screenshot({ path: fullShot, fullPage: true });

  // Quality row anchor → its "high" button.
  const qualityHigh = fieldChip(page, "quality", "high");
  // Both should be visible; the thinking one should be the *only* one
  // that holds the selected (dark) chip background.
  await expect(qualityHigh).toBeVisible();
  await expect(chip).toBeVisible();

  const thinkingBg = await chip.evaluate((el) =>
    getComputedStyle(el).backgroundColor,
  );
  const qualityBg = await qualityHigh.evaluate((el) =>
    getComputedStyle(el).backgroundColor,
  );
  // They must differ — the thinking chip is selected, quality.high is not.
  expect(thinkingBg).not.toEqual(qualityBg);
});

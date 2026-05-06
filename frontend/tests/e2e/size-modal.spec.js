import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const SHOTS_DIR = "test-results/screenshots";

function shot(name) {
  const path = `${SHOTS_DIR}/${name}.png`;
  mkdirSync(dirname(path), { recursive: true });
  return path;
}

test.describe("SizeCustomModal — redesigned", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/playground/size-modal.html");
    await expect(page.getByTestId("size-custom-modal")).toBeVisible();
    // Wait for fonts to load so screenshots are stable.
    await page.waitForLoadState("networkidle");
  });

  test("renders the redesigned chrome (header, constraint strip, preview)", async ({
    page,
  }) => {
    // Header still announces the gpt-image-2 context
    await expect(page.getByText("Custom · gpt-image-2")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Pick a size/ })
    ).toBeVisible();

    // Constraint strip lists all five backend rules
    await expect(page.getByText("Multiple", { exact: true })).toBeVisible();
    await expect(page.getByText("Longest edge", { exact: true })).toBeVisible();
    await expect(page.getByText("Total pixels", { exact: true })).toBeVisible();
    await expect(page.getByText("Aspect ratio", { exact: true })).toBeVisible();
    await expect(page.getByText("Experimental", { exact: true })).toBeVisible();

    // Live preview pane is present
    await expect(page.getByText("Live preview", { exact: true })).toBeVisible();

    await page.screenshot({ path: shot("01-initial"), fullPage: true });
  });

  test("clicking a curated preset highlights it, updates preview, and selects", async ({
    page,
  }) => {
    const preset2k = page.getByTestId("size-preset-2560x1440");
    await preset2k.click();
    // The harness records the selection; on selection the modal closes.
    await expect(page.getByTestId("harness-selected")).toContainText(
      "2560x1440"
    );
    // Reopen to verify the selected preset is highlighted on re-entry.
    await page.getByTestId("harness-open").click();
    await expect(page.getByTestId("size-custom-modal")).toBeVisible();
    await expect(
      page.getByTestId("size-preset-2560x1440")
    ).toHaveAttribute("data-on", "true");
    await expect(page.getByTestId("size-custom-stat-w")).toHaveText("2560");
    await expect(page.getByTestId("size-custom-stat-h")).toHaveText("1440");
    await expect(page.getByTestId("size-custom-zone")).toHaveText(
      /Experimental|Stable/
    );

    await page.screenshot({ path: shot("02-preset-selected"), fullPage: true });
  });

  test("manual entry: invalid then valid; Apply commits via keyboard", async ({
    page,
  }) => {
    const w = page.getByTestId("size-custom-width");
    const h = page.getByTestId("size-custom-height");

    // Multiples-of-16 violation
    await w.fill("1000");
    await h.fill("1000");
    await expect(page.getByTestId("size-custom-validation")).toContainText(
      "multiples of 16"
    );
    await expect(page.getByTestId("size-custom-apply")).toBeDisabled();
    await expect(page.getByTestId("size-custom-status")).toHaveText(/Invalid/);

    await page.screenshot({ path: shot("03-invalid"), fullPage: true });

    // Aspect-ratio cap violation (3840×1024 ≈ 3.75:1)
    await w.fill("3840");
    await h.fill("1024");
    await expect(page.getByTestId("size-custom-validation")).toContainText(
      /3 ?: ?1|exceeds|Aspect/
    );
    await expect(page.getByTestId("size-custom-apply")).toBeDisabled();

    // A clean valid value: FHD landscape (1920×1088)
    await w.fill("1920");
    await h.fill("1088");
    await expect(page.getByTestId("size-custom-validation")).toContainText(
      /looks good/
    );
    await expect(page.getByTestId("size-custom-status")).toHaveText(/Valid/);
    await expect(page.getByTestId("size-custom-apply")).toBeEnabled();

    // The preview pane mirrors the manual values
    await expect(page.getByTestId("size-custom-stat-w")).toHaveText("1920");
    await expect(page.getByTestId("size-custom-stat-h")).toHaveText("1088");

    await page.screenshot({ path: shot("04-valid-manual"), fullPage: true });

    // Submit via Enter on the height field
    await h.press("Enter");
    await expect(page.getByTestId("harness-selected")).toContainText("1920x1088");
  });

  test("Esc closes the modal", async ({ page }) => {
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("size-custom-modal")).toBeHidden();
  });

  test("Cancel button closes the modal", async ({ page }) => {
    await page.getByTestId("size-custom-cancel").click();
    await expect(page.getByTestId("size-custom-modal")).toBeHidden();
  });

  test("Close (X) button closes the modal", async ({ page }) => {
    await page.getByTestId("size-custom-close").click();
    await expect(page.getByTestId("size-custom-modal")).toBeHidden();
  });

  test("clicking the backdrop closes the modal", async ({ page }) => {
    // Click far outside the modal box
    await page.mouse.click(20, 20);
    await expect(page.getByTestId("size-custom-modal")).toBeHidden();
  });

  test("experimental preset is tagged and lights the warn zone", async ({
    page,
  }) => {
    const exp = page.getByTestId("size-preset-3840x2160");
    await expect(exp.getByText("exp")).toBeVisible();
    await exp.click();
    await page.getByTestId("harness-open").click();
    await expect(page.getByTestId("size-custom-zone")).toHaveText(
      "Experimental"
    );
    await page.screenshot({ path: shot("05-experimental"), fullPage: true });
  });

  test("opening with an existing custom value pre-fills the inputs", async ({
    page,
  }) => {
    await page.goto("/playground/size-modal.html?initial=1600x1200");
    await expect(page.getByTestId("size-custom-modal")).toBeVisible();
    await expect(page.getByTestId("size-custom-width")).toHaveValue("1600");
    await expect(page.getByTestId("size-custom-height")).toHaveValue("1200");
    await expect(page.getByTestId("size-preset-1600x1200")).toHaveAttribute(
      "data-on",
      "true"
    );
    await page.screenshot({ path: shot("06-prefill"), fullPage: true });
  });
});

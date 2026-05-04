import { test, expect } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const screenshots = path.join(__dirname, "screenshots");

const FAKE_TOKEN = "fake-jwt-for-tests";
const FAKE_USER = {
  id: "u_test",
  username: "tester",
  display_name: "Tester",
  role: "user",
  tier: "free",
};

const MODELS_FIXTURE = {
  models: [
    {
      model_id: "fake-model-a",
      display_name: "Fake Model A",
      blurb: "A test model.",
      tag: "TEST",
      logo: "draft",
      available: true,
      defaults: { aspect_ratio: "1:1", n: 1 },
      capabilities: {
        aspect_ratio: ["1:1", "16:9"],
        n_max: 4,
        max_reference_images: 4,
        max_prompt_chars: 4000,
      },
      ui_schema: [
        {
          k: "aspect_ratio",
          label: "Aspect ratio",
          hint: null,
          control: "chip-grid",
          options: ["1:1", "16:9"],
          group: "primary",
          order: 1,
        },
        {
          k: "n_max",
          value_key: "n",
          label: "Output count",
          hint: "how many to render",
          control: "number",
          presets: [1, 2, 4],
          group: "primary",
          order: 2,
        },
      ],
    },
  ],
  sessions: [],
};

async function seedAuth(page) {
  await page.addInitScript(
    ({ token, user }) => {
      try {
        localStorage.setItem("token", token);
        localStorage.setItem("user", JSON.stringify(user));
      } catch {
        // ignored — private mode
      }
    },
    { token: FAKE_TOKEN, user: FAKE_USER }
  );
}

async function installApiMocks(page) {
  // Intercept all /api/** calls. Anything we don't explicitly handle
  // returns 200 with `{}` so the page boots.
  // Match calls to the backend host only — `**/api/**` would catch
  // Vite's own `/src/api/*.js` source files and break module loading.
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === "/api/models") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MODELS_FIXTURE),
      });
    }
    if (p === "/api/me/preferences") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ generation: {}, ui: {} }),
      });
    }
    if (p === "/api/me") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(FAKE_USER),
      });
    }
    if (p === "/api/announcements") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    }
    if (p.startsWith("/api/sse")) {
      // SSE endpoint — leave it pending, no events.
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "{}",
    });
  });
}

test.beforeEach(async ({ page, context }) => {
  await context.clearCookies();
  await seedAuth(page);
  await installApiMocks(page);
  // Quiet the console noise from intentionally-unhandled stuff.
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));
});

test("autosave shows DRAFT SAVED toast and restores after reload", async ({
  page,
}) => {
  await page.goto("/create");

  // Wait for the prompt textarea.
  const textarea = page.locator("textarea").first();
  await expect(textarea).toBeVisible();

  // Type the prompt — should trigger debounced save after 600ms.
  await textarea.fill("a wedding cake on Mars, oil painting");

  // Wait for the toast to appear (DRAFT SAVED).
  const toast = page.getByTestId("draft-toast");
  await expect(toast).toBeVisible({ timeout: 3000 });
  await expect(toast).toHaveAttribute("data-kind", "saved");
  await expect(toast).toContainText("DRAFT SAVED");
  // Let the 240ms fade-in finish before snapping the screenshot.
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(screenshots, "01-saved-toast.png"),
    fullPage: false,
  });
  await page.screenshot({
    path: path.join(screenshots, "01-saved-toast-topbar.png"),
    clip: { x: 800, y: 70, width: 460, height: 60 },
  });

  // Wait for the toast to fade out (≈ 1600+360 ms hold+fade).
  await expect(toast).toBeHidden({ timeout: 3500 });

  // Sanity: localStorage now contains the draft.
  const draft = await page.evaluate(() =>
    localStorage.getItem("txt2img:create:draft:v1")
  );
  expect(draft).toBeTruthy();
  const parsed = JSON.parse(draft);
  expect(parsed.prompt).toContain("wedding cake on Mars");
  expect(parsed.user_id).toBe("u_test");

  // Reload — should auto-restore prompt and show DRAFT RESTORED toast.
  await page.reload();
  await expect(textarea).toBeVisible();
  // Restore is async (catalog loads → hook restores). Allow generous wait.
  await expect(textarea).toHaveValue(
    "a wedding cake on Mars, oil painting",
    { timeout: 5000 }
  );
  const restoredToast = page.getByTestId("draft-toast");
  await expect(restoredToast).toBeVisible({ timeout: 5000 });
  await expect(restoredToast).toHaveAttribute("data-kind", "restored");
  await expect(restoredToast).toContainText("DRAFT RESTORED");
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(screenshots, "02-restored-toast.png"),
    fullPage: false,
  });
  await page.screenshot({
    path: path.join(screenshots, "02-restored-toast-topbar.png"),
    clip: { x: 800, y: 70, width: 460, height: 60 },
  });
});

test("Clear button wipes draft and shows DRAFT CLEARED toast", async ({
  page,
}) => {
  await page.goto("/create");
  const textarea = page.locator("textarea").first();
  await expect(textarea).toBeVisible();
  await textarea.fill("draft to be cleared");

  // Wait for the SAVED toast cycle to finish so the cooldown story
  // is a clean test of the "user-action bypasses cooldown" rule.
  const toast = page.getByTestId("draft-toast");
  await expect(toast).toBeVisible({ timeout: 3000 });
  await expect(toast).toHaveAttribute("data-kind", "saved");

  // Click the top-bar Clear button. There's also a CLEAR chip on
  // the reference-images section, so scope to the topbar button.
  await page.locator(".btn.sm.ghost", { hasText: "Clear" }).click();

  const clearedToast = page.getByTestId("draft-toast");
  await expect(clearedToast).toBeVisible({ timeout: 2000 });
  await expect(clearedToast).toHaveAttribute("data-kind", "cleared");
  await expect(clearedToast).toContainText("DRAFT CLEARED");
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(screenshots, "03-cleared-toast.png"),
    fullPage: false,
  });
  await page.screenshot({
    path: path.join(screenshots, "03-cleared-toast-topbar.png"),
    clip: { x: 800, y: 70, width: 460, height: 60 },
  });

  // Prompt is now empty, draft is gone.
  await expect(textarea).toHaveValue("");
  const draft = await page.evaluate(() =>
    localStorage.getItem("txt2img:create:draft:v1")
  );
  expect(draft).toBeNull();
});

test("VISIBLE toast snaps shut on next keystroke and stays silent during cooldown", async ({
  page,
}) => {
  await page.goto("/create");
  const textarea = page.locator("textarea").first();
  await expect(textarea).toBeVisible();
  await textarea.fill("hello world");

  const toast = page.getByTestId("draft-toast");
  await expect(toast).toBeVisible({ timeout: 3000 });
  await expect(toast).toHaveAttribute("data-kind", "saved");
  // While VISIBLE, typing should snap the toast shut. After the snap
  // the element either has data-visible=false or is unmounted entirely
  // (after the 360ms fade-out the hook nulls it).
  await textarea.press("End");
  await textarea.type(" again", { delay: 30 });
  // Wait for the snap-shut: toast element should either be gone or invisible.
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="draft-toast"]');
    return !el || el.getAttribute("data-visible") === "false";
  }, undefined, { timeout: 1500 });
  // Stays silent during the 4s cooldown — even after debounce fires
  // we should not see a new saved toast become visible.
  await page.waitForTimeout(1500); // > 600ms debounce
  const stillVisible = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="draft-toast"]');
    if (!el) return false;
    return el.getAttribute("data-visible") === "true";
  });
  expect(stillVisible).toBe(false);
  await page.screenshot({
    path: path.join(screenshots, "04-cooldown-silent.png"),
    fullPage: false,
  });
});

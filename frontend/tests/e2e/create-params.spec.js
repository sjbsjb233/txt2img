// Drives the real Create page in a browser, sets every adjustable model
// parameter declared by each adapter's UI schema, submits, and verifies
// that the capture upstream received the exact wire payload the user just
// configured.
//
// Three models are exercised, mirroring the adapter ui_schema set:
//   - gpt-image-2 (openai_v1)
//   - gemini-3-pro-image-preview (gemini_v1beta, conservative subset)
//   - gemini-3.1-flash-image-preview (gemini_v1beta, full feature set)
//
// Each scenario clears the capture buffer first, drives the UI, polls
// the buffer, and asserts both the request kind/path and every field
// the UI was supposed to send.

import { test, expect, request } from "@playwright/test";

const BACKEND = "http://127.0.0.1:18900";
const CAPTURE = "http://127.0.0.1:18890";
const FRONTEND = "http://127.0.0.1:5174";

const PASS = "tester-pass-1234";
// boot_env.py seeds e2e_tester_00 … e2e_tester_19. Each test uses its
// own user so the per-user "≥5 jobs in 60s ⇒ captcha" burst rule cannot
// trigger across the suite. We mint the next free name in beforeEach.
let _userIndex = 0;
function nextUser() {
  const idx = _userIndex++;
  return `e2e_tester_${String(idx).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async function login(page, username) {
  await page.goto(`${FRONTEND}/login`);
  await page.waitForSelector('input[autocomplete="username"]');
  await page.fill('input[autocomplete="username"]', username);
  await page.fill('input[autocomplete="current-password"]', PASS);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/login")),
    page.click('button[type="submit"]'),
  ]);
}

async function gotoCreate(page) {
  await page.goto(`${FRONTEND}/create`);
  // The model strip renders once /api/models returns.
  await page.waitForSelector(".model-strip button");
}

async function selectModel(page, displayName) {
  const card = page
    .locator(`.model-strip button:not([disabled])`)
    .filter({ hasText: displayName });
  await card.first().click();
  // Wait for the right-rail to re-render with the model's schema by
  // looking for at least one [data-field] entry.
  await page.waitForSelector("[data-field]");
}

async function setChipField(page, fieldKey, value) {
  // ChipGroup / FieldRenderer renders the field wrapper as
  // <div data-field="<k>">… each option is a <button>. image_size and
  // aspect_ratio use custom render helpers that put extra descriptor
  // text inside the button, so anchored-text matching wouldn't fit; we
  // pick the button whose inner ticker / label element holds the value
  // verbatim.
  const fieldRoot = page.locator(`[data-field="${fieldKey}"]`);
  await expect(fieldRoot).toBeVisible();
  const re = new RegExp(`^\\s*${escapeRe(value)}\\s*$`);
  let chip = fieldRoot.locator("button:not([disabled])").filter({
    has: page.locator(".ticker", { hasText: re }),
  });
  if (!(await chip.count())) {
    chip = fieldRoot.locator("button:not([disabled])").filter({
      has: page.locator(".mono", { hasText: re }),
    });
  }
  if (!(await chip.count())) {
    chip = fieldRoot.locator("button:not([disabled])").filter({ hasText: re });
  }
  await chip.first().click();
}

async function setNumberPreset(page, fieldKey, n) {
  const fieldRoot = page.locator(`[data-field="${fieldKey}"]`);
  await expect(fieldRoot).toBeVisible();
  await fieldRoot
    .locator("button:not([disabled])")
    .filter({ hasText: new RegExp(`^${n}$`) })
    .first()
    .click();
}

async function setToggle(page, fieldKey, on) {
  const fieldRoot = page.locator(`[data-field="${fieldKey}"]`);
  const cb = fieldRoot.locator('input[type="checkbox"]').first();
  const checked = await cb.isChecked();
  if (checked !== on) await cb.click();
}

async function openAdvanced(page) {
  const details = page.locator('[data-testid="advanced-details"]');
  if (await details.count()) {
    const open = await details.evaluate((el) => el.open);
    if (!open) await details.locator("summary").click();
  }
}

function escapeRe(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function clearCaptures(api) {
  await api.delete(`${CAPTURE}/__captures`);
}

async function waitForCapture(api, predicate, { timeoutMs = 15000 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const resp = await api.get(`${CAPTURE}/__captures`);
    const body = await resp.json();
    const hit = (body.items || []).find(predicate);
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("timed out waiting for matching capture");
}

async function fillPromptAndSubmit(page, prompt) {
  await page.fill("textarea", prompt);
  await page.click('button:has-text("Generate")');
}

// ---------------------------------------------------------------------------
// per-suite global state
// ---------------------------------------------------------------------------

let api;

test.beforeAll(async () => {
  api = await request.newContext();
});

test.afterAll(async () => {
  await api.dispose();
});

test.beforeEach(async ({ page }) => {
  // Each scenario starts with a wiped capture buffer and a freshly
  // initialised auth session (own browser context per Playwright test).
  await api.delete(`${CAPTURE}/__captures`);
  await page.addInitScript(() => {
    // Prevent the previously cached api_base from a stale tab affecting
    // navigation; the bundle's VITE_API_BASE is what we want to honour.
    try {
      localStorage.removeItem("api_base");
    } catch (e) {}
  });
});

// ---------------------------------------------------------------------------
// gpt-image-2 — openai_v1 wire format
// ---------------------------------------------------------------------------

test("openai_v1 / gpt-image-2 — every panel parameter survives the round trip", async ({
  page,
}) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "ChatGPT Images 2.0");

  // Primary group
  await setNumberPreset(page, "n_max", 2);
  await setChipField(page, "size", "1536x1024");

  // Advanced group
  await openAdvanced(page);
  await setChipField(page, "quality", "high");
  await setChipField(page, "output_format", "jpeg");
  await setChipField(page, "background", "opaque");
  await setChipField(page, "moderation", "low");
  await setChipField(page, "thinking", "medium");

  const prompt = "still-life of three overripe bananas on a marble slab";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "openai_generations");
  const body = cap.json;
  expect(body.model).toBe("gpt-image-2");
  expect(body.prompt).toBe(prompt);
  expect(body.n).toBe(2);
  expect(body.size).toBe("1536x1024");
  expect(body.quality).toBe("high");
  expect(body.output_format).toBe("jpeg");
  expect(body.background).toBe("opaque");
  expect(body.moderation).toBe("low");
  expect(body.thinking).toBe("medium");
});

test("openai_v1 / gpt-image-2 — alternate values (size auto, format webp, thinking off)", async ({
  page,
}) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "ChatGPT Images 2.0");

  await setNumberPreset(page, "n_max", 1);
  await setChipField(page, "size", "1024x1536");

  await openAdvanced(page);
  await setChipField(page, "quality", "low");
  await setChipField(page, "output_format", "webp");
  await setChipField(page, "background", "auto");
  await setChipField(page, "moderation", "auto");
  await setChipField(page, "thinking", "off");

  const prompt = "isometric voxel diorama of a tiny kitchen";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "openai_generations");
  const body = cap.json;
  expect(body.model).toBe("gpt-image-2");
  expect(body.prompt).toBe(prompt);
  // n=1 is the wire default and the adapter omits it.
  expect(body.n === undefined || body.n === 1).toBeTruthy();
  expect(body.size).toBe("1024x1536");
  expect(body.quality).toBe("low");
  expect(body.output_format).toBe("webp");
  expect(body.background).toBe("auto");
  expect(body.moderation).toBe("auto");
  expect(body.thinking).toBe("off");
});

test("openai_v1 / gpt-image-2 — custom size 1920x1088 via SizeCustomModal", async ({
  page,
}) => {
  const _u = nextUser();
  await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "ChatGPT Images 2.0");

  await setNumberPreset(page, "n_max", 1);
  // Open the SizeCustomModal — its trigger lives below the size chips.
  await page.locator('[data-testid="size-custom-open"]').click();
  await expect(page.locator('[data-testid="size-custom-modal"]')).toBeVisible();
  await page.locator('[data-testid="size-custom-width"]').fill("1920");
  await page.locator('[data-testid="size-custom-height"]').fill("1088");
  await page.locator('[data-testid="size-custom-apply"]').click();
  await expect(page.locator('[data-testid="size-custom-modal"]')).toHaveCount(0);

  await openAdvanced(page);
  await setChipField(page, "quality", "medium");
  await setChipField(page, "output_format", "png");
  await setChipField(page, "background", "auto");
  await setChipField(page, "moderation", "auto");
  await setChipField(page, "thinking", "low");

  const prompt = "diptych of two ripe bananas glowing softly in studio light";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "openai_generations");
  const body = cap.json;
  expect(body.model).toBe("gpt-image-2");
  expect(body.prompt).toBe(prompt);
  expect(body.size).toBe("1920x1088");
  expect(body.quality).toBe("medium");
  expect(body.output_format).toBe("png");
  expect(body.background).toBe("auto");
  expect(body.moderation).toBe("auto");
  expect(body.thinking).toBe("low");
});

// ---------------------------------------------------------------------------
// gemini-3-pro-image-preview — base aspect ratios + 1K/2K/4K
// ---------------------------------------------------------------------------

test("gemini_v1beta / gemini-3-pro — primary fields + google_search", async ({
  page,
}) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "Gemini 3 Pro");

  await setChipField(page, "aspect_ratio", "21:9");
  await setChipField(page, "image_size", "4K");

  await openAdvanced(page);
  await setToggle(page, "google_search", true);

  const prompt = "cinematic establishing shot of a city at dawn";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "gemini_generate");
  const body = cap.json;
  expect(cap.model).toBe("gemini-3-pro-image-preview");
  // contents.parts[0].text holds the prompt.
  expect(body.contents[0].parts[0].text).toBe(prompt);
  expect(body.generationConfig.responseModalities).toEqual(["TEXT", "IMAGE"]);
  expect(body.generationConfig.imageConfig).toEqual({
    aspectRatio: "21:9",
    imageSize: "4K",
  });
  // 3-pro does not get the imageSearch tool — only google_search.
  expect(body.tools).toEqual([{ google_search: {} }]);
  expect(body.generationConfig.thinkingConfig).toBeUndefined();
});

test("gemini_v1beta / gemini-3-pro — 1K + 9:16, no grounding", async ({ page }) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "Gemini 3 Pro");

  await setChipField(page, "aspect_ratio", "9:16");
  await setChipField(page, "image_size", "1K");

  await openAdvanced(page);
  await setToggle(page, "google_search", false);

  const prompt = "vertical poster of a banana phone";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "gemini_generate");
  const body = cap.json;
  expect(cap.model).toBe("gemini-3-pro-image-preview");
  expect(body.generationConfig.imageConfig).toEqual({
    aspectRatio: "9:16",
    imageSize: "1K",
  });
  expect(body.tools).toBeUndefined();
});

// ---------------------------------------------------------------------------
// gemini-3.1-flash-image-preview — full feature set
// ---------------------------------------------------------------------------

test("gemini_v1beta / gemini-3.1-flash — full feature set, 8:1 + 512", async ({
  page,
}) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "Gemini 3.1 Flash");

  await setChipField(page, "aspect_ratio", "8:1");
  await setChipField(page, "image_size", "512");

  await openAdvanced(page);
  await setChipField(page, "thinking_level", "high");
  await setToggle(page, "include_thoughts", true);
  await setToggle(page, "image_search", true);
  await setToggle(page, "google_search", true);

  const prompt = "ultra-wide banner of a desert highway at golden hour";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "gemini_generate");
  const body = cap.json;
  expect(cap.model).toBe("gemini-3.1-flash-image-preview");
  expect(body.contents[0].parts[0].text).toBe(prompt);
  expect(body.generationConfig.imageConfig).toEqual({
    aspectRatio: "8:1",
    imageSize: "512",
  });
  expect(body.generationConfig.thinkingConfig).toEqual({
    thinkingLevel: "high",
    includeThoughts: true,
  });
  // image_search on 3.1-flash widens to the searchTypes selector.
  expect(body.tools).toEqual([
    {
      google_search: {
        searchTypes: { webSearch: {}, imageSearch: {} },
      },
    },
  ]);
});

test("gemini_v1beta / gemini-3.1-flash — minimal thinking, only google_search", async ({
  page,
}) => {
  const _u = nextUser(); await login(page, _u);
  await gotoCreate(page);
  await selectModel(page, "Gemini 3.1 Flash");

  await setChipField(page, "aspect_ratio", "1:4");
  await setChipField(page, "image_size", "2K");

  await openAdvanced(page);
  await setChipField(page, "thinking_level", "minimal");
  await setToggle(page, "include_thoughts", false);
  await setToggle(page, "image_search", false);
  await setToggle(page, "google_search", true);

  const prompt = "tall stacked monolith of bananas, surreal";
  await fillPromptAndSubmit(page, prompt);

  const cap = await waitForCapture(api, (c) => c.kind === "gemini_generate");
  const body = cap.json;
  expect(cap.model).toBe("gemini-3.1-flash-image-preview");
  expect(body.generationConfig.imageConfig).toEqual({
    aspectRatio: "1:4",
    imageSize: "2K",
  });
  // include_thoughts=false is filtered out by the adapter so only the
  // level field appears.
  expect(body.generationConfig.thinkingConfig).toEqual({
    thinkingLevel: "minimal",
  });
  // image_search off ⇒ adapter falls back to plain google_search shape.
  expect(body.tools).toEqual([{ google_search: {} }]);
});

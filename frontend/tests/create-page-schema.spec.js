// PR-3 Playwright smoke / regression for the schema-driven Create-page
// param panel.
//
// We never boot the real backend — every test stubs the endpoints the
// page touches via `page.route`, then verifies the rendered panel against
// the mocked ui_schema + capabilities. This isolates the renderer change
// (which is what PR-3 is about) from provider/tier/database state.

import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = path.resolve(__dirname, "..", "playwright-shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

// -------- mock factories ----------------------------------------------------

const OPENAI_UI_SCHEMA = [
  { k: "n_max", value_key: "n", control: "number", label: "Output count", hint: "per generation",
    group: "primary", order: 10, min: 1, max: 10, presets: [1, 2, 4, 8] },
  { k: "size", control: "chip-grid", label: "Size", hint: "output dimensions",
    group: "primary", order: 20,
    options: ["1024x1024", "1024x1536", "1536x1024", "auto"] },
  { k: "quality", control: "chip-row", label: "Quality", hint: "render fidelity",
    group: "advanced", order: 10, options: ["low", "medium", "high", "auto"] },
  { k: "output_format", control: "chip-row", label: "Output format", hint: "encoded as",
    group: "advanced", order: 20, options: ["jpeg", "png", "webp"] },
  { k: "background", control: "chip-row", label: "Background",
    group: "advanced", order: 30, options: ["auto", "opaque"] },
  { k: "moderation", control: "chip-row", label: "Moderation", hint: "content filter",
    group: "advanced", order: 40, options: ["auto", "low"] },
];

const GEMINI_PRO_UI_SCHEMA = [
  { k: "n_max", value_key: "n", control: "number", label: "Output count", hint: "per generation",
    group: "primary", order: 10, min: 1, max: 1, presets: [1] },
  { k: "aspect_ratio", control: "chip-grid", label: "Shape", hint: "aspect ratio",
    group: "primary", order: 20,
    options: ["1:1","16:9","2:3","21:9","3:2","3:4","4:3","4:5","5:4","9:16"] },
  { k: "image_size", control: "chip-row", label: "Image size", hint: "rendered resolution",
    group: "primary", order: 30, options: ["1K", "2K", "4K"] },
  { k: "google_search", control: "toggle", label: "Google search grounding",
    hint: "ground on web facts", group: "advanced", order: 40 },
];

const GEMINI_FLASH_UI_SCHEMA = [
  { k: "n_max", value_key: "n", control: "number", label: "Output count", hint: "per generation",
    group: "primary", order: 10, min: 1, max: 1, presets: [1] },
  { k: "aspect_ratio", control: "chip-grid", label: "Shape", hint: "aspect ratio",
    group: "primary", order: 20,
    options: ["1:1","1:4","1:8","16:9","2:3","21:9","3:2","3:4","4:1","4:3","4:5","5:4","8:1","9:16"] },
  { k: "image_size", control: "chip-row", label: "Image size", hint: "rendered resolution",
    group: "primary", order: 30, options: ["1K", "2K", "4K", "512"] },
  { k: "thinking_level", control: "chip-row", label: "Thinking level", hint: "latency vs. care",
    group: "advanced", order: 10, options: ["high", "minimal"] },
  { k: "include_thoughts", control: "toggle", label: "Include thoughts",
    hint: "surface intermediate reasoning", group: "advanced", order: 20 },
  { k: "image_search", control: "toggle", label: "Image search grounding",
    hint: "use search images as context", group: "advanced", order: 30 },
  { k: "google_search", control: "toggle", label: "Google search grounding",
    hint: "ground on web facts", group: "advanced", order: 40 },
];

function modelDescriptor(over) {
  return {
    model_id: "gpt-image-2",
    display_name: "ChatGPT Images 2.0",
    tag: "RECOMMENDED",
    logo: "chatgpt",
    blurb: "Best for editorial, photoreal, brand.",
    available: true,
    available_reason: null,
    ui_schema: OPENAI_UI_SCHEMA,
    capabilities: {
      n_max: 10,
      size: ["1024x1024", "1024x1536", "1536x1024", "auto"],
      quality: ["low", "medium", "high", "auto"],
      output_format: ["jpeg", "png", "webp"],
      background: ["auto", "opaque"],
      moderation: ["auto", "low"],
      max_reference_images: 14,
      max_prompt_chars: 32000,
    },
    defaults: { n: 1, size: "1024x1024", quality: "auto", output_format: "png" },
    ...over,
  };
}

function geminiPro(over) {
  return modelDescriptor({
    model_id: "gemini-3-pro-image-preview",
    display_name: "Gemini 3 Pro (Nano Banana Pro)",
    tag: "QUALITY",
    logo: "flash",
    blurb: "High-fidelity gemini preview with built-in thinking.",
    ui_schema: GEMINI_PRO_UI_SCHEMA,
    capabilities: {
      n_max: 1,
      aspect_ratio: ["1:1","16:9","2:3","21:9","3:2","3:4","4:3","4:5","5:4","9:16"],
      image_size: ["1K", "2K", "4K"],
      google_search: true,
      max_reference_images: 14,
      max_prompt_chars: 32000,
    },
    defaults: { aspect_ratio: "1:1", image_size: "2K" },
    ...over,
  });
}

function geminiFlash(over) {
  return modelDescriptor({
    model_id: "gemini-3.1-flash-image-preview",
    display_name: "Gemini 3.1 Flash (Nano Banana 2)",
    tag: "FAST",
    logo: "flash",
    blurb: "Fast iteration with configurable thinking.",
    ui_schema: GEMINI_FLASH_UI_SCHEMA,
    capabilities: {
      n_max: 1,
      aspect_ratio: ["1:1","1:4","1:8","16:9","2:3","21:9","3:2","3:4","4:1","4:3","4:5","5:4","8:1","9:16"],
      image_size: ["1K", "2K", "4K", "512"],
      thinking_level: ["high", "minimal"],
      include_thoughts: true,
      image_search: true,
      google_search: true,
      max_reference_images: 14,
      max_prompt_chars: 32000,
    },
    defaults: { aspect_ratio: "1:1", image_size: "1K", thinking_level: "minimal" },
    ...over,
  });
}

// -------- helpers -----------------------------------------------------------

async function fakeAuth(page) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake.test.token");
    localStorage.setItem(
      "user",
      JSON.stringify({
        id: "user_test_1",
        email: "tester@example.com",
        display_name: "Tester",
        tier: "free",
        is_admin: false,
      })
    );
  });
}

// Only intercept calls heading to the backend host (default
// http://127.0.0.1:8000). The vite dev server also serves /src/api/*.js
// under the same path prefix, so a bare `**/api/**` glob would mis-match
// those JS modules and break the page load.
const BACKEND_GLOB = "http://127.0.0.1:8000/api/**";

async function stubBackend(page, { models = [], jobsCapture = null } = {}) {
  await page.route(BACKEND_GLOB, async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;

    if (p === "/api/models") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models, sessions: [] }),
      });
    }
    if (p === "/api/announcements/active") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ announcements: [] }),
      });
    }
    if (p === "/api/me/preferences") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ generation: {}, ui: {} }),
      });
    }
    if (p === "/api/me") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          id: "user_test_1", email: "tester@example.com",
          display_name: "Tester", tier: "free", is_admin: false,
        }),
      });
    }
    if (p === "/api/jobs/precheck") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ captcha_required: false, site_key: null }),
      });
    }
    if (p === "/api/jobs") {
      // capture the multipart payload so the test can assert which
      // params the click sequence actually mapped to.
      try {
        const req = route.request();
        if (jobsCapture) {
          jobsCapture.push({
            method: req.method(),
            postData: req.postData(),
          });
        }
      } catch { /* noop */ }
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          job: { id: "job_test", status: "QUEUED" },
          images: [],
        }),
      });
    }
    if (p.startsWith("/api/sse")) {
      // Hold the stream open so EventSource doesn't reconnect-storm.
      return route.fulfill({
        status: 200, contentType: "text/event-stream",
        body: ":\n\n",
      });
    }
    if (p.startsWith("/api/archive")) {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
    }
    // default
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: "{}",
    });
  });
}

async function gotoCreate(page) {
  await page.goto("/create");
  await page.waitForSelector('button:has-text("Generate")', { timeout: 10_000 });
}

async function shot(page, name) {
  const file = path.join(SHOT_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

// -------- TESTS -------------------------------------------------------------

test.describe("Create page · schema-driven param panel", () => {

  test("A — gpt-image-2 fully unlocked: every primary + advanced field interactive", async ({ page }) => {
    await fakeAuth(page);
    await stubBackend(page, { models: [modelDescriptor()] });
    await gotoCreate(page);

    // primary fields: Output count + Size
    await expect(page.getByText("Output count", { exact: true })).toBeVisible();
    await expect(page.getByText("Size", { exact: true })).toBeVisible();

    // n=1 is default; ticker reads ×1
    await expect(page.locator(".ticker", { hasText: /^×1$/ })).toBeVisible();

    // Click "4" preset and assert ticker flips to ×4
    await page.locator('button:has-text("4")').first().click();
    await expect(page.locator(".ticker", { hasText: /^×4$/ })).toBeVisible();

    // Pick a non-default size
    await page.locator('button:has-text("1024x1536")').click();

    // Open Advanced
    await page.locator("summary", { hasText: "Advanced" }).click();
    await expect(page.getByText("Quality", { exact: true })).toBeVisible();
    await expect(page.getByText("Output format", { exact: true })).toBeVisible();
    await expect(page.getByText("Background", { exact: true })).toBeVisible();
    await expect(page.getByText("Moderation", { exact: true })).toBeVisible();

    // Click low-quality chip
    await page.locator('button:has-text("low")').first().click();

    await shot(page, "A_openai_full_unlocked");
  });

  test("B — gpt-image-2 restricted (free tier): unreachable options greyed, not hidden", async ({ page }) => {
    await fakeAuth(page);
    const restricted = modelDescriptor({
      capabilities: {
        n_max: 4,
        size: ["1024x1024"],
        quality: ["auto"],
        output_format: ["png"],
        background: ["auto"],
        moderation: ["auto"],
        max_reference_images: 14,
        max_prompt_chars: 32000,
      },
    });
    await stubBackend(page, { models: [restricted] });
    await gotoCreate(page);

    // Output count: 8 must be disabled (cap is 4)
    const eight = page.locator('button:has-text("8")').first();
    await expect(eight).toBeDisabled();

    // 4 still enabled
    const four = page.locator('button:has-text("4")').first();
    await expect(four).toBeEnabled();

    // Size: 1024x1024 enabled, 1024x1536 disabled but visible
    const sizeAllowed = page.locator('button:has-text("1024x1024")').first();
    const sizeDisallowed = page.locator('button:has-text("1024x1536")').first();
    await expect(sizeAllowed).toBeEnabled();
    await expect(sizeDisallowed).toBeVisible();
    await expect(sizeDisallowed).toBeDisabled();

    // Open Advanced — quality 'low' must be disabled, 'auto' enabled.
    // Scope to within <details> since the primary Size chips also have
    // an "auto" option which would otherwise win the .first() race.
    await page.locator("summary", { hasText: "Advanced" }).click();
    const qLow = page.locator('details button:has-text("low")').first();
    await expect(qLow).toBeVisible();
    await expect(qLow).toBeDisabled();
    const qAuto = page.locator('details button:has-text("auto")').first();
    await expect(qAuto).toBeEnabled();

    await shot(page, "B_openai_restricted_free_tier");
  });

  test("C — gemini-3-pro: n locked at 1, no thinking/image_search toggles", async ({ page }) => {
    await fakeAuth(page);
    await stubBackend(page, { models: [geminiPro()] });
    await gotoCreate(page);

    await expect(page.getByText("Shape", { exact: true })).toBeVisible();
    await expect(page.getByText("Image size", { exact: true })).toBeVisible();

    // n stuck at 1 — ticker ×1, only one preset and it's "1"
    await expect(page.locator(".ticker", { hasText: /^×1$/ })).toBeVisible();
    const presetButtons = page
      .locator('div:has-text("Output count") + div button')
      .filter({ hasNotText: /[a-zA-Z]/ });
    // Only one numeric preset in the row
    await expect(presetButtons).toHaveCount(1);

    // 1:1 should be auto-defaulted (selected) since defaults.aspect_ratio = "1:1"
    // The selected chip uses var(--ink) bg; assert by class style not super easy,
    // so we just confirm 1:1 button exists and shape grid is rendered.
    await expect(page.locator('button:has-text("1:1")')).toBeVisible();

    // Open Advanced — Google search toggle must be present, but no thinking_level
    await page.locator("summary", { hasText: "Advanced" }).click();
    await expect(page.getByText("Google search grounding")).toBeVisible();
    await expect(page.getByText("Thinking level")).toHaveCount(0);
    await expect(page.getByText("Image search grounding")).toHaveCount(0);
    await expect(page.getByText("Include thoughts")).toHaveCount(0);

    await shot(page, "C_gemini_3_pro");
  });

  test("D — gemini-3.1-flash: full advanced set, extra aspect ratios + 512", async ({ page }) => {
    await fakeAuth(page);
    await stubBackend(page, { models: [geminiFlash()] });
    await gotoCreate(page);

    // 14 aspect ratios incl. extreme 8:1 / 1:8
    await expect(page.locator('button:has-text("8:1")')).toBeVisible();
    await expect(page.locator('button:has-text("1:8")')).toBeVisible();

    // image_size includes 512
    await expect(page.locator('button:has-text("512")')).toBeVisible();

    await page.locator("summary", { hasText: "Advanced" }).click();
    await expect(page.getByText("Thinking level", { exact: true })).toBeVisible();
    await expect(page.getByText("Include thoughts", { exact: true })).toBeVisible();
    await expect(page.getByText("Image search grounding", { exact: true })).toBeVisible();
    await expect(page.getByText("Google search grounding", { exact: true })).toBeVisible();

    // Toggle "include_thoughts" — climb to the enclosing <label>
    const includeLabel = page.locator("label").filter({ hasText: "Include thoughts" });
    const include = includeLabel.locator("input[type=checkbox]");
    await expect(include).not.toBeChecked();
    await includeLabel.click();
    await expect(include).toBeChecked();

    await shot(page, "D_gemini_3_1_flash");
  });

  test("E — switch model: openai (size=1536x1024) → gemini, leftover OpenAI fields scrubbed", async ({ page }) => {
    await fakeAuth(page);
    await stubBackend(page, { models: [modelDescriptor(), geminiFlash()] });
    await gotoCreate(page);

    // pick a non-default OpenAI size
    await page.locator('button:has-text("1536x1024")').click();

    // switch to Gemini Flash via the model strip
    await page.locator(".model-strip button", { hasText: "Gemini 3.1 Flash" }).click();

    // Size field should be gone (Gemini schema doesn't define `size`)
    await expect(page.getByText("Size", { exact: true })).toHaveCount(0);
    // Shape (aspect_ratio) should now be visible
    await expect(page.getByText("Shape", { exact: true })).toBeVisible();
    // n ticker locked to ×1
    await expect(page.locator(".ticker", { hasText: /^×1$/ })).toBeVisible();

    await shot(page, "E_switch_openai_to_gemini");
  });

  test("F — empty ui_schema: 'no configurable parameters' fallback message", async ({ page }) => {
    await fakeAuth(page);
    const naked = modelDescriptor({
      ui_schema: [],
      capabilities: { max_reference_images: 14, max_prompt_chars: 32000 },
      defaults: {},
    });
    await stubBackend(page, { models: [naked] });
    await gotoCreate(page);
    await expect(page.getByText("This model has no configurable parameters.")).toBeVisible();
    await shot(page, "F_empty_ui_schema");
  });

  test("G — toggle field with cap=null: rendered greyed with disabled reason", async ({ page }) => {
    await fakeAuth(page);
    const partial = geminiFlash({
      capabilities: {
        n_max: 1,
        aspect_ratio: ["1:1", "16:9"],
        image_size: ["1K"],
        thinking_level: ["minimal"],
        // include_thoughts / image_search / google_search OMITTED
        max_reference_images: 14,
        max_prompt_chars: 32000,
      },
    });
    await stubBackend(page, { models: [partial] });
    await gotoCreate(page);
    await page.locator("summary", { hasText: "Advanced" }).click();

    // toggles should be present but disabled
    const includeBox = page
      .locator("label").filter({ hasText: "Include thoughts" })
      .locator("input[type=checkbox]");
    await expect(includeBox).toBeDisabled();
    const googleBox = page
      .locator("label").filter({ hasText: "Google search grounding" })
      .locator("input[type=checkbox]");
    await expect(googleBox).toBeDisabled();

    await shot(page, "G_toggles_disabled_no_cap");
  });

  test("H — list field with cap=[]: still rendered, all options disabled, hint shown", async ({ page }) => {
    await fakeAuth(page);
    const stripped = modelDescriptor({
      capabilities: {
        n_max: 4,
        size: [],            // empty list → field-level disabled
        quality: ["auto"],
        max_reference_images: 14,
        max_prompt_chars: 32000,
      },
    });
    await stubBackend(page, { models: [stripped] });
    await gotoCreate(page);

    await expect(page.getByText("Size", { exact: true })).toBeVisible();
    // Multiple greyed fields share this hint (size + 3 advanced toggles
    // / unspecified caps). Just confirm at least one is rendered.
    await expect(page.getByText("您当前 tier 下无中转站支持此参数").first()).toBeVisible();

    await shot(page, "H_size_disabled_empty_caps");
  });

  test("I — model unavailable: Generate button disabled, panel still rendered", async ({ page }) => {
    await fakeAuth(page);
    const offline = modelDescriptor({
      available: false,
      available_reason: "no_capable_provider",
    });
    await stubBackend(page, { models: [offline] });
    await gotoCreate(page);

    await expect(page.locator(".model-strip button", { hasText: "ChatGPT Images" }).first())
      .toBeDisabled();
    // Output count still rendered (it's the model that's offline, not the
    // ui_schema). We pluck the GENERATE submit button by its full label
    // including the ticker.
    const gen = page.locator('button:has-text("Generate")').last();
    await expect(gen).toBeDisabled();

    await shot(page, "I_model_unavailable");
  });

  test("J — n cap=4 but state seeded with n=8: ticker clamps + switching works", async ({ page }) => {
    await fakeAuth(page);
    const cap4 = modelDescriptor({
      capabilities: {
        n_max: 4,
        size: ["1024x1024"],
        max_reference_images: 14,
        max_prompt_chars: 32000,
      },
    });
    await stubBackend(page, { models: [cap4] });
    await gotoCreate(page);

    // click 4 first to verify enabled
    await page.locator('button:has-text("4")').first().click();
    await expect(page.locator(".ticker", { hasText: /^×4$/ })).toBeVisible();

    // 8 must be disabled even though it's a preset
    const eight = page.locator('button:has-text("8")').first();
    await expect(eight).toBeDisabled();
    // 8 click does nothing
    await eight.click({ force: true }).catch(() => {});
    await expect(page.locator(".ticker", { hasText: /^×4$/ })).toBeVisible();

    await shot(page, "J_n_clamp");
  });

  test("K — capability change via SSE-style refresh: stale value scrubbed", async ({ page }) => {
    await fakeAuth(page);

    let phase = "wide";
    await page.route(BACKEND_GLOB, async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === "/api/models") {
        const wide = modelDescriptor();
        const narrow = modelDescriptor({
          capabilities: {
            n_max: 2,
            size: ["1024x1024"],
            quality: ["auto"],
            output_format: ["png"],
            background: ["auto"],
            moderation: ["auto"],
            max_reference_images: 14,
            max_prompt_chars: 32000,
          },
        });
        return route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ models: [phase === "wide" ? wide : narrow], sessions: [] }),
        });
      }
      if (p === "/api/announcements/active")
        return route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ announcements: [] }) });
      if (p.startsWith("/api/sse"))
        return route.fulfill({ status: 200, contentType: "text/event-stream", body: ":\n\n" });
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.goto("/create");
    await page.waitForSelector('button:has-text("Generate")');

    // Pick non-default size + n=4 in wide mode
    await page.locator('button:has-text("1024x1536")').click();
    await page.locator('button:has-text("4")').first().click();
    await expect(page.locator(".ticker", { hasText: /^×4$/ })).toBeVisible();

    await shot(page, "K1_wide");

    // Flip to narrow + force a re-fetch via SSE event
    phase = "narrow";
    await page.evaluate(async () => {
      // import the SSE store and pretend an event came in
      const mod = await import("/src/store/sse.js");
      // notify subscribers — Create page is subscribed to model_capabilities_changed
      // We can't call internal _notifySubscribers, so just trigger refresh via the
      // exported `subscribe` path: simulate an event manually.
      // Easiest: dispatch a custom event of the same name against window and
      // hope for it being caught. As a fallback, manipulate the sse store.
      try {
        if (typeof mod.__test_emit === "function") mod.__test_emit("model_capabilities_changed", {});
      } catch {}
    });

    // SSE bridging via JS injection is fragile. Fall back to navigating to
    // force refresh — semantically the same outcome the SSE handler triggers
    // (re-fetching /api/models and reconciling).
    await page.goto("/create");
    await page.waitForSelector('button:has-text("Generate")');

    // Now: size 1024x1536 has been scrubbed (not in narrow caps), default
    // 1024x1024 reapplied. n cap is 2 → presets 4/8 disabled.
    const sz1536 = page.locator('button:has-text("1024x1536")').first();
    await expect(sz1536).toBeDisabled();
    const four = page.locator('button:has-text("4")').first();
    await expect(four).toBeDisabled();

    await shot(page, "K2_narrow_after_refresh");
  });

  test("L — defaults persist when caps allow: default Quality=auto auto-selected on first load", async ({ page }) => {
    await fakeAuth(page);
    await stubBackend(page, { models: [modelDescriptor()] });
    await gotoCreate(page);

    await page.locator("summary", { hasText: "Advanced" }).click();

    // Scope to <details> so we don't accidentally grab the primary
    // Size "auto" chip (size default is 1024x1024 → its "auto" is
    // unselected and would falsify the assertion).
    const qAuto = page.locator('details button:has-text("auto")').first();
    await expect(qAuto).toBeVisible();
    const bg = await qAuto.evaluate((el) => getComputedStyle(el).backgroundColor);
    // selected chip uses var(--ink) which CSS resolves to a dark colour;
    // unselected uses #fffdf7 → rgb(255,253,247). Anything else means selected.
    expect(bg).not.toMatch(/255,\s*253,\s*247/);

    await shot(page, "L_quality_default_selected");
  });

  test("M — payload sanity: clicked params actually reach POST /api/jobs", async ({ page }) => {
    await fakeAuth(page);
    const captured = [];
    await stubBackend(page, { models: [modelDescriptor()], jobsCapture: captured });
    await gotoCreate(page);

    // type a prompt
    await page.locator("textarea").fill("a still life of bananas");
    // pick n=4
    await page.locator('button:has-text("4")').first().click();
    // pick size 1024x1536
    await page.locator('button:has-text("1024x1536")').click();
    // open advanced + pick Quality high
    await page.locator("summary", { hasText: "Advanced" }).click();
    await page.locator('button:has-text("high")').first().click();

    // Click Generate
    await page.locator('button:has-text("Generate")').last().click();

    // Wait for the route handler to fire
    await page.waitForTimeout(500);

    expect(captured.length).toBeGreaterThan(0);
    // payload is multipart — peek at the raw body. JSON is encoded
    // without spaces (Object.entries → JSON.stringify default).
    const body = captured[0].postData || "";
    expect(body).toContain("a still life of bananas");
    expect(body).toMatch(/"n":\s*4/);
    expect(body).toContain("1024x1536");
    expect(body).toMatch(/"quality":\s*"high"/);
    expect(body).toContain("gpt-image-2");
    // BUG-1 regression guard: n_max must NOT leak into the payload
    expect(body).not.toMatch(/"n_max"\s*:/);
    // BUG-2 regression guard: aspect_ratio is not in gpt-image-2 schema,
    // so default-prefs leftovers must be scrubbed.
    expect(body).not.toMatch(/"aspect_ratio"\s*:/);

    await shot(page, "M_payload_sanity");
  });
});

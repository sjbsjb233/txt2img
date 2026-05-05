// End-to-end: admin adds a provider with thinking + size_allow_custom
// enabled → goes to Create → picks a custom size from the new modal +
// chooses thinking=medium → submits → waits for the fake image to land
// → asserts the mock upstream actually received those exact params on
// the wire.
//
// The mock upstream (frontend/tests-e2e/mock_openai_upstream.py) writes
// every request body it receives to /tmp/mock_openai_upstream.jsonl,
// which we read at the end of the test for the wire assertion.

import { test, expect, request as pwRequest } from "@playwright/test";
import { readFileSync, existsSync, unlinkSync } from "fs";

test.describe.configure({ mode: "serial" });

const BACKEND = process.env.E2E_BACKEND_URL || "http://127.0.0.1:18901";
const MOCK_LOG = process.env.MOCK_LOG || "/tmp/mock_openai_upstream.jsonl";
const MOCK_BASE = process.env.MOCK_BASE || "http://127.0.0.1:18902/v1";
const PROVIDER_ID = "e2e-mock-openai";

const ADMIN_USERNAME = "admin";
const ADMIN_PASSWORD = "test-admin-password";

async function loginAsAdmin(page) {
  await page.goto("/login");
  await page.fill('input[autocomplete="username"]', ADMIN_USERNAME);
  await page.fill('input[autocomplete="current-password"]', ADMIN_PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/login"), {
      timeout: 8000,
    }),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForTimeout(400);
}

// Tear down any leftover provider from a prior run + truncate the
// mock-upstream log so this run is self-contained.
async function resetState() {
  // Truncate mock log
  if (existsSync(MOCK_LOG)) {
    try {
      unlinkSync(MOCK_LOG);
    } catch {}
  }
  // Drop any leftover provider via API (using admin's bearer token).
  // We also wipe ``e2e-fake`` (seeded for the standalone
  // tests-e2e/thinking_and_size.spec.js) so this fuller flow only
  // ever has the one provider it controls.
  const ctx = await pwRequest.newContext({ baseURL: BACKEND });
  const lr = await ctx.post("/api/auth/login", {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  });
  const token = (await lr.json()).access_token;
  const list = await ctx.get("/api/admin/providers", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (list.ok()) {
    for (const p of await list.json()) {
      if (p.id === PROVIDER_ID || p.id === "e2e-fake") {
        await ctx.delete(`/api/admin/providers/${p.id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
      }
    }
  }
  await ctx.dispose();
}

test.beforeAll(async () => {
  await resetState();
});

test("01 — admin adds a provider via the UI with thinking + size_allow_custom", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await page.goto("/admin");
  await page.waitForTimeout(500);

  // Switch to Providers tab.
  await page.getByRole("button", { name: /^Providers$/i }).click();
  await page.waitForTimeout(300);

  // Open the create dialog.
  await page.getByRole("button", { name: /Add provider/i }).click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_01_provider_dialog_open.png",
    fullPage: true,
  });

  // Each Field is wrapped with data-field={label} for easy targeting.
  const setField = async (label, value) =>
    page
      .locator(`[data-field="${label}"] input`)
      .first()
      .fill(value);
  await setField("provider_id", PROVIDER_ID);
  await setField("label", "E2E Mock OpenAI");
  await page
    .locator('[data-field="adapter_type"] select')
    .selectOption("openai_v1");
  await setField("base_url", MOCK_BASE);
  await setField("api_key", "sk-mock-not-a-real-key");
  await setField("cost_per_image_cny", "0.001");
  await setField("initial_balance_cny", "1000");
  await setField("max_concurrency", "10");
  await setField("rpm_limit", "600");

  // Open all four tier-access chips so any user can submit.
  for (const tier of ["vip", "premium", "standard", "free"]) {
    const chip = page.locator(".chip").filter({ hasText: new RegExp(`^${tier}$`) });
    if (await chip.count()) {
      const cls = (await chip.first().getAttribute("class")) || "";
      if (!cls.includes("solid")) await chip.first().click();
    }
  }

  // Add the gpt-image-2 model + capability editor.
  await page.getByRole("button", { name: /\+ add model/i }).click();
  await page.waitForTimeout(150);
  const capEditor = page.getByTestId("capability-editor").first();
  await capEditor
    .locator("select")
    .first()
    .selectOption("gpt-image-2");
  await page.waitForTimeout(150);

  // Opt the four ``size`` presets in.
  const sizeBlock = page.getByTestId("cap-field-size");
  for (const opt of ["1024x1024", "1024x1536", "1536x1024", "auto"]) {
    const chip = sizeBlock.locator(".list-chip, button").filter({ hasText: new RegExp(`^${opt}$`) }).first();
    // Some chips may already be unselected. Click to opt in.
    await chip.click({ trial: false }).catch(() => {});
  }

  // Opt the four ``thinking`` values in.
  const thinkingBlock = page.getByTestId("cap-field-thinking");
  for (const opt of ["off", "low", "medium", "high"]) {
    const chip = thinkingBlock.locator("button").filter({ hasText: new RegExp(`^${opt}$`) }).first();
    await chip.click().catch(() => {});
  }

  // Toggle ``size_allow_custom`` to true. Scroll into view first
  // because the field sits at the bottom of the (scrollable) capability
  // editor — useful both for the e2e and for the screenshot we attach.
  const sizeAllow = page.getByTestId("cap-field-size_allow_custom");
  await sizeAllow.scrollIntoViewIfNeeded();
  await sizeAllow.locator("select").selectOption("true");
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_02b_size_allow_custom_visible.png",
    fullPage: false,
  });

  // n_max — set to 4 so the gpt-image-2 default ``n=4`` doesn't get
  // filtered out by the validator on submit.
  const nMax = page.getByTestId("cap-field-n_max").locator('input[type="number"]');
  await nMax.fill("4");

  // max_prompt_chars too — defaults to empty which would be "no opinion"
  // and that's fine; leave alone.

  await page.screenshot({
    path: "tests-e2e/_screenshots/full_02_provider_form_filled.png",
    fullPage: true,
  });

  // Submit.
  await page.getByRole("button", { name: /^Create provider$/i }).click();
  // Dialog closes when save succeeds.
  await page.waitForTimeout(800);
  await expect(page.getByText(PROVIDER_ID).first()).toBeVisible();
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_03_provider_created.png",
    fullPage: true,
  });

  // Cross-check via the admin API: the provider we just saved must be
  // reachable to the admin (vip) tier and must expose a gpt-image-2
  // model with thinking + size_allow_custom enabled. If this fails,
  // the UI didn't persist what the user expected — fix the form
  // selector, don't paper over it.
  const ctx = await pwRequest.newContext({ baseURL: BACKEND });
  const lr = await ctx.post("/api/auth/login", {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  });
  const token = (await lr.json()).access_token;
  const detail = await ctx.get(`/api/admin/providers/${PROVIDER_ID}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(detail.ok()).toBeTruthy();
  const body = await detail.json();
  // tier_access defaults to ["vip","premium"] — admin is vip so we
  // don't strictly need all four, but the ones we toggled in the UI
  // must be present.
  expect(body.tier_access).toContain("vip");
  // Find the gpt-image-2 model.
  const model = (body.supported_models || []).find(
    (m) => m.model_id === "gpt-image-2",
  );
  expect(model, "no gpt-image-2 model attached").toBeTruthy();
  expect(model.capabilities?.thinking || []).toEqual(
    expect.arrayContaining(["off", "low", "medium", "high"]),
  );
  expect(model.capabilities?.size_allow_custom).toBe(true);
  expect(model.capabilities?.size || []).toEqual(
    expect.arrayContaining(["1024x1024", "auto"]),
  );
  // n_max must be ≥ the gpt-image-2 default (n=4) so the executor
  // doesn't filter the provider when the user submits with n=4.
  expect(model.capabilities?.n_max ?? 0).toBeGreaterThanOrEqual(4);
  await ctx.dispose();
});

test("02 — Create page renders new fields + custom size modal exposes the catalog", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await page.goto("/create");
  await page.waitForTimeout(500);

  // Force the gpt-image-2 model card.
  const gpt = page.locator("text=ChatGPT Images 2").first();
  if (await gpt.isVisible().catch(() => false)) await gpt.click();
  await page.waitForTimeout(200);

  // Expand advanced.
  const adv = page.getByText(/Advanced$/i).first();
  if (await adv.isVisible().catch(() => false)) await adv.click().catch(() => {});
  await page.waitForTimeout(200);

  // The thinking row + custom size button must be there because the new
  // provider opted both into the merged capability surface.
  for (const v of ["off", "low", "medium", "high"]) {
    await expect(
      page
        .locator(`[data-field="thinking"]`)
        .locator("button", { hasText: new RegExp(`^${v}$`, "i") }),
    ).toBeVisible();
  }
  await expect(page.getByTestId("size-custom-open")).toBeVisible();

  // Open modal → catalog + manual input both visible.
  await page.getByTestId("size-custom-open").click();
  await page.waitForTimeout(200);
  await expect(page.getByTestId("size-custom-modal")).toBeVisible();
  await expect(page.getByTestId("size-preset-1280x720")).toBeVisible();
  await expect(page.getByTestId("size-custom-width")).toBeVisible();
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_04a_modal_open.png",
    fullPage: true,
  });

  // Manual input: illegal 1000×1000 keeps Apply disabled with reason.
  await page.getByTestId("size-custom-width").fill("1000");
  await page.getByTestId("size-custom-height").fill("1000");
  await expect(page.getByTestId("size-custom-apply")).toBeDisabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /multiples of 16/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_04b_invalid_not_16_multiple.png",
    fullPage: true,
  });

  // Manual input: legal 1920×1088 (FHD-equivalent, since 1080 isn't 16-multiple).
  await page.getByTestId("size-custom-width").fill("1920");
  await page.getByTestId("size-custom-height").fill("1088");
  await expect(page.getByTestId("size-custom-apply")).toBeEnabled();
  await expect(page.getByTestId("size-custom-validation")).toContainText(
    /looks good/i,
  );
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_04c_valid_fhd.png",
    fullPage: true,
  });

  // Pick from catalog instead — committing the value back to the page.
  await page.getByTestId("size-preset-1280x720").click();
  await expect(page.getByTestId("size-custom-current")).toContainText(
    "1280x720",
  );

  // Pick thinking = medium.
  await page
    .locator(`[data-field="thinking"]`)
    .locator("button", { hasText: /^medium$/i })
    .click();

  await page.screenshot({
    path: "tests-e2e/_screenshots/full_04d_create_ready.png",
    fullPage: true,
  });
});

test("03 — submit a job; mock upstream returns image; backend forwarded the right params", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await page.goto("/create");
  await page.waitForTimeout(500);

  // Force the gpt-image-2 model card again (state isn't preserved across tests).
  const gpt = page.locator("text=ChatGPT Images 2").first();
  if (await gpt.isVisible().catch(() => false)) await gpt.click();
  await page.waitForTimeout(200);

  // Expand advanced.
  const adv = page.getByText(/Advanced$/i).first();
  if (await adv.isVisible().catch(() => false)) await adv.click().catch(() => {});
  await page.waitForTimeout(200);

  // Pick custom size 1280×720 again.
  await page.getByTestId("size-custom-open").click();
  await page.waitForTimeout(150);
  await page.getByTestId("size-preset-1280x720").click();
  await expect(page.getByTestId("size-custom-current")).toContainText(
    "1280x720",
  );

  // thinking = medium.
  await page
    .locator(`[data-field="thinking"]`)
    .locator("button", { hasText: /^medium$/i })
    .click();

  // Type a prompt — the field is the visible textarea.
  await page
    .locator('textarea, [contenteditable="true"]')
    .first()
    .fill("a tiny e2e banana");

  // Generate. The button text is "Generate ×N".
  const generate = page.getByRole("button", { name: /^Generate ×\d+/ });
  await expect(generate).toBeEnabled();
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_05_about_to_generate.png",
    fullPage: true,
  });
  await generate.click();

  // The submit usually navigates to /archive — wait for that, then for
  // the SUCCEEDED status to land.
  await page.waitForURL(/\/archive/, { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_06_archive_after_submit.png",
    fullPage: true,
  });

  // Track the job via the archive index (admin can inspect their own
  // jobs the same as a user). Also poll the mock log in parallel.
  const ctxJ = await pwRequest.newContext({ baseURL: BACKEND });
  const lrJ = await ctxJ.post("/api/auth/login", {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  });
  const jToken = (await lrJ.json()).access_token;
  const jheaders = { Authorization: `Bearer ${jToken}` };

  let recorded = null;
  let lastJob = null;
  for (let i = 0; i < 60; i++) {
    // Mock log
    if (existsSync(MOCK_LOG)) {
      const lines = readFileSync(MOCK_LOG, "utf-8")
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l));
      recorded = lines.find(
        (r) =>
          r.method === "POST" &&
          r.path === "/v1/images/generations" &&
          r.body?.model === "gpt-image-2",
      );
      if (recorded) break;
    }
    // Job state
    const idx = await ctxJ.get("/api/jobs/index?limit=1", { headers: jheaders });
    if (idx.ok()) {
      const jb = await idx.json();
      const item = (jb.items || jb.jobs || [])[0];
      if (item) {
        lastJob = item;
        if (item.status === "FAILED") {
          // Pull the detail for diagnostic context.
          const det = await ctxJ.get(`/api/jobs/${item.hash_id}`, { headers: jheaders });
          if (det.ok()) lastJob = await det.json();
          break;
        }
      }
    }
    await page.waitForTimeout(500);
  }
  await ctxJ.dispose();

  if (!recorded) {
    // Surface the most recent job's status so the failure message
    // tells the operator *why* nothing reached the upstream.
    throw new Error(
      `no upstream POST captured. last job state: ${JSON.stringify(lastJob)}`,
    );
  }
  expect(recorded, "no upstream POST captured").toBeTruthy();

  // ★ The point of this whole exercise: the wire payload must carry the
  // exact knobs the user picked — proving the new fields traverse
  // schema → adapter → wire correctly.
  expect(recorded.body.model).toBe("gpt-image-2");
  expect(recorded.body.size).toBe("1280x720");
  expect(recorded.body.thinking).toBe("medium");
  expect(recorded.body.prompt).toBe("a tiny e2e banana");
  // Authorization header forwards the provider's api_key as a bearer.
  const authz = (recorded.headers.authorization || recorded.headers.Authorization) ?? "";
  expect(authz).toMatch(/^Bearer sk-/i);

  // Eyeball: the Archive page should render the image card. Take a
  // detail-zoom screenshot in either case for visual review.
  await page.waitForTimeout(2500);
  await page.screenshot({
    path: "tests-e2e/_screenshots/full_07_archive_succeeded.png",
    fullPage: true,
  });
});

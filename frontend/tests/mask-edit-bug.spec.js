// Regression guard for the "This model doesn't support mask editing."
// alert that fired for every model on /edit/:hash/:order after PR
// commit 98358a5 ("feat(mask-editor): v2 dual route") introduced a
// lookup keyed by the wrong field:
//
//   const row = (resp.models || []).find((m) => m.model === job.model);
//
// `/api/models` returns descriptors keyed by `model_id` — so the lookup
// always missed, `caps` was null, and `pickMaskMethod(null)` returned
// "unsupported". Fix: compare `m.model_id` against `job.model`.

import { test, expect } from "@playwright/test";
import fs from "node:fs";

const HASH = "testhash01";
const ORDER = 1;

const SOURCE_PNG_PATH = "/tmp/mask-test-source.png";
const SOURCE_PNG_BYTES = fs.readFileSync(SOURCE_PNG_PATH);

// Mimics the production /api/models payload (extracted from the
// uploaded data.zip's txt2img.db). Only the fields the editor reads
// are populated.
function modelsPayload() {
  return {
    models: [
      {
        model_id: "gpt-image-2",
        display_name: "ChatGPT Images 2.0",
        tag: "RECOMMENDED",
        logo: "chatgpt",
        blurb: "",
        available: true,
        available_reason: null,
        capabilities: {
          size: ["1024x1024", "1024x1536", "1536x1024", "auto"],
          quality: ["auto", "high", "low", "medium"],
          output_format: ["png", "webp", "jpeg"],
          background: ["auto", "opaque"],
          moderation: ["auto", "low"],
          thinking: ["off", "low", "medium", "high"],
          n_max: 6,
          max_reference_images: 10,
          supports_mask: true,
          size_allow_custom: true,
        },
        defaults: {
          n: 4,
          size: "auto",
          quality: "auto",
          output_format: "png",
          background: "auto",
          moderation: "auto",
        },
        ui_schema: [],
      },
    ],
    sessions: [],
    meta: {
      batch_concurrency_max: 4,
      batch_max_concurrent_per_user: 3,
      batch_slots_max: 50,
      batch_slot_image_count_max: 16,
      batch_total_images_max: 400,
    },
  };
}

function jobPayload() {
  return {
    hash_id: HASH,
    user_id: "u-test",
    session_id: null,
    batch_id: null,
    set_id: null,
    seq_no: 1,
    model: "gpt-image-2",
    prompt: "a serene mountain landscape",
    params: {},
    status: "SUCCEEDED",
    error_code: null,
    error_message: null,
    n: 1,
    submitted_at: "2026-05-12T00:00:00Z",
    started_at: "2026-05-12T00:00:01Z",
    completed_at: "2026-05-12T00:00:05Z",
    references: [],
    images: [
      {
        order: ORDER,
        starred: false,
        width: 512,
        height: 384,
        size_bytes: SOURCE_PNG_BYTES.length,
        mime: "image/png",
      },
    ],
  };
}

async function seedAuthAndCaptureAlerts(page) {
  // Bypass RequireAuth via the same storage keys api/client.js uses.
  // Also patch window.alert so its message lands in window.__alerts —
  // without that, the editor's redirect-on-unsupported branch would
  // happen behind a blocking JS dialog and Playwright couldn't observe
  // it cleanly.
  await page.addInitScript(() => {
    localStorage.setItem("token", "test-bearer");
    localStorage.setItem(
      "user",
      JSON.stringify({
        id: "u-test",
        username: "tester",
        role: "user",
        tier: "free",
      })
    );
    window.__alerts = [];
    window.alert = (msg) => {
      window.__alerts.push(String(msg));
    };
  });
}

async function stubRoutes(page) {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "u-test",
        username: "tester",
        role: "user",
        tier: "free",
      }),
    })
  );

  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(modelsPayload()),
    })
  );

  await page.route(`**/api/jobs/${HASH}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(jobPayload()),
    })
  );

  await page.route(`**/api/jobs/${HASH}/derived`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    })
  );

  await page.route(`**/api/jobs/${HASH}/images/${ORDER}/original`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/png",
      body: SOURCE_PNG_BYTES,
    })
  );

  await page.route("**/api/jobs/index*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    })
  );
}

test("mask editor opens for gpt-image-2 without 'unsupported' alert", async ({
  page,
}, testInfo) => {
  await seedAuthAndCaptureAlerts(page);
  await stubRoutes(page);

  await page.goto(`/edit/${HASH}/${ORDER}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500);

  const alerts = await page.evaluate(() => window.__alerts || []);
  const url = page.url();

  await page.screenshot({
    path: testInfo.outputPath("editor-loaded.png"),
    fullPage: true,
  });

  expect(alerts).not.toContain("This model doesn't support mask editing.");
  expect(url).toContain(`/edit/${HASH}/${ORDER}`);
});

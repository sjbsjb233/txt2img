// E2E test for the Admin Job Inspector inline accordion.
//
// Strategy: stub every /api/* call so the test runs against a built
// frontend served by `vite preview` without needing a live backend.
// This keeps the test deterministic and focused on the new UI surface.

import { test, expect } from "@playwright/test";

const ADMIN_USER_ID = "u_admin1";
const TARGET_USER_ID = "u_target1";
const SUCCESS_HASH = "j_aaaaaaaaaaaa";
const FAILED_HASH = "j_bbbbbbbbbbbb";

const NOW = new Date("2026-05-05T08:00:00Z").toISOString();

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

function userDetailFixture() {
  return {
    id: TARGET_USER_ID,
    username: "alice",
    display_name: "Alice",
    role: "user",
    tier: "premium",
    status: "active",
    today_count: 7,
    soft_quota_effective: 30,
    hard_quota_effective: 60,
    override_soft_quota: null,
    override_hard_quota: null,
    created_at: "2026-04-01T10:00:00Z",
    last_login_at: "2026-05-05T07:00:00Z",
    jobs_30d_total: 42,
    jobs_30d_success: 36,
    jobs_30d_failed: 4,
    daily_usage: Array.from({ length: 30 }).map((_, i) => ({
      date: `2026-04-${String(i + 5).padStart(2, "0")}`,
      jobs_count: i % 3,
      images_count: i % 3,
      success_count: i % 3 === 0 ? 0 : 1,
      avg_render_seconds: 4.2,
    })),
    top_models: [
      { key: "gemini-3.1-flash-image-preview", count: 22 },
      { key: "imagen-3.0-fast", count: 9 },
    ],
    top_providers: [{ key: "bltcy", count: 30 }],
    active_session_count: 1,
  };
}

function jobsListFixture() {
  return {
    items: [
      {
        hash_id: SUCCESS_HASH,
        seq_no: 9,
        model: "gemini-3.1-flash-image-preview",
        status: "SUCCEEDED",
        status_reason: null,
        provider_used: "bltcy",
        retries: 0,
        cost_cny: 0.12,
        set_id: null,
        session_id: null,
        created_at: "2026-05-05T07:55:00Z",
        finished_at: "2026-05-05T07:55:18Z",
      },
      {
        hash_id: FAILED_HASH,
        seq_no: 8,
        model: "gemini-3.1-flash-image-preview",
        status: "FAILED",
        status_reason: "ALL_PROVIDERS_FAILED",
        provider_used: null,
        retries: 3,
        cost_cny: 0,
        set_id: null,
        session_id: null,
        created_at: "2026-05-05T07:30:00Z",
        finished_at: "2026-05-05T07:30:42Z",
      },
    ],
    page: 1,
    page_size: 50,
    total: 2,
  };
}

function inspectFixture(hashId) {
  if (hashId === SUCCESS_HASH) {
    return {
      hash_id: SUCCESS_HASH,
      seq_no: 9,
      status: "SUCCEEDED",
      status_reason: null,
      model: "gemini-3.1-flash-image-preview",
      model_display_name: "Gemini 3.1 Flash Image",
      updated_at: NOW,
      set_id: null,
      session_id: null,
      prompt: "a cat sitting on a windowsill at golden hour",
      params: { aspect_ratio: "16:9", n: 1, quality: "standard" },
      references: [],
      set: null,
      images: [
        {
          image_id: "img_1",
          order: 1,
          thumb_url: "/api/jobs/" + SUCCESS_HASH + "/images/1/thumb",
          download_url:
            "/api/jobs/" + SUCCESS_HASH + "/images/1/original",
          width: 1280,
          height: 720,
          format: "webp",
          file_size_bytes: 224000,
          starred: true,
        },
      ],
      session: null,
      timing: {
        queued_at: "2026-05-05T07:55:00Z",
        started_at: "2026-05-05T07:55:01Z",
        finished_at: "2026-05-05T07:55:18Z",
        queue_seconds: 1.0,
        render_seconds: 17.0,
      },
      error: null,
      flags: { promoted: false },
      user_id: TARGET_USER_ID,
      user_username: "alice",
      provider_used: "bltcy",
      retries: 0,
      cost_cny: 0.12,
      balance_after_cny: 12.34,
      lifecycle: {
        queued_at: "2026-05-05T07:55:00Z",
        dispatched_at: "2026-05-05T07:55:00.5Z",
        started_at: "2026-05-05T07:55:01Z",
        finished_at: "2026-05-05T07:55:18Z",
        queued_seconds: 0.5,
        admission_seconds: 0.5,
        routing_seconds: 0.2,
        attempts_seconds: 16.0,
        finalize_seconds: 0.3,
      },
      user_state_at_submit: {
        tier: "premium",
        today_count: 7,
        soft_quota_effective: 30,
        hard_quota_effective: 60,
        soft_quota_triggered: false,
        captcha_required: false,
        captcha_verified: false,
        recent_fail_rate_n: 0,
        recent_fail_rate_total: 10,
      },
      routing: {
        pool_total: 4,
        pool_survived: 2,
        pool_scored: 2,
        filtered_out: [
          {
            provider_id: "openai-east-1",
            label: "OpenAI East 1",
            reason: "circuit_open",
            detail: "state=open",
          },
          {
            provider_id: "imagen-cn",
            label: "Imagen CN",
            reason: "tier_denied",
            detail: "tier 'premium' not in ['vip']",
          },
        ],
        scored: [
          {
            rank: 1,
            provider_id: "bltcy",
            label: "Bltcy",
            components: {
              cost: 0.92,
              success: 1.0,
              latency: 0.78,
              load: 0.95,
              freshness: 0.88,
            },
            total_score: 0.91,
            chosen: true,
          },
          {
            rank: 2,
            provider_id: "fal-ai",
            label: "fal.ai",
            components: {
              cost: 0.55,
              success: 0.99,
              latency: 0.6,
              load: 0.9,
              freshness: 0.7,
            },
            total_score: 0.74,
            chosen: false,
          },
        ],
        selector_config: {
          cost: 0.2,
          success: 0.3,
          latency: 0.2,
          load: 0.15,
          freshness: 0.15,
        },
      },
      attempts: [
        {
          attempt_no: 1,
          provider_id: "bltcy",
          provider_label: "Bltcy",
          started_at: "2026-05-05T07:55:01.5Z",
          latency_ms: 15800,
          ok: true,
          error_kind: null,
          upstream_status: 200,
          upstream_body_excerpt: null,
          provider_snapshot: {
            circuit_state: "healthy",
            success_rate_5m: 0.97,
            p50_latency_ms: 12400,
            current_concurrency: 1,
            max_concurrency: 8,
            current_rpm: 14,
            rpm_limit: 60,
            extra: {},
          },
          raw_log_url:
            "/api/admin/jobs/" + SUCCESS_HASH + "/upstream/1",
          chosen: true,
        },
      ],
      circuit_ripples: [],
      degraded_sections: [],
    };
  }

  // FAILED job — multiple attempts, routing trace, error reason
  return {
    hash_id: FAILED_HASH,
    seq_no: 8,
    status: "FAILED",
    status_reason: "ALL_PROVIDERS_FAILED",
    model: "gemini-3.1-flash-image-preview",
    model_display_name: "Gemini 3.1 Flash Image",
    updated_at: NOW,
    set_id: null,
    session_id: null,
    prompt: "an oil painting of a thunderstorm",
    params: { n: 1 },
    references: [],
    set: null,
    images: [],
    session: null,
    timing: {
      queued_at: "2026-05-05T07:30:00Z",
      started_at: "2026-05-05T07:30:01Z",
      finished_at: "2026-05-05T07:30:42Z",
      queue_seconds: 1.0,
      render_seconds: 41.0,
    },
    error: "ALL_PROVIDERS_FAILED",
    flags: {},
    user_id: TARGET_USER_ID,
    user_username: "alice",
    provider_used: null,
    retries: 3,
    cost_cny: 0,
    balance_after_cny: null,
    lifecycle: {
      queued_at: "2026-05-05T07:30:00Z",
      dispatched_at: "2026-05-05T07:30:00.4Z",
      started_at: "2026-05-05T07:30:01Z",
      finished_at: "2026-05-05T07:30:42Z",
      queued_seconds: 0.4,
      admission_seconds: 0.6,
      routing_seconds: 0.5,
      attempts_seconds: 39.5,
      finalize_seconds: 0.5,
    },
    user_state_at_submit: {
      tier: "premium",
      today_count: 12,
      soft_quota_effective: 30,
      hard_quota_effective: 60,
      soft_quota_triggered: false,
      captcha_required: false,
      captcha_verified: false,
      recent_fail_rate_n: 2,
      recent_fail_rate_total: 10,
    },
    routing: {
      pool_total: 4,
      pool_survived: 3,
      pool_scored: 3,
      filtered_out: [
        {
          provider_id: "imagen-cn",
          label: "Imagen CN",
          reason: "tier_denied",
          detail: null,
        },
      ],
      scored: [
        {
          rank: 1,
          provider_id: "bltcy",
          label: "Bltcy",
          components: { cost: 0.9, success: 0.5, latency: 0.6, load: 0.8, freshness: 0.7 },
          total_score: 0.7,
          chosen: false,
        },
        {
          rank: 2,
          provider_id: "openai-east-1",
          label: "OpenAI East 1",
          components: { cost: 0.6, success: 0.6, latency: 0.7, load: 0.7, freshness: 0.5 },
          total_score: 0.62,
          chosen: false,
        },
        {
          rank: 3,
          provider_id: "fal-ai",
          label: "fal.ai",
          components: { cost: 0.4, success: 0.5, latency: 0.5, load: 0.7, freshness: 0.6 },
          total_score: 0.52,
          chosen: false,
        },
      ],
      selector_config: { cost: 0.2, success: 0.3, latency: 0.2, load: 0.15, freshness: 0.15 },
    },
    attempts: [
      {
        attempt_no: 1,
        provider_id: "bltcy",
        provider_label: "Bltcy",
        started_at: "2026-05-05T07:30:01.5Z",
        latency_ms: 12100,
        ok: false,
        error_kind: "RATE_LIMITED",
        upstream_status: 429,
        upstream_body_excerpt: "rate limit exceeded; retry-after 30s",
        provider_snapshot: {
          circuit_state: "healthy",
          success_rate_5m: 0.5,
          p50_latency_ms: 9000,
          current_concurrency: 8,
          max_concurrency: 8,
          current_rpm: 60,
          rpm_limit: 60,
          extra: {},
        },
        raw_log_url: "/api/admin/jobs/" + FAILED_HASH + "/upstream/1",
        chosen: false,
      },
      {
        attempt_no: 2,
        provider_id: "openai-east-1",
        provider_label: "OpenAI East 1",
        started_at: "2026-05-05T07:30:14Z",
        latency_ms: 22000,
        ok: false,
        error_kind: "UPSTREAM_TIMEOUT",
        upstream_status: 504,
        upstream_body_excerpt: "Gateway Time-out — upstream did not respond in 22s",
        provider_snapshot: {
          circuit_state: "half_open",
          success_rate_5m: 0.4,
          p50_latency_ms: 18000,
          current_concurrency: 2,
          max_concurrency: 4,
          current_rpm: 30,
          rpm_limit: 50,
          extra: {},
        },
        raw_log_url: "/api/admin/jobs/" + FAILED_HASH + "/upstream/2",
        chosen: false,
      },
      {
        attempt_no: 3,
        provider_id: "fal-ai",
        provider_label: "fal.ai",
        started_at: "2026-05-05T07:30:36Z",
        latency_ms: 5300,
        ok: false,
        error_kind: "AUTH",
        upstream_status: 401,
        upstream_body_excerpt: "Unauthorized — [REDACTED]",
        provider_snapshot: {
          circuit_state: "healthy",
          success_rate_5m: null,
          p50_latency_ms: null,
          current_concurrency: 0,
          max_concurrency: 4,
          current_rpm: 0,
          rpm_limit: 30,
          extra: {},
        },
        raw_log_url: "/api/admin/jobs/" + FAILED_HASH + "/upstream/3",
        chosen: false,
      },
    ],
    circuit_ripples: [
      {
        provider_id: "openai-east-1",
        label: "OpenAI East 1",
        transition: "half_open → open",
        triggered_by_attempt: 2,
      },
    ],
    degraded_sections: [],
  };
}

// ---------------------------------------------------------------------------
// Page setup — pre-seed localStorage and stub /api/*
// ---------------------------------------------------------------------------

async function setupAdminPage(page) {
  // Pre-seed token + API base so apiFetch sends Authorization headers and
  // calls land on the same origin (we'll route them).
  await page.addInitScript(() => {
    localStorage.setItem("token", "test-admin-token");
    localStorage.setItem("api_base", "");
    localStorage.setItem(
      "user",
      JSON.stringify({
        id: "u_admin1",
        username: "admin",
        display_name: "Admin",
        role: "admin",
        tier: "vip",
        status: "active",
      }),
    );
  });

  // Fallback API stubs FIRST so unrelated calls (overview tab, etc.) don't
  // hang. Playwright matches the most-recently registered route first, so
  // the specific routes below override this catch-all.
  await page.route("**/api/**", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }),
    });
  });

  // /api/auth/me → admin role gate
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: ADMIN_USER_ID,
        username: "admin",
        display_name: "Admin",
        role: "admin",
        tier: "vip",
        status: "active",
        today_count: 0,
        soft_quota_effective: 999,
        hard_quota_effective: 9999,
      }),
    });
  });

  // Users list
  await page.route(/\/api\/admin\/users(\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: TARGET_USER_ID,
            username: "alice",
            display_name: "Alice",
            role: "user",
            tier: "premium",
            status: "active",
            today_count: 7,
            soft_quota_effective: 30,
            hard_quota_effective: 60,
            created_at: "2026-04-01T10:00:00Z",
            last_login_at: "2026-05-05T07:00:00Z",
            jobs_30d_total: 42,
            jobs_30d_success: 36,
            jobs_30d_failed: 4,
          },
        ],
        page: 1,
        page_size: 50,
        total: 1,
      }),
    });
  });

  // Single user detail
  await page.route(`**/api/admin/users/${TARGET_USER_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(userDetailFixture()),
    });
  });

  // Jobs list under that user
  await page.route(
    new RegExp(`/api/admin/users/${TARGET_USER_ID}/jobs`),
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(jobsListFixture()),
      });
    },
  );

  // Inspect endpoint
  await page.route(/\/api\/admin\/jobs\/(j_\w+)\/inspect/, async (route) => {
    const m = route.request().url().match(/jobs\/(j_\w+)\/inspect/);
    const hashId = m ? m[1] : "unknown";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(inspectFixture(hashId)),
    });
  });

}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Admin Job Inspector", () => {
  test("expanding a successful job row reveals the inspector", async ({
    page,
  }) => {
    await setupAdminPage(page);
    await page.goto("/admin");

    // Switch to Users tab if not already.
    const usersTab = page.locator("[data-admin-tab=users]").first();
    if (await usersTab.isVisible()) {
      await usersTab.click();
    }

    // Click the alice row.
    await page.locator(`[data-user-row="${TARGET_USER_ID}"]`).first().click();

    // Wait for the user detail to render then click "load" to fetch jobs.
    await page.getByText("RECENT JOBS").waitFor();
    await page.getByRole("button", { name: /^load$/ }).click();

    // The success row should appear.
    const succRow = page.locator(`[data-hash="${SUCCESS_HASH}"]`).first();
    await expect(succRow).toBeVisible();

    // Click the row to expand.
    await succRow.click();

    // Wait for the inspector to load.
    const inspector = page.locator(
      `[data-testid=job-inspector][data-hash="${SUCCESS_HASH}"]`,
    );
    await expect(inspector).toBeVisible();
    await expect(inspector).toHaveAttribute("data-state", "ready", {
      timeout: 5000,
    });

    // Lifecycle, routing, attempts, outcome should all be present.
    await expect(inspector.locator("[data-testid=ji-lifecycle]")).toBeVisible();
    await expect(inspector.locator("[data-testid=ji-routing-summary]")).toBeVisible();
    await expect(inspector.locator("[data-testid=ji-attempt]")).toHaveCount(1);
    await expect(inspector.locator("[data-testid=ji-outcome]")).toBeVisible();

    // The chosen scoring row contains a ✓.
    await expect(
      inspector.locator("[data-testid=ji-routing-scored]"),
    ).toContainText("✓");

    await inspector.scrollIntoViewIfNeeded();
    await inspector.screenshot({
      path: "test-results/inspector-success.png",
    });
    await page.screenshot({
      path: "test-results/inspector-success-fullpage.png",
      fullPage: true,
    });
  });

  test("expanding a failed job shows attempt chain + error reason", async ({
    page,
  }) => {
    await setupAdminPage(page);
    await page.goto("/admin");

    const usersTab = page.locator("[data-admin-tab=users]").first();
    if (await usersTab.isVisible()) {
      await usersTab.click();
    }
    await page.locator(`[data-user-row="${TARGET_USER_ID}"]`).first().click();
    await page.getByText("RECENT JOBS").waitFor();
    await page.getByRole("button", { name: /^load$/ }).click();

    const failRow = page.locator(`[data-hash="${FAILED_HASH}"]`).first();
    await expect(failRow).toBeVisible();
    await failRow.click();

    const inspector = page.locator(
      `[data-testid=job-inspector][data-hash="${FAILED_HASH}"]`,
    );
    await expect(inspector).toHaveAttribute("data-state", "ready", {
      timeout: 5000,
    });

    // Three attempts, all failed.
    await expect(inspector.locator("[data-testid=ji-attempt]")).toHaveCount(3);
    await expect(
      inspector.locator("[data-testid=ji-attempt][data-ok='1']"),
    ).toHaveCount(0);

    // Status reason rendered red bold inside outcome block.
    await expect(inspector.locator("[data-testid=ji-outcome]")).toContainText(
      "ALL_PROVIDERS_FAILED",
    );

    // Both rows expanded simultaneously: open the success row too.
    await page.locator(`[data-hash="${SUCCESS_HASH}"]`).first().click();
    await expect(
      page.locator(`[data-testid=job-inspector][data-hash="${SUCCESS_HASH}"]`),
    ).toBeVisible();

    await inspector.scrollIntoViewIfNeeded();
    await inspector.screenshot({
      path: "test-results/inspector-failed.png",
    });
    await page.screenshot({
      path: "test-results/inspector-failed-and-success.png",
      fullPage: true,
    });
  });

  test("collapsed by default; toggle indicator flips on expand", async ({
    page,
  }) => {
    await setupAdminPage(page);
    await page.goto("/admin");
    const usersTab = page.locator("[data-admin-tab=users]").first();
    if (await usersTab.isVisible()) {
      await usersTab.click();
    }
    await page.locator(`[data-user-row="${TARGET_USER_ID}"]`).first().click();
    await page.getByText("RECENT JOBS").waitFor();
    await page.getByRole("button", { name: /^load$/ }).click();

    const succRow = page
      .locator(`[data-testid=admin-job-row][data-hash="${SUCCESS_HASH}"]`)
      .first();
    await expect(succRow).toHaveAttribute("data-expanded", "0");
    await expect(succRow.locator("[data-testid=admin-job-row-toggle]")).toHaveText(
      "▸",
    );
    // Collapsed-state shot before expanding.
    await page
      .getByText("RECENT JOBS")
      .first()
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "test-results/inspector-collapsed.png",
      fullPage: true,
    });

    await succRow.click();
    await expect(succRow).toHaveAttribute("data-expanded", "1");
    await expect(succRow.locator("[data-testid=admin-job-row-toggle]")).toHaveText(
      "▾",
    );
    // Expanded toggle highlight shot.
    await succRow.scrollIntoViewIfNeeded();
    await succRow.screenshot({
      path: "test-results/inspector-row-highlight.png",
    });
  });
});

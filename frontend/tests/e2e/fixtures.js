// Shared test helpers for the Create-page sticky persistence suite.
//
// Why this module exists:
//   - Every test needs to seed a logged-in user (token + cached user)
//     and intercept the same set of backend endpoints.
//   - The model catalog has to be small enough to read at a glance but
//     wide enough to test "switch between two models" — two real-shape
//     descriptors fit the bill.

export const USER = { id: "u_test_sticky", role: "user", email: "test@example.com" };
export const TOKEN = "test-token-sticky";

// Two model descriptors with overlapping but distinct ui_schemas. Both
// expose aspect_ratio + n; only Gemini exposes ``quality``. That gives
// us a clean "schema-validation drops a stale field" test surface.
export const MODELS = [
  {
    model_id: "openai-gpt-image-1",
    display_name: "GPT-Image",
    tag: "FAST",
    logo: "chatgpt",
    blurb: "OpenAI’s image generator.",
    available: true,
    available_reason: null,
    capabilities: {
      aspect_ratio: ["1:1", "3:2", "16:9"],
      quality: null,
      n_max: 4,
      max_reference_images: 8,
      max_prompt_chars: 4000,
    },
    defaults: { aspect_ratio: "1:1", n: 1 },
    ui_schema: [
      {
        k: "aspect_ratio",
        control: "chip-grid",
        label: "Shape",
        hint: "aspect ratio",
        group: "primary",
        order: 10,
        options: ["1:1", "3:2", "16:9"],
      },
      {
        k: "n_max",
        value_key: "n",
        control: "number",
        label: "Output count",
        group: "primary",
        order: 20,
        presets: [1, 2, 4],
      },
    ],
  },
  {
    model_id: "google-gemini-2.5-flash-image",
    display_name: "Gemini Flash",
    tag: "PRO",
    logo: "flash",
    blurb: "Google’s fast image generator.",
    available: true,
    available_reason: null,
    capabilities: {
      aspect_ratio: ["1:1", "3:2", "16:9"],
      quality: ["standard", "hd"],
      n_max: 1,
      max_reference_images: 4,
      max_prompt_chars: 8000,
    },
    defaults: { aspect_ratio: "16:9", quality: "standard", n: 1 },
    ui_schema: [
      {
        k: "aspect_ratio",
        control: "chip-grid",
        label: "Shape",
        hint: "aspect ratio",
        group: "primary",
        order: 10,
        options: ["1:1", "3:2", "16:9"],
      },
      {
        k: "quality",
        control: "chip-row",
        label: "Quality",
        group: "primary",
        order: 30,
        options: ["standard", "hd"],
      },
    ],
  },
];

const PREFS = {
  generation: {
    default_model_id: "openai-gpt-image-1",
    default_aspect_ratio: "1:1",
    default_batch_size: 1,
    auto_bind_session: true,
    auto_retry: true,
    remember_prompt_history: true,
  },
  notifications: {
    browser_on_complete: false,
    sound_on_complete: false,
    sound_volume: 60,
    desktop_badge: true,
    announcements_level: "all",
  },
  appearance: {
    theme: "system",
    density: "comfortable",
    interface_scale: "default",
    reduce_motion: false,
    high_contrast: false,
    show_keyboard_hints: true,
  },
  privacy: {
    auto_delete_after_days: null,
    encrypted_at_rest: false,
    archive_visibility: "private",
    log_retention_days: 30,
  },
  workflow: {
    default_landing_page: "create",
    confirm_on_navigate_with_unsaved: true,
  },
};

/**
 * Install all the API fakes the Create page touches. Catalog is the
 * common case; tests that need to override a specific endpoint can
 * call `page.route` again afterwards.
 */
export async function stubBackend(page, { models = MODELS, sessions = [] } = {}) {
  await page.route(/127\.0\.0\.1:8000\/api\/me\/preferences$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PREFS),
    });
  });

  await page.route(/127\.0\.0\.1:8000\/api\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(USER),
    });
  });

  await page.route(/127\.0\.0\.1:8000\/api\/models(\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ models, sessions }),
    });
  });

  // SSE / archive / announcements — return empty bodies so the page
  // mounts cleanly without hammering the network.
  await page.route(/127\.0\.0\.1:8000\/api\/announcements/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    });
  });
  await page.route(/127\.0\.0\.1:8000\/api\/sse/, async (route) => {
    // Long-lived stream; just dribble an "open" event and stay alive.
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "event: hello\ndata: {}\n\n",
    });
  });
  await page.route(/127\.0\.0\.1:8000\/api\/archive/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    });
  });
}

/**
 * Run before page.goto to seed the auth tuple in localStorage so
 * RequireAuth lets us in without going through the real login flow.
 */
export async function seedAuth(page) {
  await page.addInitScript(
    ({ token, user }) => {
      try {
        localStorage.setItem("token", token);
        localStorage.setItem("user", JSON.stringify(user));
      } catch {
        // ignored
      }
    },
    { token: TOKEN, user: USER }
  );
}

/**
 * Read the sticky payload as a parsed object (or null) from the page
 * origin's localStorage. Centralised so tests don't repeat the key.
 */
export async function readSticky(page) {
  return page.evaluate(() => {
    const raw = localStorage.getItem("txt2img:create:sticky:v1");
    return raw ? JSON.parse(raw) : null;
  });
}

export async function readDraftV2(page) {
  return page.evaluate(() => {
    const raw = localStorage.getItem("txt2img:create:draft:v2");
    return raw ? JSON.parse(raw) : null;
  });
}

export async function readDraftV1(page) {
  return page.evaluate(() => {
    const raw = localStorage.getItem("txt2img:create:draft:v1");
    return raw ? JSON.parse(raw) : null;
  });
}

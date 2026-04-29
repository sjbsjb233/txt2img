/**
 * Shared helpers for archive page tests.
 *
 * Injects fake archive data via:
 *  1. localStorage for auth state
 *  2. IndexedDB pre-population so the archive store's hydration step
 *     returns real rows immediately (no backend required)
 *  3. Network mocks to handle the delta-sync and thumbnail requests
 */

export const FAKE_USER = {
  id: "user-test-001",
  email: "test@example.com",
  role: "user",
  name: "Test User",
};

export const FAKE_TOKEN = "test.fake.jwt.token";

/** Build N fake SUCCEEDED job rows. */
export function makeFakeJobs(n = 12) {
  return Array.from({ length: n }, (_, i) => {
    const hashId = `fakehash${String(i).padStart(4, "0")}`;
    const seqNo = 1000 + i;
    const updatedAt = new Date(Date.now() - i * 60_000).toISOString();
    return {
      hash_id: hashId,
      set_id: null,
      seq_no: seqNo,
      status: "SUCCEEDED",
      model: "gemini-3-pro-image-preview",
      model_display_name: "Gemini 3 Pro",
      prompt: `Test prompt number ${i + 1} — a beautiful landscape with mountains and a river flowing through a valley at sunset`,
      params: { size: "1024x1024", quality: "standard" },
      references: [],
      images: [
        {
          order: 0,
          width: 1024,
          height: 1024,
          thumb_url: `http://127.0.0.1:8000/api/jobs/${hashId}/images/0/thumb`,
          starred: i === 0,
          format: "png",
        },
      ],
      session: { id: "sess-001", name: "Test Session" },
      timing: {
        queued_at: new Date(Date.now() - i * 60_000 - 5000).toISOString(),
        queue_seconds: 1.2,
        render_seconds: 3.4,
      },
      error: null,
      flags: {},
      updated_at: updatedAt,
      _hydrated: true,
      _localUpdatedAt: updatedAt,
    };
  });
}

/**
 * Build the addInitScript payload that:
 *  1. Sets localStorage auth
 *  2. Pre-populates IndexedDB "txt2img" / "archive_<userId>" with jobs
 *
 * Returns a string suitable for page.addInitScript().
 */
export function makeFullInitScript(jobs = makeFakeJobs()) {
  const user = JSON.stringify(FAKE_USER);
  const jobsJson = JSON.stringify(jobs);
  const storeName = `archive_${FAKE_USER.id}`;

  return `
    // ── Auth ──────────────────────────────────────────────────────────────
    localStorage.setItem('token', '${FAKE_TOKEN}');
    localStorage.setItem('user', ${JSON.stringify(user)});

    // ── Pre-populate IndexedDB so archiveDB.getAll() returns rows ─────────
    (function seedIDB() {
      const DB_NAME = "txt2img";
      const STORE = "${storeName}";
      const jobs = ${jobsJson};

      const openReq = indexedDB.open(DB_NAME, 1);
      openReq.onupgradeneeded = function(e) {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "hash_id" });
          store.createIndex("updated_at", "updated_at", { unique: false });
          store.createIndex("set_id", "set_id", { unique: false });
          store.createIndex("session_id", "session_id", { unique: false });
          store.createIndex("status", "status", { unique: false });
        }
      };
      openReq.onsuccess = function(e) {
        const db = e.target.result;
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        for (const job of jobs) {
          store.put({ ...job, _localUpdatedAt: Date.now() });
        }
        tx.oncomplete = function() { db.close(); };
      };
    })();
  `;
}

/**
 * Wire up network mocks needed for the archive page sync and thumbnails.
 * Call before navigation.
 *
 * IMPORTANT: Playwright matches routes from LAST-registered to FIRST-registered.
 * The most specific routes must be registered LAST so they take priority.
 */
export async function setupArchiveMocks(page, jobs = makeFakeJobs()) {
  const API_BASE = "http://127.0.0.1:8000";

  // Small solid-color 1×1 PNG for thumbnails
  const THUMB_PNG = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    "base64"
  );

  // 1. Catch-all (lowest priority — registered first)
  await page.route(`${API_BASE}/**`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  );

  // 2. SSE — abort so the connection doesn't hang
  await page.route(`${API_BASE}/api/sse*`, (route) => route.abort());
  await page.route(`${API_BASE}/api/events*`, (route) => route.abort());

  // 3. Jobs index — return empty (IDB already has data from initScript)
  await page.route(`${API_BASE}/api/jobs/index*`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    })
  );

  // 4. Jobs details batch
  await page.route(`${API_BASE}/api/jobs/details`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: jobs }),
    })
  );

  // 5. Job thumbnails — must be registered LAST so it wins over catch-all
  //    Pattern: /api/jobs/<hash>/images/<order>/thumb
  await page.route(`${API_BASE}/api/jobs/*/images/*/thumb`, (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: THUMB_PNG })
  );
}

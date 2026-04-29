import { test } from "@playwright/test";
import { setupArchiveMocks, makeFakeJobs } from "./helpers/mockArchive.js";

const FAKE_USER = {
  id: "user-test-001",
  email: "test@example.com",
  role: "user",
  name: "Test User",
};

test("debug: check page state and console errors", async ({ page }) => {
  const errors = [];
  const consoleLogs = [];
  const networkReqs = [];

  page.on("console", (msg) => consoleLogs.push(`[${msg.type()}] ${msg.text()}`));
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("request", (req) => {
    if (req.url().includes("8000")) networkReqs.push(`${req.method()} ${req.url()}`);
  });

  const user = JSON.stringify(FAKE_USER);
  const jobs = makeFakeJobs(4);
  const jobsJson = JSON.stringify(jobs);
  const storeName = `archive_${FAKE_USER.id}`;

  await page.addInitScript(`
    localStorage.setItem('token', 'fake-token-123');
    localStorage.setItem('user', ${JSON.stringify(user)});

    // Seed IndexedDB
    window.__idbSeedDone = false;
    window.__idbSeedError = null;
    const DB_NAME = "txt2img";
    const STORE = "${storeName}";
    const jobs = ${jobsJson};

    const openReq = indexedDB.open(DB_NAME);
    openReq.onsuccess = function(e) {
      const db = e.target.result;
      const currentVersion = db.version;
      db.close();

      const upgradeReq = indexedDB.open(DB_NAME, currentVersion + 1);
      upgradeReq.onupgradeneeded = function(e2) {
        const db2 = e2.target.result;
        if (!db2.objectStoreNames.contains(STORE)) {
          const store = db2.createObjectStore(STORE, { keyPath: "hash_id" });
          store.createIndex("updated_at", "updated_at", { unique: false });
          store.createIndex("set_id", "set_id", { unique: false });
          store.createIndex("session_id", "session_id", { unique: false });
          store.createIndex("status", "status", { unique: false });
        }
      };
      upgradeReq.onsuccess = function(e2) {
        const db2 = e2.target.result;
        const tx = db2.transaction(STORE, "readwrite");
        const objStore = tx.objectStore(STORE);
        for (const job of jobs) {
          objStore.put({ ...job, _localUpdatedAt: Date.now() });
        }
        tx.oncomplete = function() {
          db2.close();
          window.__idbSeedDone = true;
          console.log('IDB SEED COMPLETE');
        };
        tx.onerror = function(e3) {
          window.__idbSeedError = 'tx error: ' + e3.target.error;
          console.error('IDB SEED TX ERROR:', e3.target.error);
        };
      };
      upgradeReq.onerror = function(e2) {
        window.__idbSeedError = 'upgrade error: ' + e2.target.error;
        console.error('IDB SEED UPGRADE ERROR:', e2.target.error);
      };
      upgradeReq.onblocked = function() {
        window.__idbSeedError = 'blocked';
        console.error('IDB SEED BLOCKED');
      };
    };
    openReq.onerror = function(e) {
      window.__idbSeedError = 'initial open error: ' + e.target.error;
      console.error('IDB SEED INITIAL OPEN ERROR:', e.target.error);
    };
  `);

  // Mock API
  await page.route("http://127.0.0.1:8000/**", (route) => {
    const url = route.request().url();
    if (url.includes("/api/jobs/index")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
    }
    if (url.includes("/events")) return route.abort();
    return route.fulfill({ status: 404, body: "{}" });
  });

  await page.goto("/archive");
  await page.waitForTimeout(5000);

  const idbSeedDone = await page.evaluate(() => window.__idbSeedDone);
  const idbSeedError = await page.evaluate(() => window.__idbSeedError);
  const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
  const allCards = await page.evaluate(() =>
    document.querySelectorAll('[data-testid="archive-card"]').length
  );

  console.log("=== IDB Seed Done:", idbSeedDone);
  console.log("=== IDB Seed Error:", idbSeedError);
  console.log("=== Cards count:", allCards);
  console.log("=== Body text:", bodyText.substring(0, 200));
  console.log("=== Console logs:", consoleLogs.slice(0, 20).join("\n"));
  console.log("=== Errors:", errors.join("\n"));
  console.log("=== Network (to backend):", networkReqs.slice(0, 10).join("\n"));

  await page.screenshot({ path: "e2e/screenshots/debug_state.png" });
});

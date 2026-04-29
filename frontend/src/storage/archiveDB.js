// IndexedDB-backed local cache for the Archive page.
//
// Why IndexedDB and not localStorage:
//   - localStorage is sync + a string-only blob; serialising thousands of
//     archive rows on every write would jank the UI on touch hardware.
//   - IndexedDB has structured-clone storage, indexes for the
//     "newest first" sort the archive needs, and per-store quotas
//     measured in tens of MB out of the box.
//
// Why hand-rolled and not Dexie:
//   - We use ~7 IndexedDB calls. Pulling in Dexie is more bytes than
//     this file. The DOM IDB API is ugly but the surface we need is
//     small and the wrappers below paper over it.
//
// Account isolation:
//   - One object store per user_id (``archive_<userId>``). Switching
//     users does NOT clear the previous user's store — the design doc
//     §9.2 explicitly wants the cache to survive a logout/login cycle.
//   - The store schema bumps the DB version when a brand-new user_id
//     shows up. IDB will run our upgrade callback to ``createObjectStore``
//     for the missing user. We avoid pre-creating stores for users we
//     don't know about because IDB upgrade transactions are blocking.
//
// Schema for each store:
//   - keyPath: "hash_id"
//   - indexes:
//       updated_at  (for "newest first" + delta sync)
//       set_id      (for grouping into Sets)
//       session_id  (for the ``?session_id=`` filter)
//       status      (for showing only QUEUED / RUNNING in some views)
//
// Stored shape (one row):
//   {
//     hash_id, set_id, seq_no, status, model, model_display_name,
//     prompt, params, references[], images[], session, set, timing,
//     error, flags, updated_at,  // ← ISO string, not Date
//     _localUpdatedAt,           // wall-clock when we last wrote it
//   }

const DB_NAME = "txt2img";
const STORE_PREFIX = "archive_";

// ---------------------------------------------------------------------
// Open / upgrade
// ---------------------------------------------------------------------

let cachedDB = null;
let cachedDBVersion = 0;

function storeNameFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("archiveDB: userId required");
  }
  return `${STORE_PREFIX}${userId}`;
}

/**
 * Open the database, upgrading the schema in-place if the requested
 * store is missing. Cached across calls so we don't pay the open cost
 * on every read.
 *
 * Concurrent ``openFor("u1")`` and ``openFor("u2")`` from two unrelated
 * code paths is safe: the second call sees the first's open completed
 * if needed and just adds its own store via a fresh upgrade.
 */
async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("archiveDB: IndexedDB not available");
  }
  const storeName = storeNameFor(userId);

  // Hot path: cached DB already has the store we need.
  if (cachedDB && cachedDB.objectStoreNames.contains(storeName)) {
    return cachedDB;
  }

  // Cold path: open the existing DB to learn its current version, then
  // (if the store we need is missing) close + reopen at version + 1 so
  // the upgrade callback can create it. We can't shortcut to "open at
  // version+1" without knowing the current version, because IDB rejects
  // an open whose version is *lower* than the persisted version with
  // VersionError — and we don't know what other tabs / users have
  // already pushed the schema to.
  let baseDB;
  try {
    baseDB = await openExisting();
  } catch (e) {
    throw e;
  }

  if (baseDB.objectStoreNames.contains(storeName)) {
    cachedDB = baseDB;
    cachedDBVersion = baseDB.version;
    armVersionChangeHandler(cachedDB);
    return cachedDB;
  }

  // Store missing — bump the version and create it in onupgradeneeded.
  const nextVersion = baseDB.version + 1;
  baseDB.close();
  cachedDB = null;

  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, nextVersion);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(storeName)) {
        const store = db.createObjectStore(storeName, { keyPath: "hash_id" });
        store.createIndex("updated_at", "updated_at", { unique: false });
        store.createIndex("set_id", "set_id", { unique: false });
        store.createIndex("session_id", "session_id", { unique: false });
        store.createIndex("status", "status", { unique: false });
      }
    };
    req.onsuccess = () => {
      cachedDB = req.result;
      cachedDBVersion = req.result.version;
      armVersionChangeHandler(cachedDB);
      resolve(cachedDB);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("archiveDB: open blocked by another tab"));
  });
}

/**
 * Open the database without specifying a version. IDB returns the
 * currently-persisted version this way, so we can compute the next
 * upgrade target without guessing. Cleanup of the connection is the
 * caller's responsibility.
 */
function openExisting() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("archiveDB: existing-open blocked by another tab"));
  });
}

function armVersionChangeHandler(db) {
  // If another tab triggers a schema upgrade we must yield our
  // connection so it can proceed; otherwise the user's two tabs both
  // jam waiting on each other.
  db.onversionchange = () => {
    try {
      db.close();
    } catch {
      // already closed
    }
    if (cachedDB === db) cachedDB = null;
  };
}

// ---------------------------------------------------------------------
// Promise wrappers around IDBRequest
// ---------------------------------------------------------------------

function reqToPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
  });
}

// ---------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------

/**
 * Read every row for the user, sorted by updated_at descending.
 * Also returns the latest ``updated_at`` so the caller knows the
 * delta cursor without a second pass.
 */
export async function getAll(userId) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readonly");
  const store = tx.objectStore(storeNameFor(userId));
  const all = await reqToPromise(store.getAll());
  await txDone(tx);
  // Sort newest-first. We don't trust the index ordering across
  // browsers — Safari has historical bugs with descending iteration.
  all.sort((a, b) => {
    const ta = a?.updated_at || "";
    const tb = b?.updated_at || "";
    if (ta === tb) return (a?.hash_id || "").localeCompare(b?.hash_id || "");
    return tb.localeCompare(ta);
  });
  return all;
}

/**
 * Return the largest stored ``updated_at`` ISO string, or null. Used
 * as the ``?since=`` cursor for delta sync.
 */
export async function getMaxUpdatedAt(userId) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readonly");
  const store = tx.objectStore(storeNameFor(userId));
  const idx = store.index("updated_at");
  // `prev` opens the cursor from the highest key downward.
  const cursor = await reqToPromise(idx.openCursor(null, "prev"));
  await txDone(tx);
  if (!cursor) return null;
  return cursor.value?.updated_at || null;
}

/** Look up a single row by its hash_id. */
export async function getByHashId(userId, hashId) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readonly");
  const store = tx.objectStore(storeNameFor(userId));
  const value = await reqToPromise(store.get(hashId));
  await txDone(tx);
  return value || null;
}

/**
 * Insert or replace a single row. Existing rows are merged at the field
 * level so a partial SSE event (e.g. status only) doesn't blow away
 * fields populated by a previous detail fetch.
 */
export async function upsert(userId, row) {
  if (!row || typeof row !== "object" || !row.hash_id) {
    throw new Error("archiveDB.upsert: row.hash_id required");
  }
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readwrite");
  const store = tx.objectStore(storeNameFor(userId));
  const existing = await reqToPromise(store.get(row.hash_id));
  const merged = {
    ...(existing || {}),
    ...row,
    _localUpdatedAt: Date.now(),
  };
  await reqToPromise(store.put(merged));
  await txDone(tx);
  return merged;
}

/** Bulk version of ``upsert``. One transaction. */
export async function upsertMany(userId, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return [];
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readwrite");
  const store = tx.objectStore(storeNameFor(userId));
  const merged = [];
  for (const row of rows) {
    if (!row || !row.hash_id) continue;
    const existing = await reqToPromise(store.get(row.hash_id));
    const next = {
      ...(existing || {}),
      ...row,
      _localUpdatedAt: Date.now(),
    };
    await reqToPromise(store.put(next));
    merged.push(next);
  }
  await txDone(tx);
  return merged;
}

/**
 * Patch a row by id with a shallow merge. Returns the resulting row,
 * or ``null`` if there's nothing to patch (the row was missing).
 */
export async function patch(userId, hashId, patchFields) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readwrite");
  const store = tx.objectStore(storeNameFor(userId));
  const existing = await reqToPromise(store.get(hashId));
  if (!existing) {
    await txDone(tx);
    return null;
  }
  const next = {
    ...existing,
    ...patchFields,
    _localUpdatedAt: Date.now(),
  };
  await reqToPromise(store.put(next));
  await txDone(tx);
  return next;
}

/** Delete one row. */
export async function deleteByHashId(userId, hashId) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readwrite");
  const store = tx.objectStore(storeNameFor(userId));
  await reqToPromise(store.delete(hashId));
  await txDone(tx);
}

/**
 * Wipe every row in the user's store. Used by the "remove from local
 * cache" flow when the server says a row no longer exists, or by
 * tests resetting between cases.
 */
export async function clear(userId) {
  const db = await openFor(userId);
  const tx = db.transaction(storeNameFor(userId), "readwrite");
  const store = tx.objectStore(storeNameFor(userId));
  await reqToPromise(store.clear());
  await txDone(tx);
}

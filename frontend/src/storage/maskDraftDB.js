// IndexedDB-backed local cache for in-progress mask edits.
//
// Why a separate file from draftDB / archiveDB:
//   - draftDB stores at most one CreatePage record per user (single
//     "current" row, holding only reference Files).
//   - archiveDB stores fully-hydrated job rows.
//   - mask drafts are different again: many rows per user (one per
//     image+mode being edited), with a Blob mask payload alongside
//     prompt/refs/options. They need their own store + indexes.
//
// Account isolation: one object store per user, ``mask_draft_<userId>``.
// Same DB version-bump pattern as archiveDB / draftDB so all three
// share a single database connection.
//
// Schema for each store:
//   - keyPath: "draft_id"  ─ format ``${parent_hash_id}:${order}:${mode}``
//   - indexes:
//       saved_at         (TTL scan + LRU eviction)
//       parent_hash_id   (look up "is there a draft for this image?")
//
// Stored shape (one row): see frontend/src/storage/maskDraftDB.js
// docstring on ``putDraft`` for the full field list.

const DB_NAME = "txt2img";
const STORE_PREFIX = "mask_draft_";

const DEFAULT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_MAX_PER_USER = 5;

let cachedDB = null;

function storeNameFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("maskDraftDB: userId required");
  }
  return `${STORE_PREFIX}${userId}`;
}

async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("maskDraftDB: IndexedDB not available");
  }
  const storeName = storeNameFor(userId);

  if (cachedDB && cachedDB.objectStoreNames.contains(storeName)) {
    return cachedDB;
  }

  // Open at no-version to learn current persisted version. The other
  // stores in this database (archive_<u>, create_draft_<u>) may have
  // already pushed the schema beyond what we'd guess.
  const baseDB = await openExisting();

  if (baseDB.objectStoreNames.contains(storeName)) {
    cachedDB = baseDB;
    armVersionChangeHandler(cachedDB);
    return cachedDB;
  }

  const nextVersion = baseDB.version + 1;
  baseDB.close();
  cachedDB = null;

  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, nextVersion);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(storeName)) {
        const store = db.createObjectStore(storeName, { keyPath: "draft_id" });
        store.createIndex("saved_at", "saved_at", { unique: false });
        store.createIndex("parent_hash_id", "parent_hash_id", { unique: false });
      }
    };
    req.onsuccess = () => {
      cachedDB = req.result;
      armVersionChangeHandler(cachedDB);
      resolve(cachedDB);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("maskDraftDB: open blocked by another tab"));
  });
}

function openExisting() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("maskDraftDB: existing-open blocked by another tab"));
  });
}

function armVersionChangeHandler(db) {
  db.onversionchange = () => {
    try {
      db.close();
    } catch {
      // already closed
    }
    if (cachedDB === db) cachedDB = null;
  };
}

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

/** Build the canonical draft id from URL params. */
export function makeDraftId(parentHashId, order, mode) {
  const m = mode === "outpaint" ? "outpaint" : "inpaint";
  return `${parentHashId}:${order}:${m}`;
}

/**
 * Insert or replace one draft. Records are written verbatim — callers
 * are responsible for filling every field they care about.
 */
export async function putDraft(userId, record) {
  if (!record || !record.draft_id) {
    throw new Error("maskDraftDB.putDraft: record.draft_id required");
  }
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(store.put(record));
  await txDone(tx);
}

/**
 * Fetch a single draft by id. Returns ``null`` if not found or expired.
 *
 * TTL is enforced *here* (not just in ``prune``) so a stale record on
 * disk never makes it into the editor — the user wouldn't expect a
 * draft from 8 days ago to come back when they re-open an image.
 */
export async function getDraft(userId, draftId, opts = {}) {
  const maxAgeMs = opts.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  const value = await reqToPromise(store.get(draftId));
  if (!value) {
    await txDone(tx);
    return null;
  }
  const savedAtMs = Date.parse(value.saved_at || "");
  if (Number.isFinite(savedAtMs) && Date.now() - savedAtMs > maxAgeMs) {
    await reqToPromise(store.delete(draftId));
    await txDone(tx);
    return null;
  }
  await txDone(tx);
  return value;
}

/** Remove one draft. Silent on misses. */
export async function deleteDraft(userId, draftId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(store.delete(draftId));
  await txDone(tx);
}

/**
 * List every draft belonging to the user, newest first. Cheap enough
 * (≤ 5 records by quota) that the ArchivePage can read all of them on
 * mount to render the resume banner.
 */
export async function listDrafts(userId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readonly");
  const store = tx.objectStore(storeName);
  const all = await reqToPromise(store.getAll());
  await txDone(tx);
  all.sort((a, b) => {
    const ta = a?.saved_at || "";
    const tb = b?.saved_at || "";
    if (ta === tb) return (a?.draft_id || "").localeCompare(b?.draft_id || "");
    return tb.localeCompare(ta);
  });
  return all;
}

/**
 * One-pass maintenance: drop drafts past TTL, then keep only the N
 * newest. Returns ``{removed: count}`` for diagnostics. Designed to
 * be called once on app startup — it doesn't need to be perfect, just
 * cheap.
 */
export async function prune(userId, opts = {}) {
  const maxAgeMs = opts.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const maxCount = opts.maxCount ?? DEFAULT_MAX_PER_USER;

  let removed = 0;
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  const all = await reqToPromise(store.getAll());
  const now = Date.now();
  // Sort newest first so the "keep top N" trim is a tail slice.
  all.sort((a, b) => (b?.saved_at || "").localeCompare(a?.saved_at || ""));

  const survivors = [];
  for (const rec of all) {
    if (!rec?.draft_id) continue;
    const ts = Date.parse(rec.saved_at || "");
    if (Number.isFinite(ts) && now - ts > maxAgeMs) {
      await reqToPromise(store.delete(rec.draft_id));
      removed += 1;
    } else {
      survivors.push(rec);
    }
  }
  if (survivors.length > maxCount) {
    for (const rec of survivors.slice(maxCount)) {
      await reqToPromise(store.delete(rec.draft_id));
      removed += 1;
    }
  }
  await txDone(tx);
  return { removed };
}

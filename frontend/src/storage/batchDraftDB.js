// IndexedDB-backed local cache for the BatchPage editor draft.
//
// One record per user, stored under ``batch_draft_<userId>``:
//   {
//     id: "current",
//     saved_at: ISO,
//     draft: {
//       title, fixed: { prompt, refs: File[], append_mode },
//       slots: [{ slot_id, stable_idx, rev, title, prompt, refs: File[],
//                  notes, image_count, set_id, overrides, session_strategy,
//                  session_target, model_override }],
//       global_overrides, session_strategy
//     }
//   }
//
// Mirrors ``draftDB.js`` — same DB, separate store; the Files inside
// ``draft.fixed.refs`` and each slot's ``refs`` survive structured-clone
// (the same ``File`` round-trip the Create page draft already relies on).

const DB_NAME = "txt2img";
const STORE_PREFIX = "batch_draft_";
const RECORD_ID = "current";

let cachedDB = null;

function storeNameFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("batchDraftDB: userId required");
  }
  return `${STORE_PREFIX}${userId}`;
}

async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("batchDraftDB: IndexedDB not available");
  }
  const storeName = storeNameFor(userId);

  if (cachedDB && cachedDB.objectStoreNames.contains(storeName)) {
    return cachedDB;
  }

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
        db.createObjectStore(storeName, { keyPath: "id" });
      }
    };
    req.onsuccess = () => {
      cachedDB = req.result;
      armVersionChangeHandler(cachedDB);
      resolve(cachedDB);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("batchDraftDB: open blocked by another tab"));
  });
}

function openExisting() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("batchDraftDB: existing-open blocked by another tab"));
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

/** Persist (overwrite) the user's current batch draft. */
export async function putDraft(userId, draft) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(
    store.put({
      id: RECORD_ID,
      saved_at: new Date().toISOString(),
      draft,
    })
  );
  await txDone(tx);
}

/** Read the current draft, or ``null`` if the user has none. */
export async function getDraft(userId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readonly");
  const store = tx.objectStore(storeName);
  const value = await reqToPromise(store.get(RECORD_ID));
  await txDone(tx);
  if (!value) return null;
  return { draft: value.draft || null, saved_at: value.saved_at || null };
}

/** Wipe the user's current draft. Called on stage ② of submit. */
export async function clearDraft(userId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(store.delete(RECORD_ID));
  await txDone(tx);
}

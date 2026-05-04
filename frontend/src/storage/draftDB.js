// IndexedDB-backed local cache for the CreatePage draft (reference images only).
//
// Why a separate file from archiveDB:
//   - archiveDB uses one store per user for the *archive* — list of jobs.
//     Drafts have a different schema (single record per user with the
//     in-progress reference File[] blob), and we don't want to mix them
//     into the same store.
//   - We share the same DB connection scheme so we don't open two
//     IndexedDB databases for one page.
//
// Schema for each store (one store per user, ``create_draft_<userId>``):
//   - keyPath: "id"  — fixed value "current", overwritten on each save.
//   - record:
//       {
//         id: "current",
//         saved_at: ISO string,
//         files: [File, ...]  // structured-clone ok for File objects
//       }

const DB_NAME = "txt2img";
const STORE_PREFIX = "create_draft_";
const RECORD_ID = "current";

let cachedDB = null;

function storeNameFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("draftDB: userId required");
  }
  return `${STORE_PREFIX}${userId}`;
}

async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("draftDB: IndexedDB not available");
  }
  const storeName = storeNameFor(userId);

  if (cachedDB && cachedDB.objectStoreNames.contains(storeName)) {
    return cachedDB;
  }

  // Open at no-version to learn current persisted version. archiveDB
  // may have already bumped the schema for its own stores.
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
      reject(new Error("draftDB: open blocked by another tab"));
  });
}

function openExisting() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("draftDB: existing-open blocked by another tab"));
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

/** Write the (possibly empty) reference-image set for the user. */
export async function putRefs(userId, files) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(
    store.put({
      id: RECORD_ID,
      saved_at: new Date().toISOString(),
      files: Array.isArray(files) ? files.slice() : [],
    })
  );
  await txDone(tx);
}

/** Read the current draft refs. Returns {files: File[], saved_at} or null. */
export async function getRefs(userId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readonly");
  const store = tx.objectStore(storeName);
  const value = await reqToPromise(store.get(RECORD_ID));
  await txDone(tx);
  if (!value) return null;
  return {
    files: Array.isArray(value.files) ? value.files : [],
    saved_at: value.saved_at || null,
  };
}

/** Wipe the user's draft refs. */
export async function clearRefs(userId) {
  const db = await openFor(userId);
  const storeName = storeNameFor(userId);
  const tx = db.transaction(storeName, "readwrite");
  const store = tx.objectStore(storeName);
  await reqToPromise(store.delete(RECORD_ID));
  await txDone(tx);
}

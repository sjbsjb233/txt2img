// IndexedDB-backed local cache for the Picker page (PRD §11.2).
//
// Reuses the same DB ("txt2img") as archiveDB; the design doc §11
// explicitly says the picker shares the storage layer with the
// archive. We avoid stepping on archive's stores by using distinct
// prefixes (``picker_session_<userId>`` for session metadata,
// ``picker_image_<userId>`` for individual image rows).
//
// Account isolation rules are identical to archiveDB: per-user object
// stores, no cross-user access. We piggyback on archiveDB's open
// helper conceptually but keep an independent module so the schema
// upgrade path is local — touching archiveDB's open code from here
// would couple the two stores' lifecycles.

const DB_NAME = "txt2img";
const STORE_PREFIX_SESSION = "picker_session_";
const STORE_PREFIX_IMAGE = "picker_image_";

let cachedDB = null;

function sessionStoreName(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("pickerDB: userId required");
  }
  return `${STORE_PREFIX_SESSION}${userId}`;
}

function imageStoreName(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("pickerDB: userId required");
  }
  return `${STORE_PREFIX_IMAGE}${userId}`;
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

function openExisting() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("pickerDB: existing-open blocked by another tab"));
  });
}

function armVersionChangeHandler(db) {
  db.onversionchange = () => {
    try {
      db.close();
    } catch {
      /* already closed */
    }
    if (cachedDB === db) cachedDB = null;
  };
}

async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("pickerDB: IndexedDB not available");
  }
  const sName = sessionStoreName(userId);
  const iName = imageStoreName(userId);

  if (
    cachedDB &&
    cachedDB.objectStoreNames.contains(sName) &&
    cachedDB.objectStoreNames.contains(iName)
  ) {
    return cachedDB;
  }

  let baseDB;
  try {
    baseDB = await openExisting();
  } catch (e) {
    throw e;
  }

  const needsSession = !baseDB.objectStoreNames.contains(sName);
  const needsImage = !baseDB.objectStoreNames.contains(iName);
  if (!needsSession && !needsImage) {
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
      if (!db.objectStoreNames.contains(sName)) {
        const store = db.createObjectStore(sName, { keyPath: "id" });
        store.createIndex("picker_state", "picker_state", { unique: false });
        store.createIndex("updated_at", "updated_at", { unique: false });
      }
      if (!db.objectStoreNames.contains(iName)) {
        const store = db.createObjectStore(iName, { keyPath: "image_id" });
        store.createIndex("session_id", "session_id", { unique: false });
        store.createIndex("pick_state", "pick_state", { unique: false });
      }
    };
    req.onsuccess = () => {
      cachedDB = req.result;
      armVersionChangeHandler(cachedDB);
      resolve(cachedDB);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () =>
      reject(new Error("pickerDB: open blocked by another tab"));
  });
}

// ---- Session metadata cache ---------------------------------------------

export async function getAllSessions(userId) {
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreName(userId), "readonly");
  const all = await reqToPromise(tx.objectStore(sessionStoreName(userId)).getAll());
  await txDone(tx);
  all.sort((a, b) => {
    const ta = a?.updated_at || "";
    const tb = b?.updated_at || "";
    return tb.localeCompare(ta);
  });
  return all;
}

export async function getSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreName(userId), "readonly");
  const value = await reqToPromise(
    tx.objectStore(sessionStoreName(userId)).get(sessionId)
  );
  await txDone(tx);
  return value || null;
}

export async function upsertSession(userId, row) {
  if (!row || !row.id) throw new Error("pickerDB.upsertSession: row.id required");
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreName(userId), "readwrite");
  const store = tx.objectStore(sessionStoreName(userId));
  const existing = await reqToPromise(store.get(row.id));
  const merged = {
    ...(existing || {}),
    ...row,
    _localUpdatedAt: Date.now(),
  };
  await reqToPromise(store.put(merged));
  await txDone(tx);
  return merged;
}

export async function upsertManySessions(userId, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return [];
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreName(userId), "readwrite");
  const store = tx.objectStore(sessionStoreName(userId));
  const merged = [];
  for (const row of rows) {
    if (!row || !row.id) continue;
    const existing = await reqToPromise(store.get(row.id));
    const next = { ...(existing || {}), ...row, _localUpdatedAt: Date.now() };
    await reqToPromise(store.put(next));
    merged.push(next);
  }
  await txDone(tx);
  return merged;
}

export async function deleteSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreName(userId), "readwrite");
  await reqToPromise(tx.objectStore(sessionStoreName(userId)).delete(sessionId));
  await txDone(tx);
}

// ---- Image cache --------------------------------------------------------

export async function getImagesForSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreName(userId), "readonly");
  const idx = tx.objectStore(imageStoreName(userId)).index("session_id");
  const all = await reqToPromise(idx.getAll(sessionId));
  await txDone(tx);
  return all || [];
}

export async function upsertManyImages(userId, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return [];
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreName(userId), "readwrite");
  const store = tx.objectStore(imageStoreName(userId));
  for (const row of rows) {
    if (!row || !row.image_id) continue;
    await reqToPromise(store.put({ ...row, _localUpdatedAt: Date.now() }));
  }
  await txDone(tx);
}

export async function patchImage(userId, imageId, patch) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreName(userId), "readwrite");
  const store = tx.objectStore(imageStoreName(userId));
  const existing = await reqToPromise(store.get(imageId));
  if (!existing) {
    await txDone(tx);
    return null;
  }
  const next = { ...existing, ...patch, _localUpdatedAt: Date.now() };
  await reqToPromise(store.put(next));
  await txDone(tx);
  return next;
}

export async function deleteImagesForSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreName(userId), "readwrite");
  const store = tx.objectStore(imageStoreName(userId));
  const idx = store.index("session_id");
  const keys = await reqToPromise(idx.getAllKeys(sessionId));
  for (const k of keys) {
    await reqToPromise(store.delete(k));
  }
  await txDone(tx);
}

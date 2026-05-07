// IndexedDB-backed local cache for the Picker page.
//
// Two object-store families per user_id:
//   - picker_session_<userId> — keyed by session.id
//   - picker_image_<userId>   — keyed by image.image_id, indexed on
//                                session_id and pick_state for fast
//                                per-session lookups.
//
// We share the single ``txt2img`` IndexedDB used by archiveDB.js so the
// browser only has one database to evict under storage pressure. The
// schema-upgrade pattern is the same: open at the persisted version,
// detect missing stores, reopen at version+1 to create them.

const DB_NAME = "txt2img";
const SESSION_PREFIX = "picker_session_";
const IMAGE_PREFIX = "picker_image_";

let cachedDB = null;

function sessionStoreFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("pickerDB: userId required");
  }
  return `${SESSION_PREFIX}${userId}`;
}

function imageStoreFor(userId) {
  if (typeof userId !== "string" || !userId) {
    throw new Error("pickerDB: userId required");
  }
  return `${IMAGE_PREFIX}${userId}`;
}

async function openFor(userId) {
  if (typeof indexedDB === "undefined") {
    throw new Error("pickerDB: IndexedDB not available");
  }
  const wantedStores = [sessionStoreFor(userId), imageStoreFor(userId)];

  if (
    cachedDB &&
    wantedStores.every((s) => cachedDB.objectStoreNames.contains(s))
  ) {
    return cachedDB;
  }

  const baseDB = await openExisting();
  const haveAll = wantedStores.every((s) =>
    baseDB.objectStoreNames.contains(s)
  );
  if (haveAll) {
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
      const sname = sessionStoreFor(userId);
      if (!db.objectStoreNames.contains(sname)) {
        const store = db.createObjectStore(sname, { keyPath: "id" });
        store.createIndex("picker_state", "picker_state", { unique: false });
        store.createIndex("updated_at", "updated_at", { unique: false });
      }
      const iname = imageStoreFor(userId);
      if (!db.objectStoreNames.contains(iname)) {
        const store = db.createObjectStore(iname, { keyPath: "image_id" });
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

// ---------------------------------------------------------------------
// Promise wrappers
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
// Sessions
// ---------------------------------------------------------------------

export async function getAllSessions(userId) {
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreFor(userId), "readonly");
  const store = tx.objectStore(sessionStoreFor(userId));
  const all = await reqToPromise(store.getAll());
  await txDone(tx);
  return all;
}

export async function upsertSession(userId, session) {
  if (!session || !session.id) return null;
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreFor(userId), "readwrite");
  const store = tx.objectStore(sessionStoreFor(userId));
  const existing = await reqToPromise(store.get(session.id));
  const merged = { ...(existing || {}), ...session, _localUpdatedAt: Date.now() };
  await reqToPromise(store.put(merged));
  await txDone(tx);
  return merged;
}

export async function upsertSessionsMany(userId, sessions) {
  if (!Array.isArray(sessions) || sessions.length === 0) return;
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreFor(userId), "readwrite");
  const store = tx.objectStore(sessionStoreFor(userId));
  for (const s of sessions) {
    if (!s || !s.id) continue;
    const existing = await reqToPromise(store.get(s.id));
    await reqToPromise(
      store.put({ ...(existing || {}), ...s, _localUpdatedAt: Date.now() })
    );
  }
  await txDone(tx);
}

export async function deleteSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(sessionStoreFor(userId), "readwrite");
  const store = tx.objectStore(sessionStoreFor(userId));
  await reqToPromise(store.delete(sessionId));
  await txDone(tx);
}

// ---------------------------------------------------------------------
// Images
// ---------------------------------------------------------------------

export async function getImagesForSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreFor(userId), "readonly");
  const store = tx.objectStore(imageStoreFor(userId));
  const idx = store.index("session_id");
  const all = await reqToPromise(idx.getAll(sessionId));
  await txDone(tx);
  return all;
}

export async function upsertImagesMany(userId, images) {
  if (!Array.isArray(images) || images.length === 0) return;
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreFor(userId), "readwrite");
  const store = tx.objectStore(imageStoreFor(userId));
  for (const im of images) {
    if (!im || !im.image_id) continue;
    const existing = await reqToPromise(store.get(im.image_id));
    await reqToPromise(
      store.put({ ...(existing || {}), ...im, _localUpdatedAt: Date.now() })
    );
  }
  await txDone(tx);
}

export async function patchImage(userId, imageId, patchFields) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreFor(userId), "readwrite");
  const store = tx.objectStore(imageStoreFor(userId));
  const existing = await reqToPromise(store.get(imageId));
  if (!existing) {
    await txDone(tx);
    return null;
  }
  const next = { ...existing, ...patchFields, _localUpdatedAt: Date.now() };
  await reqToPromise(store.put(next));
  await txDone(tx);
  return next;
}

export async function clearImagesForSession(userId, sessionId) {
  const db = await openFor(userId);
  const tx = db.transaction(imageStoreFor(userId), "readwrite");
  const store = tx.objectStore(imageStoreFor(userId));
  const idx = store.index("session_id");
  const cursorReq = idx.openCursor(sessionId);
  await new Promise((resolve, reject) => {
    cursorReq.onsuccess = (event) => {
      const cursor = event.target.result;
      if (cursor) {
        store.delete(cursor.primaryKey);
        cursor.continue();
      } else {
        resolve();
      }
    };
    cursorReq.onerror = () => reject(cursorReq.error);
  });
  await txDone(tx);
}

export async function clearAll(userId) {
  const db = await openFor(userId);
  for (const name of [sessionStoreFor(userId), imageStoreFor(userId)]) {
    if (!db.objectStoreNames.contains(name)) continue;
    const tx = db.transaction(name, "readwrite");
    await reqToPromise(tx.objectStore(name).clear());
    await txDone(tx);
  }
}

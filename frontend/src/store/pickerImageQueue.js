// Picker image queue (PRD §7.1.2).
//
// Strict 6-concurrent ceiling on image fetches across all picker
// surfaces (compare panes, contact sheet, filmstrip hover, prefetch).
// Prevents a 50-image session from firing 50 simultaneous image
// requests at the backend on entry.
//
// API shape:
//   - `request(key, path, priority)` — enqueue or return a cached promise
//   - `cancel(key)` — best-effort: drop from waiting queue if not yet
//     dispatched; in-flight requests are *not* aborted
//   - `release(key)` — release a cached blob URL (LRU eviction)
//
// Priority is a small integer where higher = sooner. The PRD priority
// table maps to:
//   100 — current cursor full image
//    90 — current final full image
//    80 — next/prev cursor full
//    70 — contact-sheet thumbs (8)
//    60 — filmstrip hover thumb
//    50 — prefetch ±1 thumb
//    10 — lazy / off-screen
//
// We share blob URLs across consumers via a small LRU keyed by the
// caller-supplied key (typically image_id+":thumb" or image_id+":full").

import { fetchImageBlob } from "../api/picker.js";

const MAX_CONCURRENT = 6;
const LRU_CAPACITY = 50;

let inflightCount = 0;
const cache = new Map(); // key -> { blob, url, refCount, addedAt }
const promises = new Map(); // key -> Promise<string|null>
let waiting = []; // Array<{key, path, priority, resolve, reject}>

function pruneLRU() {
  if (cache.size <= LRU_CAPACITY) return;
  // Evict the oldest unreferenced entries until under cap.
  const ordered = Array.from(cache.entries()).sort(
    (a, b) => a[1].addedAt - b[1].addedAt
  );
  for (const [key, entry] of ordered) {
    if (cache.size <= LRU_CAPACITY) break;
    if (entry.refCount > 0) continue;
    if (entry.url) {
      try {
        URL.revokeObjectURL(entry.url);
      } catch {
        /* ignore */
      }
    }
    cache.delete(key);
  }
}

function pump() {
  if (inflightCount >= MAX_CONCURRENT) return;
  if (waiting.length === 0) return;
  // Highest priority first; FIFO tiebreaker (Array.sort is stable in V8/JSC).
  waiting.sort((a, b) => b.priority - a.priority);
  const job = waiting.shift();
  if (!job) return;
  inflightCount += 1;
  fetchImageBlob(job.path)
    .then((blob) => {
      if (blob === null) {
        job.resolve(null);
        return;
      }
      const url = URL.createObjectURL(blob);
      cache.set(job.key, {
        blob,
        url,
        refCount: 1,
        addedAt: Date.now(),
      });
      pruneLRU();
      job.resolve(url);
    })
    .catch((err) => {
      job.reject(err);
    })
    .finally(() => {
      inflightCount = Math.max(0, inflightCount - 1);
      promises.delete(job.key);
      pump();
    });
}

/**
 * Request a blob URL for the given image. Returns a Promise that resolves
 * to the cached object URL (string) or null when the file is missing
 * (404).
 *
 * Multiple concurrent calls for the same key share a single in-flight
 * fetch — the cache is reference-counted so callers must invoke
 * `release(key)` when they're done with it.
 */
export function request(key, path, priority = 50) {
  if (cache.has(key)) {
    const entry = cache.get(key);
    entry.refCount += 1;
    return Promise.resolve(entry.url);
  }
  const existing = promises.get(key);
  if (existing) {
    existing.then((url) => {
      // Race: cache might have just been populated; bump refcount.
      const entry = cache.get(key);
      if (entry) entry.refCount += 1;
      return url;
    });
    return existing;
  }
  const p = new Promise((resolve, reject) => {
    waiting.push({ key, path, priority, resolve, reject });
    // Schedule a microtask flush so multiple synchronous request()
    // calls coalesce before we sort/dispatch.
    queueMicrotask(pump);
  });
  promises.set(key, p);
  return p;
}

/**
 * Drop a key from the waiting queue if it hasn't been dispatched yet.
 * In-flight requests are not aborted (HTTP/2 abort is a noop on the
 * server side and just wastes the bytes already inflight).
 */
export function cancel(key) {
  waiting = waiting.filter((j) => j.key !== key);
  promises.delete(key);
}

/**
 * Drop one reference. When refCount hits zero the entry becomes a
 * candidate for LRU eviction; we don't revoke immediately because the
 * caller might re-mount and re-request the same key (e.g., React
 * StrictMode double-mount, or moving cursor back).
 */
export function release(key) {
  const entry = cache.get(key);
  if (!entry) return;
  entry.refCount = Math.max(0, entry.refCount - 1);
  pruneLRU();
}

/** Drop everything. Used on session switch / logout. */
export function clearAll() {
  for (const [, entry] of cache) {
    if (entry.url) {
      try {
        URL.revokeObjectURL(entry.url);
      } catch {
        /* ignore */
      }
    }
  }
  cache.clear();
  promises.clear();
  waiting = [];
}

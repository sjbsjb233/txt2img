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
// In-flight tracker — refCount accumulates `request` calls that arrive
// before the fetch has resolved. `release` decrements this counter
// while the promise is pending; on resolve we use it as the seed for
// the cache entry's refCount so unmounts during fetch don't strand
// permanent references.
const inflight = new Map(); // key -> { promise, refCount, epoch }
let waiting = []; // Array<{key, path, priority, resolve, epoch}>
// Bumped by clearAll() — anything that started under a prior epoch
// silently discards its result instead of repopulating the cache.
let currentEpoch = 0;

class CancelledError extends Error {
  constructor() {
    super("picker image request cancelled");
    this.name = "CancelledError";
    this.cancelled = true;
  }
}

function pruneLRU() {
  if (cache.size <= LRU_CAPACITY) return;
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
  waiting.sort((a, b) => b.priority - a.priority);
  const job = waiting.shift();
  if (!job) return;
  // Drop jobs from a previous epoch — clearAll() invalidated them.
  if (job.epoch !== currentEpoch) {
    job.resolve(null);
    inflight.delete(job.key);
    pump();
    return;
  }
  inflightCount += 1;
  fetchImageBlob(job.path)
    .then((blob) => {
      const tracker = inflight.get(job.key);
      // Discard if epoch changed mid-flight: clearAll() ran while we
      // were waiting on bytes. Don't repopulate the cache.
      if (job.epoch !== currentEpoch) {
        job.resolve(null);
        return;
      }
      if (blob === null) {
        job.resolve(null);
        return;
      }
      const url = URL.createObjectURL(blob);
      // Seed refCount from in-flight tracker so callers that requested
      // *and* released while the fetch was pending still net out
      // correctly.
      const seedRefs = tracker ? Math.max(0, tracker.refCount) : 1;
      cache.set(job.key, {
        blob,
        url,
        refCount: seedRefs,
        addedAt: Date.now(),
      });
      pruneLRU();
      job.resolve(url);
    })
    .catch((err) => {
      // Resolve to null so consumers downgrade to placeholder rather
      // than throw an unhandled rejection. fetchImageBlob already
      // distinguishes 404 from transient errors; both surface as a
      // missing-image fallback in the UI.
      job.resolve(null);
      // eslint-disable-next-line no-console
      if (typeof console !== "undefined") {
        console.warn("pickerImageQueue: fetch failed", job.path, err);
      }
    })
    .finally(() => {
      inflightCount = Math.max(0, inflightCount - 1);
      inflight.delete(job.key);
      pump();
    });
}

/**
 * Request a blob URL for the given image. Returns a Promise that
 * resolves to the cached object URL (string) or null when the file is
 * missing or cancelled.
 */
export function request(key, path, priority = 50) {
  // Cache hit — bump refcount and resolve.
  if (cache.has(key)) {
    const entry = cache.get(key);
    entry.refCount += 1;
    return Promise.resolve(entry.url);
  }
  // In-flight hit — share the promise and bump the in-flight refcount
  // so callers that release before the fetch resolves are accounted
  // for.
  const existing = inflight.get(key);
  if (existing) {
    existing.refCount += 1;
    return existing.promise;
  }
  // Fresh request.
  const epoch = currentEpoch;
  const promise = new Promise((resolve) => {
    waiting.push({ key, path, priority, resolve, epoch });
    queueMicrotask(pump);
  });
  inflight.set(key, { promise, refCount: 1, epoch });
  return promise;
}

/**
 * Drop a key from the waiting queue if it hasn't been dispatched yet.
 * Settles the pending promise with `null` so awaiters don't hang
 * forever. In-flight requests are not aborted (HTTP/2 abort is a
 * noop on the server side).
 */
export function cancel(key) {
  const idx = waiting.findIndex((j) => j.key === key);
  if (idx >= 0) {
    const [job] = waiting.splice(idx, 1);
    try {
      job.resolve(null);
    } catch {
      /* ignore */
    }
    inflight.delete(key);
  }
}

/**
 * Drop one reference. While the entry is in-flight we decrement the
 * pending refcount; once cached, we decrement the cache entry and let
 * LRU sweep it.
 */
export function release(key) {
  const entry = cache.get(key);
  if (entry) {
    entry.refCount = Math.max(0, entry.refCount - 1);
    pruneLRU();
    return;
  }
  const tracker = inflight.get(key);
  if (tracker) {
    tracker.refCount = Math.max(0, tracker.refCount - 1);
  }
}

/**
 * Drop every cached entry and invalidate every in-flight request.
 * Used on session switch / logout. In-flight fetches still complete
 * but discard their result (epoch check) so we don't leak blob URLs
 * after the user navigated away.
 */
export function clearAll() {
  currentEpoch += 1;
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
  // Settle any pending promises that haven't been dispatched yet so
  // their consumers unblock immediately.
  for (const job of waiting) {
    try {
      job.resolve(null);
    } catch {
      /* ignore */
    }
  }
  waiting = [];
  inflight.clear();
}

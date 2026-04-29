// Tiny optimistic-insert buffer between Create and Archive.
//
// FE-03 (this PR) lands the Create→Archive handoff: when a job is
// successfully POSTed we want Archive to render the QUEUED card
// immediately, without waiting for the SSE round-trip. FE-04 will
// replace this with the IndexedDB-backed archive store; until then we
// stash the latest few jobs in `sessionStorage` keyed by user.
//
// Why sessionStorage (not localStorage):
//   - Scoped to the tab session — exactly the lifetime over which the
//     handoff matters.
//   - Survives the in-app `router.push('/archive')` navigation.
//   - Cleared on tab close so a stale optimistic entry from a previous
//     session can't corrupt a future archive load.
//
// Shape: `{ entries: [JobCreateResponse, ...], capacity: number }`.

import { getCurrentUser } from "../api/client.js";

const STORAGE_PREFIX = "archiveOptimistic.v1.";
const DEFAULT_CAPACITY = 8;

function storageKey() {
  const user = getCurrentUser();
  if (!user) return null;
  return `${STORAGE_PREFIX}${user.id}`;
}

function safeRead() {
  const key = storageKey();
  if (!key) return [];
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter(Boolean);
    return [];
  } catch {
    return [];
  }
}

function safeWrite(entries) {
  const key = storageKey();
  if (!key) return;
  try {
    sessionStorage.setItem(
      key,
      JSON.stringify(entries.slice(-DEFAULT_CAPACITY))
    );
  } catch {
    // Quota exceeded or storage disabled — silently fall back.
  }
}

/**
 * Push one fresh job-create response to the front of the buffer so the
 * Archive page can prepend it before the SSE event arrives. Returns
 * the truncated buffer for convenience.
 */
export function pushOptimisticJob(jobResponse) {
  if (!jobResponse || typeof jobResponse !== "object") return;
  const entries = safeRead();
  // Dedup by hash_id — re-submitting the same client_request_id
  // returns the same hash, no need to insert twice.
  const filtered = entries.filter(
    (e) => e && e.hash_id !== jobResponse.hash_id
  );
  filtered.push({ ...jobResponse, _capturedAt: Date.now() });
  safeWrite(filtered);
}

/**
 * Read the buffer non-destructively. The Archive page consumes this
 * and merges with any existing local cache entries.
 */
export function readOptimisticJobs() {
  return safeRead();
}

/**
 * Drop one or all entries — Archive calls this once it has confirmed
 * the entry exists in its own (IndexedDB-backed, in FE-04) cache.
 */
export function clearOptimisticJob(hashId) {
  if (hashId === undefined) {
    safeWrite([]);
    return;
  }
  const remaining = safeRead().filter((e) => e && e.hash_id !== hashId);
  safeWrite(remaining);
}

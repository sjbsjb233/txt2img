// Archive store — bridges IndexedDB + REST sync + SSE updates.
//
// Lifecycle:
//   - ``mount(userId)`` is called by ArchivePage.jsx on mount. It
//     reads everything from IndexedDB into memory, kicks off a
//     background ``sync()``, and subscribes to the SSE channel.
//   - ``unmount()`` tears down the SSE subscriptions but keeps the
//     in-memory cache around (the user might come right back).
//
// Synchronisation strategy:
//   - On mount, ``getJobsIndex({since: maxLocalUpdatedAt})`` returns
//     only rows that changed on the server since the local cache last
//     saw them. We diff status/updated_at against the local copy and
//     pull full ``getJobsDetails`` for any row whose status differs
//     or whose local cache is missing the detail payload (e.g. an
//     SSE-created stub).
//   - Live updates ride on the SSE channel:
//       - ``task_created`` → insert / merge a stub (status QUEUED).
//       - ``job_state``    → patch status, finished_at, images.
//       - ``job_progress`` → patch position / ETA on QUEUED rows.
//       - ``task_deleted`` → drop from the local cache.
//
// State shape kept in the in-memory ``rows`` map keyed by hash_id:
//   {
//     hash_id, set_id, seq_no, status, model, model_display_name?,
//     prompt?, params?, references?, images?, session?, set?, timing?,
//     error?, flags?, updated_at, _localUpdatedAt,
//     _hydrated: boolean,    // true once full detail has been pulled
//     _position?: number,    // SSE-driven for QUEUED
//     _eta?: number,
//     _runStartedAt?: number // wall-clock ms; running cards use it for the timer
//   }

import { useEffect, useState } from "react";

import {
  getJobsDetails,
  getJobsIndex,
  starImage as apiStarImage,
} from "../api/archive.js";
import * as archiveDB from "../storage/archiveDB.js";
import * as sseStore from "./sse.js";

// ---------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------

let activeUserId = null;
const rows = new Map(); // hash_id -> row
const sessions = new Map(); // session_id -> session summary (cached)

const subscribers = new Set();
const sseUnsubs = new Set();
let syncInFlight = false;
let syncFailedAt = 0;

// ---------------------------------------------------------------------
// Subscribe API
// ---------------------------------------------------------------------

function notify() {
  for (const fn of subscribers) {
    try {
      fn();
    } catch {
      // Bad listener should not break the rest.
    }
  }
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

/**
 * Hook: re-renders on every store change. Returns a snapshot of the
 * full row list, sorted newest-first.
 */
export function useArchive() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return getRows();
}

/** Read every row, sorted newest-first. Useful for one-off reads. */
export function getRows() {
  const list = Array.from(rows.values());
  list.sort((a, b) => {
    const ta = a?.updated_at || "";
    const tb = b?.updated_at || "";
    if (ta === tb) return (a?.hash_id || "").localeCompare(b?.hash_id || "");
    return tb.localeCompare(ta);
  });
  return list;
}

export function getRow(hashId) {
  return rows.get(hashId) || null;
}

// ---------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------

/**
 * Bind the store to a user. Reads the IndexedDB cache, kicks off a
 * delta sync, and starts listening to the SSE event stream. Safe to
 * call when already mounted for a different user — that swaps the
 * active user and reloads.
 */
export async function mount(userId) {
  if (!userId) {
    unmount();
    return;
  }
  if (activeUserId !== userId) {
    rows.clear();
    sessions.clear();
    activeUserId = userId;
    notify();
  }

  // 1. Hydrate from IndexedDB.
  try {
    const stored = await archiveDB.getAll(userId);
    for (const row of stored) {
      rows.set(row.hash_id, row);
    }
    notify();
  } catch (e) {
    // IndexedDB may be unavailable (private mode in some browsers).
    // Fall back to memory-only — sync() will still populate from REST.
    console.warn("archive: IndexedDB hydrate failed", e);
  }

  // 2. Wire SSE subscriptions before we kick off the REST sync — that
  //    way any event landing while the sync is in flight is captured
  //    in our in-memory state and reconciled with the REST result on
  //    arrival (the merge path in upsert is shallow + last-write-wins
  //    on per-field basis).
  attachSse();

  // 3. Background delta sync.
  void sync();
}

/** Tear down SSE subscriptions; keep the in-memory cache. */
export function unmount() {
  detachSse();
  // Deliberately don't clear ``rows`` — the user may come back to the
  // page and we want the previous render to survive a route change.
}

/**
 * Hard reset — clears the in-memory cache and the IndexedDB store for
 * the current user. Wired for diagnostic / emergency use; the normal
 * ``unmount`` path keeps state.
 */
export async function reset() {
  detachSse();
  if (activeUserId) {
    try {
      await archiveDB.clear(activeUserId);
    } catch {
      // ignore
    }
  }
  rows.clear();
  sessions.clear();
  activeUserId = null;
  notify();
}

// ---------------------------------------------------------------------
// Delta sync
// ---------------------------------------------------------------------

/**
 * Pull the index since the latest cached ``updated_at``, then fetch
 * details for any row whose status/updated_at changed or that we don't
 * have details for yet.
 *
 * Idempotent and re-entrant: a second call while a sync is already in
 * flight is a no-op (the in-flight call will pick up SSE events that
 * landed during it). Safe to call from anywhere — the page calls it
 * on mount and after a long ``connection_lost``.
 */
export async function sync() {
  if (!activeUserId) return;
  if (syncInFlight) return;
  syncInFlight = true;
  try {
    const since = await archiveDB.getMaxUpdatedAt(activeUserId);
    let cursor = null;
    const allEntries = [];
    // Walk pages until next_cursor is null. Using the smaller default
    // limit (1000) is plenty for typical archives; the loop covers the
    // ridiculous case of a freshly-restored backup.
    do {
      const page = await getJobsIndex({
        since: since || undefined,
        cursor: cursor || undefined,
      });
      for (const entry of page.items || []) {
        allEntries.push(entry);
      }
      cursor = page.next_cursor || null;
    } while (cursor);

    if (allEntries.length === 0) {
      return;
    }

    // Decide which rows to refetch in detail. A row needs a full pull
    // when its status changed (or it's new) or when we're missing the
    // detail payload (``_hydrated === false``).
    const toFetch = [];
    for (const entry of allEntries) {
      const local = rows.get(entry.hash_id);
      if (
        !local ||
        !local._hydrated ||
        local.status !== entry.status ||
        (local.updated_at || "") !== (entry.updated_at || "")
      ) {
        toFetch.push(entry.hash_id);
      } else {
        // Even when nothing changed, refresh the index-level fields
        // so the cache reflects server truth.
        await applyRow({
          ...local,
          ...entry,
        });
      }
    }

    if (toFetch.length > 0) {
      const detailResp = await getJobsDetails(toFetch);
      for (const item of detailResp.items || []) {
        if (item?.not_found) {
          // Server reaped this id (cleanup task). Remove locally.
          await dropLocal(item.hash_id);
          continue;
        }
        await applyDetail(item);
      }
    }
    syncFailedAt = 0;
  } catch (e) {
    syncFailedAt = Date.now();
    console.warn("archive: sync failed", e);
  } finally {
    syncInFlight = false;
  }
}

/** Wall-clock timestamp of the last sync failure (or 0). */
export function lastSyncFailedAt() {
  return syncFailedAt;
}

// ---------------------------------------------------------------------
// SSE wiring
// ---------------------------------------------------------------------

function attachSse() {
  if (sseUnsubs.size > 0) return;
  sseUnsubs.add(sseStore.subscribe("task_created", onTaskCreated));
  sseUnsubs.add(sseStore.subscribe("job_state", onJobState));
  sseUnsubs.add(sseStore.subscribe("job_progress", onJobProgress));
  sseUnsubs.add(sseStore.subscribe("task_deleted", onTaskDeleted));
  // When the connection comes back from ``lost``, run a fresh sync to
  // catch up on anything we might have missed beyond the SSE replay
  // window. ``subscribeConnectionState`` calls the handler immediately
  // with the current state, so we filter to only act on the
  // open-after-disruption transition.
  let prevStatus = null;
  sseUnsubs.add(
    sseStore.subscribeConnectionState(({ status }) => {
      const wasInterrupted =
        prevStatus === "lost" || prevStatus === "reconnecting";
      if (status === "open" && wasInterrupted) {
        void sync();
      }
      prevStatus = status;
    })
  );
}

function detachSse() {
  for (const off of sseUnsubs) {
    try {
      off();
    } catch {
      // ignore
    }
  }
  sseUnsubs.clear();
}

async function onTaskCreated(payload) {
  if (!payload?.hash_id) return;
  // Optimistic-create from the same tab? The Create page already
  // pushed the response into the store via ``insertOptimistic``; the
  // SSE roundtrip arrives later with the same client_request_id and
  // we reconcile by hash_id (same key → merged in place).
  await applyRow({
    hash_id: payload.hash_id,
    set_id: payload.set_id ?? null,
    seq_no: payload.seq_no,
    status: payload.status || "QUEUED",
    model: payload.model,
    updated_at: payload.created_at || new Date().toISOString(),
    _hydrated: false,
    _position: payload.position ?? null,
    _eta: payload.estimated_wait_seconds ?? null,
  });
}

async function onJobState(payload) {
  if (!payload?.hash_id) return;
  const local = rows.get(payload.hash_id) || {
    hash_id: payload.hash_id,
    set_id: payload.set_id ?? null,
    seq_no: payload.seq_no,
    model: payload.model,
    _hydrated: false,
  };
  const next = {
    ...local,
    status: payload.to || local.status,
    updated_at: payload.ts || local.updated_at,
  };
  if (payload.set_id) next.set_id = payload.set_id;
  if (payload.seq_no) next.seq_no = payload.seq_no;
  if (payload.model) next.model = payload.model;
  if (payload.reason) next.error = payload.reason;
  if (payload.to === "RUNNING" && !next._runStartedAt) {
    next._runStartedAt = Date.now();
  }
  if (payload.to === "SUCCEEDED" && payload.result?.images) {
    // Stamp the images we received but keep the row marked unhydrated
    // so the next sync pulls full timing/params/refs. Frontend can
    // already render thumbs from the SSE payload.
    next.images = payload.result.images;
  }
  await applyRow(next);

  // Some terminal transitions deserve a fresh detail pull right away
  // so the right-side drawer has full timing / params on first open.
  if (
    payload.to === "SUCCEEDED" ||
    payload.to === "FAILED" ||
    payload.to === "CANCELLED"
  ) {
    void refreshDetail(payload.hash_id);
  }
}

async function onJobProgress(payload) {
  if (!payload?.hash_id) return;
  const local = rows.get(payload.hash_id);
  if (!local) return;
  const patch = {};
  if (payload.position != null) patch._position = payload.position;
  if (payload.estimated_wait_seconds != null)
    patch._eta = payload.estimated_wait_seconds;
  if (payload.elapsed_ms != null && local.status === "RUNNING") {
    // Anchor the running timer against the message's timestamp so
    // the card's seconds counter stays accurate even when the server
    // clock and ours disagree.
    patch._runStartedAt = Date.now() - payload.elapsed_ms;
  }
  if (Object.keys(patch).length === 0) return;
  await applyRow({ ...local, ...patch });
}

async function onTaskDeleted(payload) {
  if (!payload?.hash_id) return;
  await dropLocal(payload.hash_id);
}

// ---------------------------------------------------------------------
// Optimistic insert from CreatePage
// ---------------------------------------------------------------------

/**
 * Called by CreatePage when ``POST /api/jobs`` returns. Inserts a
 * QUEUED row immediately so the user sees their submission in the
 * archive list before any SSE round-trip.
 *
 * Idempotent — if a row with the same hash_id already exists (the
 * SSE event raced us), the merge keeps the more-detailed row.
 */
export async function insertOptimistic(jobResponse) {
  if (!jobResponse?.hash_id || !activeUserId) return;
  await applyRow({
    hash_id: jobResponse.hash_id,
    set_id: jobResponse.set_id ?? null,
    seq_no: jobResponse.seq_no,
    status: jobResponse.status || "QUEUED",
    model: jobResponse.model,
    updated_at:
      jobResponse.queued_at ||
      jobResponse.created_at ||
      new Date().toISOString(),
    _hydrated: false,
    _position: jobResponse.position ?? null,
    _eta: jobResponse.estimated_wait_seconds ?? null,
  });
}

// ---------------------------------------------------------------------
// Manual operations
// ---------------------------------------------------------------------

/**
 * Pull a single row's full details. Use after a star toggle or when
 * the user opens the right-side drawer for a row that was created via
 * SSE (no detail payload yet).
 */
export async function refreshDetail(hashId) {
  if (!activeUserId) return null;
  try {
    const resp = await getJobsDetails([hashId]);
    const item = resp.items?.[0];
    if (!item) return null;
    if (item.not_found) {
      await dropLocal(hashId);
      return null;
    }
    return await applyDetail(item);
  } catch (e) {
    console.warn("archive: refreshDetail failed", e);
    return null;
  }
}

/**
 * Toggle (or set) the starred bit on one image and patch the local
 * row in place. Optimistic — we update the cache before the round
 * trip; on failure we re-pull details to sync back to server truth.
 */
export async function toggleStar(hashId, order, starred) {
  const local = rows.get(hashId);
  if (!local) return null;
  const images = (local.images || []).map((img) =>
    img.order === order
      ? { ...img, starred: starred === undefined ? !img.starred : !!starred }
      : img
  );
  await applyRow({ ...local, images });
  try {
    const resp = await apiStarImage(hashId, order, starred);
    return resp;
  } catch (e) {
    console.warn("archive: star toggle failed; refreshing", e);
    await refreshDetail(hashId);
    throw e;
  }
}

/**
 * Drop a row from local storage — used when the server says it no
 * longer exists (a thumb returned 404, or a ``task_deleted`` event
 * arrived for an id we don't own anymore).
 */
export async function dropLocal(hashId) {
  if (!activeUserId) return;
  rows.delete(hashId);
  try {
    await archiveDB.deleteByHashId(activeUserId, hashId);
  } catch {
    // ignore — best effort
  }
  notify();
}

// ---------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------

async function applyRow(row) {
  if (!activeUserId || !row?.hash_id) return null;
  const merged = mergeRow(row);
  rows.set(merged.hash_id, merged);
  notify();
  try {
    await archiveDB.upsert(activeUserId, _stripVolatile(merged));
  } catch (e) {
    console.warn("archive: persist failed", e);
  }
  return merged;
}

async function applyDetail(detail) {
  if (!detail?.hash_id) return null;
  // Note: the volatile fields (_position, _eta, _runStartedAt) are
  // owned by SSE and shouldn't be clobbered by the REST detail; the
  // merge below preserves them by taking the local copy first.
  const local = rows.get(detail.hash_id) || {};
  const merged = mergeRow({
    ...local,
    ...detail,
    _hydrated: true,
  });
  rows.set(merged.hash_id, merged);
  notify();
  try {
    await archiveDB.upsert(activeUserId, _stripVolatile(merged));
  } catch (e) {
    console.warn("archive: persist failed", e);
  }
  if (detail.session?.id) {
    sessions.set(detail.session.id, detail.session);
  }
  return merged;
}

function mergeRow(row) {
  // Coerce updated_at to a stable ISO string. The IndexedDB index
  // sorts as strings, so a Date here would silently break ordering.
  let updatedAt = row.updated_at;
  if (updatedAt instanceof Date) updatedAt = updatedAt.toISOString();
  return {
    ...row,
    updated_at: updatedAt || new Date(0).toISOString(),
  };
}

function _stripVolatile(row) {
  // Don't persist the SSE-derived runtime fields — they're meaningful
  // only while the page is alive. Persisting them would leave a stale
  // ``_runStartedAt`` sitting in IndexedDB that survives a tab close
  // and would lead to "running for 5 hours" timers on the next open.
  // eslint-disable-next-line no-unused-vars
  const { _position, _eta, _runStartedAt, ...persistable } = row;
  return persistable;
}

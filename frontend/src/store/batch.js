// Batch store: server-truth running batches + local-only editor draft.
//
// Mounted at the App level so the running list is warm regardless of
// which page the user is on. Two pieces of state live here:
//
//   1. ``runningBatches`` — Map<batch_id, BatchSummary> hydrated from
//      ``GET /api/batches`` and updated incrementally by SSE
//      ``batch_progress`` events. View-only — the editor never reads
//      from this.
//   2. ``dismissedIds`` — set of batch ids the user clicked Dismiss
//      on. Sticky in ``localStorage`` so refresh keeps the chosen
//      cards hidden; explicitly NOT synced across devices (per
//      frontend doc §8.2 — Dismiss is "I've seen this card", not a
//      business state).
//
// The store is plain pub/sub — no React context — to keep parity with
// ``store/sse.js`` and ``store/archive.js``. Components subscribe via
// ``useRunningBatches()``.

import { useEffect, useState } from "react";

import { listBatches } from "../api/batches.js";
import * as sseStore from "./sse.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  runningBatches: new Map(), // batch_id -> entry
  dismissedIds: loadDismissed(),
  meta: null, // last seen ModelsResponseMeta cache for batch limits
};

const listeners = new Set();
let unsubSSE = null;

const DISMISSED_KEY = "txt2img_batch_dismissed_ids";

function loadDismissed() {
  if (typeof localStorage === "undefined") return new Set();
  try {
    const raw = localStorage.getItem(DISMISSED_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return new Set(arr.filter((s) => typeof s === "string"));
  } catch {
    /* fallthrough */
  }
  return new Set();
}

function persistDismissed() {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(
      DISMISSED_KEY,
      JSON.stringify(Array.from(state.dismissedIds))
    );
  } catch {
    /* ignore */
  }
}

function emit() {
  for (const fn of listeners) {
    try {
      fn();
    } catch {
      /* listener errors must not break the bus */
    }
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function mount(_userId) {
  // Hydrate the list from the server. Best-effort; if the request
  // fails (logged-out, network) we just stay empty until the next
  // explicit refresh.
  try {
    const data = await listBatches({
      status: "non_terminal,recent",
      limit: 50,
    });
    state.runningBatches = new Map(
      (data.items || []).map((b) => [b.batch_id, b])
    );
  } catch {
    /* ignore */
  }
  if (unsubSSE) {
    unsubSSE();
    unsubSSE = null;
  }
  unsubSSE = sseStore.subscribe("batch_progress", (payload) => {
    if (!payload || typeof payload !== "object" || !payload.batch_id) return;
    const prev = state.runningBatches.get(payload.batch_id) || {};
    state.runningBatches.set(payload.batch_id, { ...prev, ...payload });
    emit();
  });
  emit();
}

export function unmount() {
  if (unsubSSE) {
    unsubSSE();
    unsubSSE = null;
  }
  state.runningBatches = new Map();
  emit();
}

/**
 * Force a refresh from the server. Used after a user action that
 * altered the list shape (cancel, delete, finalize) so the user sees
 * the change immediately even if the SSE event is in flight.
 */
export async function refresh() {
  try {
    const data = await listBatches({
      status: "non_terminal,recent",
      limit: 50,
    });
    state.runningBatches = new Map(
      (data.items || []).map((b) => [b.batch_id, b])
    );
    emit();
  } catch {
    /* ignore */
  }
}

/** Insert (or update) a batch record locally — used for optimistic updates. */
export function upsert(batch) {
  if (!batch || !batch.batch_id) return;
  const prev = state.runningBatches.get(batch.batch_id) || {};
  state.runningBatches.set(batch.batch_id, { ...prev, ...batch });
  emit();
}

export function dismiss(batchId) {
  state.dismissedIds.add(batchId);
  persistDismissed();
  emit();
}

export function isDismissed(batchId) {
  return state.dismissedIds.has(batchId);
}

export function setMeta(meta) {
  state.meta = meta || null;
  emit();
}

export function getMeta() {
  return state.meta;
}

/** Synchronous read for non-React callers. */
export function getRunningBatches() {
  return Array.from(state.runningBatches.values()).filter(
    (b) => !state.dismissedIds.has(b.batch_id)
  );
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

export function useRunningBatches() {
  const [, force] = useState(0);
  useEffect(() => {
    const off = subscribe(() => force((n) => n + 1));
    return off;
  }, []);
  return getRunningBatches();
}

export function useBatchMeta() {
  const [, force] = useState(0);
  useEffect(() => {
    const off = subscribe(() => force((n) => n + 1));
    return off;
  }, []);
  return getMeta();
}

/** Sorted view: non-terminal first, then most-recent terminal. */
export function sortBatches(items) {
  return items.slice().sort((a, b) => {
    const aTerm = isTerminal(a.status);
    const bTerm = isTerminal(b.status);
    if (aTerm !== bTerm) return aTerm ? 1 : -1;
    const aTs = Date.parse(a.updated_at || a.created_at || 0) || 0;
    const bTs = Date.parse(b.updated_at || b.created_at || 0) || 0;
    return bTs - aTs;
  });
}

const TERMINAL_STATUSES = new Set([
  "completed",
  "partial",
  "cancelled",
  "abandoned",
]);

export function isTerminal(status) {
  return TERMINAL_STATUSES.has(status);
}

/** Count active (non-terminal) batches the user currently has. */
export function inFlightCount() {
  let n = 0;
  for (const b of state.runningBatches.values()) {
    if (!isTerminal(b.status)) n += 1;
  }
  return n;
}

// Picker store — three-layer sync (IndexedDB + REST + SSE) per PRD §11.
//
// State shape:
//   - overview: { deck_title, last_edited, totals, summary, sessions[] }
//   - currentSession: { session, jobs, images } (active session view)
//   - undoStack: per-session-only undo entries
//
// Lifecycle:
//   - mountOverview(userId) — call when /picker is rendered (deck overview)
//   - openSession(sessionId) — call when /picker?session_id=X is rendered
//   - closeSession() — call on route leave; flushes cursor PATCH
//   - unmount() — full teardown, called on logout
//
// Optimistic updates:
//   - judging writes update memory + IndexedDB immediately, then
//     fire a debounced REST POST. On failure we roll back from the
//     undo entry.
//   - SSE `image_pick_state` events overwrite memory unconditionally
//     (server is source of truth) — but we tag own-events via a recent
//     write set so we don't double-apply.

import { useEffect, useState } from "react";

import {
  finalizeSession as apiFinalize,
  getPickerOverview,
  getSessionPicker,
  patchCursor,
  unfinalizeSession as apiUnfinalize,
  varySeed as apiVarySeed,
  writePickState as apiWriteState,
} from "../api/picker.js";
import * as pickerDB from "../storage/pickerDB.js";
import * as sseStore from "./sse.js";

// ---------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------

let activeUserId = null;
let overviewState = {
  loading: true,
  data: null,
};
let sessionState = {
  sessionId: null,
  loading: false,
  data: null, // { session, jobs[], images[] }
  error: null,
};
let undoStack = []; // per-session
let recentLocalWrites = new Map(); // image_id -> ts; suppress own SSE

const subscribers = new Set();
const sseUnsubs = new Set();
let syncOverviewInFlight = false;

// Cursor patch debounce/throttle
let cursorTimer = null;
const CURSOR_THROTTLE_MS = 1000;

// Debounced write queue (coalesces rapid same-image writes)
const pendingWrites = new Map(); // image_id -> {hashId, order, state, timer}
const WRITE_DEBOUNCE_MS = 100;

function notify() {
  for (const fn of subscribers) {
    try {
      fn();
    } catch {
      /* ignore */
    }
  }
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

// ---------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------

export function usePickerOverview() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return overviewState;
}

export function usePickerSession() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return sessionState;
}

export function getActiveSession() {
  return sessionState.data;
}

// ---------------------------------------------------------------------
// Mount / unmount / sync
// ---------------------------------------------------------------------

export async function mountOverview(userId) {
  if (!userId) return;
  if (activeUserId !== userId) {
    activeUserId = userId;
    wireSSE();
  }
  // Hydrate from IndexedDB first.
  try {
    const cached = await pickerDB.getAllSessions(userId);
    if (cached.length > 0) {
      overviewState = {
        loading: false,
        data: buildOverviewFromCache(cached),
      };
      notify();
    }
  } catch {
    /* ignore */
  }
  // Always pull fresh; the cached view is "first paint" only.
  syncOverview();
}

function buildOverviewFromCache(cachedRows) {
  // Cached rows already match the API session shape minus deck-level
  // aggregates. Recompute totals/summary on the fly.
  const summary = {
    finalized: 0,
    ready_to_finalize: 0,
    judging: 0,
    not_started: 0,
  };
  const totals = { images: 0, judged: 0, inflight: 0 };
  let last_edited = null;
  for (const s of cachedRows) {
    totals.images += s.image_count || 0;
    totals.judged += s.judged_count || 0;
    if (s.in_flight) {
      totals.inflight +=
        (s.in_flight.queued || 0) +
        (s.in_flight.running || 0) +
        (s.in_flight.failed || 0);
    }
    if (s.picker_state === "finalized") {
      summary.finalized += 1;
    } else if (s.picker_state === "judging") {
      const stats = s.stats || {};
      if (
        (stats.unjudged || 0) === 0 &&
        (stats.deferred || 0) === 0 &&
        s.final_image_id &&
        (s.image_count || 0) > 0
      ) {
        summary.ready_to_finalize += 1;
      } else {
        summary.judging += 1;
      }
    } else {
      summary.not_started += 1;
    }
    if (!last_edited || (s.updated_at && s.updated_at > last_edited)) {
      last_edited = s.updated_at;
    }
  }
  return {
    deck_title: "Untitled deck",
    last_edited,
    totals,
    summary,
    sessions: cachedRows,
  };
}

export async function syncOverview() {
  if (!activeUserId) return;
  if (syncOverviewInFlight) return;
  syncOverviewInFlight = true;
  try {
    const data = await getPickerOverview();
    overviewState = { loading: false, data };
    // Persist sessions to IndexedDB.
    try {
      await pickerDB.upsertManySessions(activeUserId, data.sessions || []);
    } catch {
      /* ignore */
    }
    notify();
  } catch (err) {
    overviewState = {
      loading: false,
      data: overviewState.data,
      error: err?.message || String(err),
    };
    notify();
  } finally {
    syncOverviewInFlight = false;
  }
}

export async function openSession(sessionId) {
  if (!activeUserId || !sessionId) return;
  if (sessionState.sessionId === sessionId && sessionState.data) {
    // Already loaded; trigger a sync to refresh.
    void syncSession(sessionId);
    return;
  }
  // Flush any pending cursor on the previous session.
  await flushCursor();
  // Clear undo stack on session switch.
  undoStack = [];
  sessionState = {
    sessionId,
    loading: true,
    data: null,
    error: null,
  };
  notify();

  // Hydrate from IndexedDB first.
  try {
    const cachedSess = await pickerDB.getSession(activeUserId, sessionId);
    const cachedImgs = await pickerDB.getImagesForSession(
      activeUserId,
      sessionId
    );
    if (cachedSess && cachedImgs.length > 0) {
      sessionState = {
        sessionId,
        loading: false,
        data: {
          session: cachedSess,
          jobs: cachedSess._jobs || [],
          images: cachedImgs.sort((a, b) => {
            // job_idx then order
            if (a.job_idx !== b.job_idx) return a.job_idx - b.job_idx;
            return a.order - b.order;
          }),
        },
        error: null,
      };
      notify();
    }
  } catch {
    /* ignore */
  }

  // Always pull fresh.
  await syncSession(sessionId);
}

async function syncSession(sessionId) {
  try {
    const data = await getSessionPicker(sessionId);
    sessionState = {
      sessionId,
      loading: false,
      data,
      error: null,
    };
    // Persist to IndexedDB.
    try {
      const sessRow = {
        ...data.session,
        _jobs: data.jobs,
        image_count: data.images.length,
      };
      await pickerDB.upsertSession(activeUserId, sessRow);
      await pickerDB.deleteImagesForSession(activeUserId, sessionId);
      const imgRows = data.images.map((im) => ({
        ...im,
        session_id: sessionId,
      }));
      await pickerDB.upsertManyImages(activeUserId, imgRows);
    } catch {
      /* ignore */
    }
    notify();
  } catch (err) {
    sessionState = {
      ...sessionState,
      loading: false,
      error: err?.message || String(err),
    };
    notify();
  }
}

export function closeSession() {
  // Flush cursor synchronously via sendBeacon? We use the regular API
  // call; if the page is unloading the browser will send it best-effort.
  void flushCursor();
  sessionState = {
    sessionId: null,
    loading: false,
    data: null,
    error: null,
  };
  undoStack = [];
  notify();
}

export function unmount() {
  for (const fn of sseUnsubs) {
    try {
      fn();
    } catch {
      /* ignore */
    }
  }
  sseUnsubs.clear();
  activeUserId = null;
  overviewState = { loading: true, data: null };
  sessionState = {
    sessionId: null,
    loading: false,
    data: null,
    error: null,
  };
  undoStack = [];
  pendingWrites.clear();
  if (cursorTimer) {
    clearTimeout(cursorTimer);
    cursorTimer = null;
  }
  notify();
}

// ---------------------------------------------------------------------
// SSE wiring
// ---------------------------------------------------------------------

function wireSSE() {
  for (const fn of sseUnsubs) {
    try {
      fn();
    } catch {
      /* ignore */
    }
  }
  sseUnsubs.clear();

  sseUnsubs.add(sseStore.subscribe("image_pick_state", onImagePickState));
  sseUnsubs.add(sseStore.subscribe("session_finalized", onSessionFinalized));
  sseUnsubs.add(sseStore.subscribe("session_deleted", onSessionDeleted));
  sseUnsubs.add(sseStore.subscribe("task_created", onTaskCreated));
  sseUnsubs.add(sseStore.subscribe("job_state", onJobState));
}

function isOwnEcho(imageId) {
  // Suppress events that arrive within 2s of a local write — those are
  // the server echoing back our own change. SSE may also be delivered
  // out of order so we only suppress *very* recent ones.
  const ts = recentLocalWrites.get(imageId);
  if (!ts) return false;
  if (Date.now() - ts > 2000) {
    recentLocalWrites.delete(imageId);
    return false;
  }
  return true;
}

function onImagePickState(payload) {
  const imgId = payload?.image_id;
  if (!imgId) return;
  if (isOwnEcho(imgId)) return;
  if (sessionState.data && sessionState.sessionId === payload.session_id) {
    const idx = sessionState.data.images.findIndex(
      (im) => im.image_id === imgId
    );
    if (idx >= 0) {
      const next = [...sessionState.data.images];
      next[idx] = {
        ...next[idx],
        pick_state: payload.to,
        starred: !!payload.starred,
        pick_state_updated_at: payload.ts,
      };
      sessionState = {
        ...sessionState,
        data: {
          ...sessionState.data,
          session: {
            ...sessionState.data.session,
            picker_state:
              payload.session_picker_state ||
              sessionState.data.session.picker_state,
            final_image_id:
              payload.session_final_image_id !== undefined
                ? payload.session_final_image_id
                : sessionState.data.session.final_image_id,
          },
          images: next,
        },
      };
      notify();
    }
  }
  // Also refresh the overview lazily.
  if (overviewState.data) {
    void syncOverview();
  }
}

function onSessionFinalized(payload) {
  if (!payload?.session_id) return;
  if (
    sessionState.data &&
    sessionState.sessionId === payload.session_id
  ) {
    sessionState = {
      ...sessionState,
      data: {
        ...sessionState.data,
        session: {
          ...sessionState.data.session,
          picker_state: payload.picker_state,
          final_image_id:
            payload.final_image_id ?? sessionState.data.session.final_image_id,
          finalized_at:
            payload.picker_state === "finalized" ? payload.ts : null,
        },
      },
    };
    notify();
  }
  if (overviewState.data) void syncOverview();
}

function onSessionDeleted(payload) {
  if (!payload?.session_id) return;
  if (
    sessionState.data &&
    sessionState.sessionId === payload.session_id
  ) {
    sessionState = {
      ...sessionState,
      data: { ...sessionState.data, _deleted: true },
    };
    notify();
  }
  if (overviewState.data) void syncOverview();
}

function onTaskCreated(payload) {
  // New job in our session — append a placeholder job so the rail
  // can show it as in-flight; full row will arrive via subsequent sync.
  if (!payload || !payload.session_id) return;
  if (
    sessionState.data &&
    sessionState.sessionId === payload.session_id
  ) {
    void syncSession(payload.session_id);
  }
  if (overviewState.data) void syncOverview();
}

function onJobState(payload) {
  if (!payload) return;
  if (payload.to === "SUCCEEDED" || payload.to === "FAILED") {
    if (sessionState.sessionId) {
      // Refresh — image rows get added on SUCCEEDED.
      void syncSession(sessionState.sessionId);
    }
    if (overviewState.data) void syncOverview();
  }
}

// ---------------------------------------------------------------------
// Judgment writes (optimistic + debounced)
// ---------------------------------------------------------------------

export function judgeImage(imageId, targetState) {
  if (!sessionState.data) return;
  const data = sessionState.data;
  const idx = data.images.findIndex((im) => im.image_id === imageId);
  if (idx < 0) return;
  const img = data.images[idx];
  const prevState = img.pick_state;
  // Skip no-op writes (PRD §6.2 — same state in/out shouldn't fire a request).
  if (prevState === targetState) return;
  const prevSessionFinal = data.session.final_image_id;

  // Push undo entry first.
  undoStack.push({
    kind: prevState === "final" || targetState === "final" ? "swap_final" : "set_state",
    image_id: imageId,
    prev_state: prevState,
    new_state: targetState,
    prev_session_final_image_id: prevSessionFinal,
  });

  applyLocalState(imageId, targetState, { recompute: true });

  recentLocalWrites.set(imageId, Date.now());
  // Debounce the REST call so rapid same-image writes coalesce.
  schedulePickWrite(img.hash_id, img.order, targetState, imageId);
}

function applyLocalState(imageId, targetState, { recompute = true } = {}) {
  if (!sessionState.data) return;
  const data = sessionState.data;
  const idx = data.images.findIndex((im) => im.image_id === imageId);
  if (idx < 0) return;
  const next = [...data.images];
  // Demotion: if setting a new final and another image was final,
  // demote it to picked.
  let nextSessionFinal = data.session.final_image_id;
  if (targetState === "final") {
    next.forEach((im, i) => {
      if (im.pick_state === "final" && im.image_id !== imageId) {
        next[i] = { ...im, pick_state: "picked", starred: true };
      }
    });
    nextSessionFinal = imageId;
  }
  if (
    targetState !== "final" &&
    nextSessionFinal === imageId
  ) {
    nextSessionFinal = null;
  }
  next[idx] = {
    ...next[idx],
    pick_state: targetState,
    starred: targetState === "picked" || targetState === "final",
    pick_state_updated_at: new Date().toISOString(),
  };

  let nextPickerState = data.session.picker_state;
  if (recompute && nextPickerState !== "finalized") {
    const hasJudged = next.some(
      (im) => im.pick_state && im.pick_state !== "unjudged"
    );
    nextPickerState = hasJudged ? "judging" : "not_started";
  }

  sessionState = {
    ...sessionState,
    data: {
      ...data,
      session: {
        ...data.session,
        final_image_id: nextSessionFinal,
        picker_state: nextPickerState,
      },
      images: next,
    },
  };
  notify();
}

function schedulePickWrite(hashId, order, targetState, imageId) {
  const existing = pendingWrites.get(imageId);
  if (existing) {
    clearTimeout(existing.timer);
  }
  const timer = setTimeout(async () => {
    pendingWrites.delete(imageId);
    try {
      const resp = await apiWriteState(hashId, order, targetState);
      // Reconcile: server response is authoritative.
      if (
        sessionState.data &&
        sessionState.data.images.some((im) => im.image_id === resp.image_id)
      ) {
        const next = [...sessionState.data.images];
        const idx = next.findIndex((im) => im.image_id === resp.image_id);
        if (idx >= 0) {
          next[idx] = {
            ...next[idx],
            pick_state: resp.pick_state,
            starred: resp.starred,
            pick_state_updated_at: resp.pick_state_updated_at,
          };
        }
        // Also handle the demoted previous final, if present.
        if (resp.previous_final_image_id) {
          const pIdx = next.findIndex(
            (im) => im.image_id === resp.previous_final_image_id
          );
          if (pIdx >= 0) {
            next[pIdx] = {
              ...next[pIdx],
              pick_state: "picked",
              starred: true,
            };
          }
        }
        sessionState = {
          ...sessionState,
          data: {
            ...sessionState.data,
            session: {
              ...sessionState.data.session,
              picker_state:
                resp.session_picker_state ||
                sessionState.data.session.picker_state,
              final_image_id:
                resp.session_final_image_id !== undefined
                  ? resp.session_final_image_id
                  : sessionState.data.session.final_image_id,
            },
            images: next,
          },
        };
        notify();
      }
    } catch (err) {
      // Roll back the *most recent* undo entry for this image. If the
      // user judged the same image multiple times before the debounce
      // settled, `findIndex` would have grabbed the oldest entry —
      // wrong target. Walk the stack backwards instead.
      let lastIdx = -1;
      for (let i = undoStack.length - 1; i >= 0; i--) {
        if (undoStack[i].image_id === imageId) {
          lastIdx = i;
          break;
        }
      }
      if (lastIdx >= 0) {
        const entry = undoStack[lastIdx];
        undoStack.splice(lastIdx, 1);
        applyLocalState(imageId, entry.prev_state, { recompute: true });
      }
      // Don't rethrow — this callback is a fire-and-forget timer, so
      // throwing turns into an unhandled rejection. Log instead and
      // surface failure through the rolled-back UI state + a future
      // toast hook (the SessionView already has a toast slot).
      // eslint-disable-next-line no-console
      if (typeof console !== "undefined") {
        console.warn("picker: judgment write failed, rolled back", err);
      }
    }
  }, WRITE_DEBOUNCE_MS);
  pendingWrites.set(imageId, { hashId, order, state: targetState, timer });
}

export function undoLast() {
  if (undoStack.length === 0) return null;
  const entry = undoStack.pop();
  // Find the image and restore.
  if (!sessionState.data) return null;
  const img = sessionState.data.images.find(
    (im) => im.image_id === entry.image_id
  );
  if (!img) return null;
  applyLocalState(entry.image_id, entry.prev_state, { recompute: true });
  recentLocalWrites.set(entry.image_id, Date.now());
  // Send the actual REST call.
  apiWriteState(img.hash_id, img.order, entry.prev_state).catch(() => {
    /* roll forward on failure: best effort */
  });
  return entry;
}

export function getUndoDepth() {
  return undoStack.length;
}

// ---------------------------------------------------------------------
// Cursor patch
// ---------------------------------------------------------------------

let lastCursorImageId = null;

export function updateCursor(imageId) {
  if (!sessionState.sessionId) return;
  lastCursorImageId = imageId;
  if (sessionState.data) {
    sessionState = {
      ...sessionState,
      data: {
        ...sessionState.data,
        session: {
          ...sessionState.data.session,
          cursor_image_id: imageId,
        },
      },
    };
    notify();
  }
  if (cursorTimer) clearTimeout(cursorTimer);
  cursorTimer = setTimeout(() => {
    cursorTimer = null;
    void doCursorFlush();
  }, CURSOR_THROTTLE_MS);
}

async function doCursorFlush() {
  if (!sessionState.sessionId || !lastCursorImageId) return;
  try {
    await patchCursor(sessionState.sessionId, lastCursorImageId);
  } catch {
    /* ignore */
  }
}

export async function flushCursor() {
  if (cursorTimer) {
    clearTimeout(cursorTimer);
    cursorTimer = null;
  }
  await doCursorFlush();
}

// ---------------------------------------------------------------------
// Finalize / unfinalize
// ---------------------------------------------------------------------

export async function finalizeCurrentSession() {
  if (!sessionState.sessionId) throw new Error("no session active");
  const resp = await apiFinalize(sessionState.sessionId);
  if (sessionState.data) {
    sessionState = {
      ...sessionState,
      data: {
        ...sessionState.data,
        session: {
          ...sessionState.data.session,
          picker_state: resp.picker_state,
          finalized_at: resp.finalized_at,
          final_image_id: resp.final_image_id,
        },
      },
    };
    notify();
  }
  void syncOverview();
  return resp;
}

export async function unfinalizeCurrentSession() {
  if (!sessionState.sessionId) throw new Error("no session active");
  const resp = await apiUnfinalize(sessionState.sessionId);
  if (sessionState.data) {
    sessionState = {
      ...sessionState,
      data: {
        ...sessionState.data,
        session: {
          ...sessionState.data.session,
          picker_state: resp.picker_state,
          finalized_at: resp.finalized_at,
        },
      },
    };
    notify();
  }
  void syncOverview();
  return resp;
}

// ---------------------------------------------------------------------
// Vary seed
// ---------------------------------------------------------------------

export async function varySeedFor(imageId, opts = {}) {
  return apiVarySeed({ sourceImageId: imageId, seed: opts.seed });
}

// ---------------------------------------------------------------------
// Helpers used by views
// ---------------------------------------------------------------------

export function computeSessionStats(images) {
  const out = {
    total: images.length,
    final: 0,
    picked: 0,
    discarded: 0,
    deferred: 0,
    unjudged: 0,
  };
  for (const im of images) {
    const s = im.pick_state || "unjudged";
    if (s in out) out[s] += 1;
    else out.unjudged += 1;
  }
  return out;
}

export function computeReadyToFinalize(session, images) {
  if (!session) return false;
  if (session.picker_state === "finalized") return false;
  if (!session.final_image_id) return false;
  if (images.length === 0) return false;
  return images.every(
    (im) => im.pick_state !== "unjudged" && im.pick_state !== "deferred"
  );
}

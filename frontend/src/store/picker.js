// Picker store — bridges IndexedDB + REST + SSE.
//
// Lifecycle:
//   - mountOverview(userId) hydrates the deck-overview cache.
//   - openSession(userId, sessionId) hydrates one session's images.
//   - closeSession() flushes pending writes (cursor) and detaches.
//   - unmount() detaches SSE subscriptions.
//
// State shape:
//   sessions: Map<id, DeckSessionSummary>
//   sessionData: Map<id, {session, jobs, images}>     (per-session bundle)
//   undoStack: Array<{kind, ...}>                     (per active session)
//
// Architecture mirrors store/archive.js:
//   - in-memory map + subscribe() / notify()
//   - debounced write to IndexedDB
//   - debounced write to REST
//   - SSE subscriptions wired in attach/detach helpers

import { useEffect, useState } from "react";

import {
  pickImage as apiPick,
  discardImage as apiDiscard,
  finalImage as apiFinal,
  deferImage as apiDefer,
  unjudgeImage as apiUnjudge,
  finalizeSession as apiFinalize,
  unfinalizeSession as apiUnfinalize,
  patchSessionCursor as apiPatchCursor,
  getPickerOverview,
  getSessionPicker,
} from "../api/picker.js";
import { listSessions, createSession as apiCreateSession } from "../api/sessions.js";
import * as pickerDB from "../storage/pickerDB.js";
import * as sseStore from "./sse.js";

// ---------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------

let activeUserId = null;
const overviewSessions = new Map(); // session_id -> DeckSessionSummary
const sessionBundles = new Map(); // session_id -> {session, jobs, images: image_id -> image}
const subscribers = new Set();
const sseUnsubs = new Set();

let activeSessionId = null;
let undoStack = [];
let pendingCursorSessionId = null;
let pendingCursorImageId = null;
let cursorTimer = null;

const PICK_STATES = ["unjudged", "picked", "discarded", "final", "deferred"];

const STATE = {
  UNJUDGED: "unjudged",
  PICKED: "picked",
  DISCARDED: "discarded",
  FINAL: "final",
  DEFERRED: "deferred",
};

export { STATE };

// ---------------------------------------------------------------------
// Subscribe API
// ---------------------------------------------------------------------

function notify() {
  for (const fn of subscribers) {
    try {
      fn();
    } catch {
      /* one bad listener should not block others */
    }
  }
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function usePickerSnapshot() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return getSnapshot();
}

export function getSnapshot() {
  return {
    overview: Array.from(overviewSessions.values()).sort((a, b) =>
      (b.updated_at || "").localeCompare(a.updated_at || "")
    ),
    activeSessionId,
    bundle: activeSessionId ? sessionBundles.get(activeSessionId) || null : null,
    undoStack,
  };
}

// ---------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------

export async function mountOverview(userId) {
  if (!userId) {
    unmount();
    return;
  }
  if (activeUserId !== userId) {
    overviewSessions.clear();
    sessionBundles.clear();
    activeUserId = userId;
  }

  // 1. Hydrate from IndexedDB.
  try {
    const stored = await pickerDB.getAllSessions(userId);
    for (const s of stored) overviewSessions.set(s.id, s);
    notify();
  } catch (e) {
    console.warn("picker: IndexedDB hydrate failed", e);
  }

  // 2. Wire SSE before the REST call so events arriving in flight are
  //    captured and reconciled with the response.
  attachSse();

  // 3. Background fetch.
  void refreshOverview();
}

export async function refreshOverview() {
  if (!activeUserId) return;
  try {
    const data = await getPickerOverview();
    const fresh = new Map();
    for (const s of data.sessions || []) fresh.set(s.id, s);
    // Keep deleted sessions out of the cache.
    overviewSessions.clear();
    for (const [id, s] of fresh) overviewSessions.set(id, s);
    notify();
    void pickerDB.upsertSessionsMany(activeUserId, Array.from(fresh.values()));
  } catch (e) {
    console.warn("picker: refreshOverview failed", e);
  }
}

export function unmount() {
  detachSse();
  flushCursor();
  // Keep the in-memory cache so a quick remount renders instantly.
}

// ---------------------------------------------------------------------
// Open / close one session
// ---------------------------------------------------------------------

export async function openSession(userId, sessionId) {
  if (!userId || !sessionId) return null;
  if (activeUserId !== userId) {
    overviewSessions.clear();
    sessionBundles.clear();
    activeUserId = userId;
  }
  if (activeSessionId !== sessionId) {
    flushCursor();
    activeSessionId = sessionId;
    undoStack = [];
  }
  attachSse();

  // Hydrate from IndexedDB.
  try {
    const cachedImages = await pickerDB.getImagesForSession(userId, sessionId);
    if (cachedImages.length > 0) {
      const localBundle = sessionBundles.get(sessionId) || {
        session: overviewSessions.get(sessionId) || { id: sessionId },
        jobs: [],
        images: new Map(),
      };
      for (const im of cachedImages) localBundle.images.set(im.image_id, im);
      sessionBundles.set(sessionId, localBundle);
      notify();
    }
  } catch (e) {
    console.warn("picker: hydrate session images failed", e);
  }

  // Background REST fetch.
  await refreshSession(sessionId);
  return sessionBundles.get(sessionId);
}

export async function refreshSession(sessionId) {
  if (!activeUserId) return null;
  try {
    const data = await getSessionPicker(sessionId);
    const imageMap = new Map();
    for (const im of data.images || []) imageMap.set(im.image_id, im);
    const bundle = {
      session: data.session,
      jobs: data.jobs || [],
      images: imageMap,
    };
    sessionBundles.set(sessionId, bundle);
    // Mirror the session-meta into overview so deck cards stay fresh.
    const overviewEntry = overviewSessions.get(sessionId);
    if (overviewEntry) {
      overviewSessions.set(sessionId, {
        ...overviewEntry,
        ...sessionMetaToOverview(data.session, bundle),
      });
    }
    notify();
    void pickerDB.upsertImagesMany(
      activeUserId,
      (data.images || []).map((im) => ({ ...im, session_id: sessionId }))
    );
    if (data.session) {
      void pickerDB.upsertSession(activeUserId, {
        ...overviewSessions.get(sessionId),
        id: data.session.id,
        name: data.session.name,
        picker_state: data.session.picker_state,
        final_image_id: data.session.final_image_id,
        cursor_image_id: data.session.cursor_image_id,
        finalized_at: data.session.finalized_at,
        updated_at: data.session.updated_at,
        created_at: data.session.created_at,
      });
    }
    return bundle;
  } catch (e) {
    console.warn("picker: refreshSession failed", e);
    return null;
  }
}

function sessionMetaToOverview(meta, bundle) {
  return {
    picker_state: meta.picker_state,
    final_image_id: meta.final_image_id,
    finalized_at: meta.finalized_at,
    updated_at: meta.updated_at,
    image_count: bundle.images.size,
  };
}

export function closeSession() {
  flushCursor();
  activeSessionId = null;
  undoStack = [];
  notify();
}

// ---------------------------------------------------------------------
// Per-image judgments (optimistic write, lazy persist)
// ---------------------------------------------------------------------

function _bundleFor(sessionId) {
  return sessionBundles.get(sessionId);
}

function _activeBundle() {
  if (!activeSessionId) return null;
  return _bundleFor(activeSessionId);
}

function _applyImagePatch(sessionId, imageId, patch) {
  const bundle = _bundleFor(sessionId);
  if (!bundle) return;
  const cur = bundle.images.get(imageId);
  if (!cur) return;
  const next = { ...cur, ...patch };
  bundle.images.set(imageId, next);
  if (activeUserId) {
    void pickerDB.patchImage(activeUserId, imageId, patch);
  }
}

function _applySessionPatch(sessionId, patch) {
  const bundle = _bundleFor(sessionId);
  if (!bundle) return;
  bundle.session = { ...bundle.session, ...patch };
  const overview = overviewSessions.get(sessionId);
  if (overview) {
    overviewSessions.set(sessionId, { ...overview, ...patch });
  }
  if (activeUserId) {
    void pickerDB.upsertSession(activeUserId, {
      ...(overviewSessions.get(sessionId) || {}),
      id: sessionId,
      ...patch,
    });
  }
}

function _localTransition(sessionId, image, newState) {
  // The store mirrors the backend state machine so the UI feedback is
  // immediate. The actual API call is fire-and-forget below; if it
  // fails we re-pull the session bundle to converge on server truth.
  const bundle = _bundleFor(sessionId);
  if (!bundle) return null;
  const events = [];
  // If promoting to FINAL, demote any existing FINAL in the same session.
  if (newState === STATE.FINAL) {
    for (const [otherId, otherImg] of bundle.images) {
      if (otherId === image.image_id) continue;
      if (otherImg.pick_state === STATE.FINAL) {
        const prev = otherImg.pick_state;
        events.push({ image_id: otherId, prev, next: STATE.PICKED });
        bundle.images.set(otherId, {
          ...otherImg,
          pick_state: STATE.PICKED,
          starred: true,
          pick_state_updated_at: new Date().toISOString(),
        });
      }
    }
  }
  const prev = image.pick_state || STATE.UNJUDGED;
  const newImg = {
    ...image,
    pick_state: newState,
    starred: newState === STATE.PICKED || newState === STATE.FINAL,
    pick_state_updated_at: new Date().toISOString(),
  };
  bundle.images.set(image.image_id, newImg);
  events.push({ image_id: image.image_id, prev, next: newState });

  // Update session.final_image_id locally.
  if (newState === STATE.FINAL) {
    bundle.session = { ...bundle.session, final_image_id: image.image_id };
  } else if (
    bundle.session.final_image_id === image.image_id &&
    newState !== STATE.FINAL
  ) {
    bundle.session = { ...bundle.session, final_image_id: null };
  }

  // Recompute picker_state for the local cache.
  bundle.session = {
    ...bundle.session,
    picker_state: _computePickerState(bundle),
  };

  // Persist the patches to IndexedDB.
  if (activeUserId) {
    for (const ev of events) {
      const im = bundle.images.get(ev.image_id);
      void pickerDB.patchImage(activeUserId, ev.image_id, {
        pick_state: im.pick_state,
        starred: im.starred,
        pick_state_updated_at: im.pick_state_updated_at,
        session_id: sessionId,
      });
    }
  }

  return events;
}

function _computePickerState(bundle) {
  // ready_to_finalize is a frontend-computed view; we only track the
  // three persisted states here. Don't override finalized — only the
  // explicit unfinalize action drops out of it.
  if (bundle.session.picker_state === "finalized") return "finalized";
  let total = 0;
  let unjudgedOrDeferred = 0;
  let judged = 0;
  for (const im of bundle.images.values()) {
    total += 1;
    if (im.pick_state === STATE.UNJUDGED || im.pick_state === STATE.DEFERRED) {
      unjudgedOrDeferred += 1;
    } else {
      judged += 1;
    }
  }
  if (total === 0) return "not_started";
  if (judged > 0 || unjudgedOrDeferred < total) return "judging";
  return "not_started";
}

export function isReadyToFinalize(bundle) {
  if (!bundle) return false;
  if (bundle.session.picker_state === "finalized") return false;
  if (!bundle.session.final_image_id) return false;
  for (const im of bundle.images.values()) {
    if (im.pick_state === STATE.UNJUDGED || im.pick_state === STATE.DEFERRED) {
      return false;
    }
  }
  return bundle.images.size > 0;
}

const API_FOR_STATE = {
  picked: apiPick,
  discarded: apiDiscard,
  final: apiFinal,
  deferred: apiDefer,
  unjudged: apiUnjudge,
};

export async function judgeImage(image, newState) {
  if (!image || !activeSessionId) return null;
  if (!PICK_STATES.includes(newState)) return null;
  const sessionId = activeSessionId;
  const prev = image.pick_state;
  const events = _localTransition(sessionId, image, newState);
  notify();
  // Push undo record.
  undoStack.push({
    kind: "transition",
    image_id: image.image_id,
    prev,
    next: newState,
    sibling_changes: events.filter((e) => e.image_id !== image.image_id),
    session_id: sessionId,
  });

  try {
    const fn = API_FOR_STATE[newState];
    const resp = await fn(image.hash_id, image.order);
    // Reconcile with server response (covers race conditions).
    if (resp) {
      _applyImagePatch(sessionId, image.image_id, {
        pick_state: resp.pick_state,
        starred: resp.starred,
        pick_state_updated_at: resp.pick_state_updated_at,
      });
      _applySessionPatch(sessionId, {
        picker_state: resp.session_picker_state,
        final_image_id: resp.session_final_image_id,
      });
      notify();
    }
    return resp;
  } catch (e) {
    console.warn("picker: judge failed; rolling back", e);
    // Roll back optimistic write by re-pulling the bundle.
    void refreshSession(sessionId);
    throw e;
  }
}

export async function undoLast() {
  const entry = undoStack.pop();
  if (!entry) return false;
  notify();
  const bundle = _bundleFor(entry.session_id);
  if (!bundle) return false;

  // Restore sibling state (e.g. previous final) first.
  for (const sib of entry.sibling_changes || []) {
    const im = bundle.images.get(sib.image_id);
    if (!im) continue;
    const prevImg = { ...im, pick_state: sib.prev, starred: sib.prev === STATE.PICKED || sib.prev === STATE.FINAL };
    bundle.images.set(sib.image_id, prevImg);
    if (activeUserId) {
      void pickerDB.patchImage(activeUserId, sib.image_id, {
        pick_state: prevImg.pick_state,
        starred: prevImg.starred,
      });
    }
  }
  const targetImg = bundle.images.get(entry.image_id);
  if (targetImg) {
    const restored = {
      ...targetImg,
      pick_state: entry.prev,
      starred: entry.prev === STATE.PICKED || entry.prev === STATE.FINAL,
    };
    bundle.images.set(entry.image_id, restored);
    if (activeUserId) {
      void pickerDB.patchImage(activeUserId, entry.image_id, {
        pick_state: restored.pick_state,
        starred: restored.starred,
      });
    }
    bundle.session = {
      ...bundle.session,
      picker_state: _computePickerState(bundle),
    };
    notify();
    try {
      const fn = API_FOR_STATE[entry.prev] || apiUnjudge;
      await fn(restored.hash_id, restored.order);
      // For each demoted sibling, re-promote it back to its prev state.
      for (const sib of entry.sibling_changes || []) {
        const sibImg = bundle.images.get(sib.image_id);
        if (sibImg) {
          const sibFn = API_FOR_STATE[sib.prev] || apiUnjudge;
          await sibFn(sibImg.hash_id, sibImg.order);
        }
      }
    } catch (e) {
      console.warn("picker: undo API failed; refreshing", e);
      void refreshSession(entry.session_id);
    }
  }
  return true;
}

export function undoStackSize() {
  return undoStack.length;
}

// ---------------------------------------------------------------------
// Cursor (throttled)
// ---------------------------------------------------------------------

export function setCursor(sessionId, imageId) {
  pendingCursorSessionId = sessionId;
  pendingCursorImageId = imageId;
  // Mirror locally so reload picks it up.
  const overview = overviewSessions.get(sessionId);
  if (overview) {
    overviewSessions.set(sessionId, { ...overview, cursor_image_id: imageId });
  }
  const bundle = _bundleFor(sessionId);
  if (bundle) {
    bundle.session = { ...bundle.session, cursor_image_id: imageId };
  }
  if (cursorTimer) {
    clearTimeout(cursorTimer);
  }
  cursorTimer = setTimeout(flushCursor, 1000);
}

function flushCursor() {
  if (!pendingCursorSessionId) return;
  const sid = pendingCursorSessionId;
  const cid = pendingCursorImageId;
  pendingCursorSessionId = null;
  pendingCursorImageId = null;
  if (cursorTimer) {
    clearTimeout(cursorTimer);
    cursorTimer = null;
  }
  apiPatchCursor(sid, cid).catch((e) =>
    console.warn("picker: cursor patch failed", e)
  );
}

// ---------------------------------------------------------------------
// Finalize / unfinalize
// ---------------------------------------------------------------------

export async function finalizeSession(sessionId) {
  try {
    const resp = await apiFinalize(sessionId);
    _applySessionPatch(sessionId, {
      picker_state: resp.picker_state,
      finalized_at: resp.finalized_at,
      final_image_id: resp.final_image_id,
    });
    notify();
    return resp;
  } catch (e) {
    console.warn("picker: finalize failed", e);
    throw e;
  }
}

export async function unfinalizeSession(sessionId) {
  try {
    const resp = await apiUnfinalize(sessionId);
    _applySessionPatch(sessionId, {
      picker_state: resp.picker_state,
      finalized_at: resp.finalized_at,
      final_image_id: resp.final_image_id,
    });
    notify();
    return resp;
  } catch (e) {
    console.warn("picker: unfinalize failed", e);
    throw e;
  }
}

export async function createNewSession(name) {
  const created = await apiCreateSession(name);
  // Mirror into overview cache as a brand-new not_started session.
  const summary = {
    id: created.id,
    name: created.name,
    picker_state: "not_started",
    image_count: 0,
    judged_count: 0,
    stats: { final: 0, picked: 0, discarded: 0, deferred: 0, unjudged: 0 },
    in_flight: { queued: 0, running: 0, failed: 0 },
    final_image_id: null,
    final_thumb_url: null,
    last_prompt: null,
    updated_at: created.updated_at,
    created_at: created.created_at,
    finalized_at: null,
  };
  overviewSessions.set(created.id, summary);
  notify();
  if (activeUserId) {
    void pickerDB.upsertSession(activeUserId, summary);
  }
  return created;
}

// ---------------------------------------------------------------------
// SSE wiring
// ---------------------------------------------------------------------

function attachSse() {
  if (sseUnsubs.size > 0) return;
  sseUnsubs.add(sseStore.subscribe("image_pick_state", onImagePickState));
  sseUnsubs.add(sseStore.subscribe("session_finalized", onSessionFinalized));
  sseUnsubs.add(sseStore.subscribe("task_created", onTaskCreated));
  sseUnsubs.add(sseStore.subscribe("job_state", onJobState));
}

function detachSse() {
  for (const off of sseUnsubs) {
    try {
      off();
    } catch {
      /* ignore */
    }
  }
  sseUnsubs.clear();
}

function onImagePickState(payload) {
  if (!payload?.image_id) return;
  const sid = payload.session_id;
  if (!sid) return;
  // If the affected session is in our cache, patch it.
  const bundle = _bundleFor(sid);
  if (bundle && bundle.images.has(payload.image_id)) {
    _applyImagePatch(sid, payload.image_id, {
      pick_state: payload.to,
      starred: !!payload.starred,
      pick_state_updated_at: payload.ts,
    });
    _applySessionPatch(sid, {
      picker_state: payload.session_picker_state,
      final_image_id: payload.session_final_image_id,
    });
    notify();
  } else {
    // Just bump the overview entry's stats by re-fetching the overview.
    void refreshOverview();
  }
}

function onSessionFinalized(payload) {
  if (!payload?.session_id) return;
  _applySessionPatch(payload.session_id, {
    picker_state: payload.picker_state,
    final_image_id: payload.final_image_id,
    finalized_at: payload.ts,
  });
  notify();
}

function onTaskCreated(payload) {
  if (!payload?.session_id) return;
  if (!_bundleFor(payload.session_id)) return;
  // Re-pull the session bundle so the new in-flight job appears.
  void refreshSession(payload.session_id);
}

function onJobState(payload) {
  if (!payload) return;
  // For SUCCEEDED → re-pull so new images are added.
  // For QUEUED / RUNNING / FAILED → re-pull so the in-flight tray
  // updates its progress badges.
  // (Fine-grained patches would be possible but the bundle is small.)
  if (!payload.session_id) return;
  if (!_bundleFor(payload.session_id)) return;
  void refreshSession(payload.session_id);
}

// ---------------------------------------------------------------------
// Lifecycle hook for window unload
// ---------------------------------------------------------------------

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => {
    flushCursor();
  });
}
